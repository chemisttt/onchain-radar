import asyncio
import json
import logging
import time
import aiohttp

from db import get_db
from services import dexscreener, etherscan, helius, geckoterminal
from routers.feed import manager

log = logging.getLogger("feed_engine")

# Blacklist known stables, wraps, and LSDs — these pollute the feed with scam duplicates
BLACKLIST_SYMBOLS: set[str] = {
    "USDT", "USDC", "USDC.E", "DAI", "BUSD", "TUSD", "FRAX", "USDP", "GUSD", "LUSD",
    "WETH", "WBNB", "WMATIC", "WBTC", "WAVAX", "WFTM", "WSOL",
    "STETH", "CBETH", "RETH", "WSTETH", "METH", "EZETH",
    "USDD", "PYUSD", "FDUSD", "EUSD", "CUSD", "SUSD",
}

# Minimum liquidity thresholds
MIN_LIQ_NEW_POOLS = 5_000      # $5k for new pools
MIN_LIQ_TRENDING = 10_000      # $10k for trending pools

# TTL-based dedup (1 hour)
_DEDUP_TTL = 3600
_seen_keys: dict[str, float] = {}  # key → timestamp
_task: asyncio.Task | None = None

# Rolling volume averages for spike detection
_volume_history: dict[str, list[float]] = {}

# Rotate GeckoTerminal networks to avoid rate limits (30 req/min)
_gecko_network_idx = 0


def _dedup_key(event_type: str, chain: str, address: str) -> str:
    return f"{event_type}:{chain}:{address}"


def _is_seen(key: str) -> bool:
    """Check if key was seen within TTL. Also marks it as seen."""
    now = time.monotonic()
    if key in _seen_keys and now - _seen_keys[key] < _DEDUP_TTL:
        return True
    _seen_keys[key] = now
    return False


def _cleanup_seen():
    """Remove expired entries from dedup dict."""
    now = time.monotonic()
    expired = [k for k, ts in _seen_keys.items() if now - ts >= _DEDUP_TTL]
    for k in expired:
        del _seen_keys[k]


_broadcast_queue: list[dict] = []
_last_flush_time: float = 0
_FLUSH_INTERVAL = 2.0  # Batch WS broadcasts every 2 seconds


async def _save_and_broadcast(event: dict):
    db = get_db()
    await db.execute(
        """INSERT INTO feed_events (event_type, chain, token_address, pair_address, token_symbol, details, severity)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            event["event_type"],
            event["chain"],
            event.get("token_address"),
            event.get("pair_address"),
            event.get("token_symbol"),
            json.dumps(event.get("details", {})),
            event.get("severity", "info"),
        ),
    )
    await db.commit()

    row = await db.execute_fetchall("SELECT * FROM feed_events ORDER BY id DESC LIMIT 1")
    if row:
        saved = dict(row[0])
        saved["details"] = json.loads(saved.get("details") or "{}")
        _broadcast_queue.append(saved)
    else:
        _broadcast_queue.append(event)

    # Flush if enough time passed
    await _maybe_flush_broadcasts()


async def _maybe_flush_broadcasts():
    """Flush queued broadcasts in batch to avoid WS flood."""
    global _last_flush_time
    now = time.monotonic()
    if now - _last_flush_time < _FLUSH_INTERVAL and len(_broadcast_queue) < 20:
        return
    if not _broadcast_queue:
        return

    _last_flush_time = now
    events = _broadcast_queue[:]
    _broadcast_queue.clear()

    # Send batch as single message
    await manager.broadcast({"type": "feed_batch", "data": events})


async def _prune_feed_events():
    """Keep only the last 2000 feed events in DB."""
    db = get_db()
    await db.execute(
        """DELETE FROM feed_events WHERE id NOT IN
           (SELECT id FROM feed_events ORDER BY id DESC LIMIT 2000)"""
    )
    await db.commit()


def _detect_protocol(chain: str, address: str) -> str:
    """Detect protocol/launchpad from chain + address pattern."""
    if chain == "solana":
        if address.endswith("pump"):
            return "pump.fun"
        if address.endswith("moon"):
            return "moonshot"
    return ""


# DEX ID → display name
_DEX_LABELS: dict[str, str] = {
    "raydium": "Raydium", "raydium-clmm": "Raydium",
    "orca-whirlpools": "Orca", "orca": "Orca",
    "uniswap_v3": "Uniswap V3", "uniswap_v2": "Uniswap V2",
    "pancakeswap_v3": "PancakeSwap", "pancakeswap_v2": "PancakeSwap",
    "aerodrome-slipstream": "Aerodrome", "aerodrome-v2": "Aerodrome",
    "sushiswap-v3": "SushiSwap", "sushiswap": "SushiSwap",
    "camelot-v3": "Camelot", "velodrome-v3": "Velodrome",
    "quickswap-v3": "QuickSwap", "trader-joe-v2.1": "TraderJoe",
    "meteora-dlmm": "Meteora", "meteora": "Meteora",
    "pumpfun": "pump.fun", "moonshot": "moonshot",
}


def _dex_label(dex_id: str) -> str:
    if not dex_id:
        return ""
    return _DEX_LABELS.get(dex_id, dex_id.replace("-", " ").replace("_", " ").title())


async def _poll_dexscreener(session: aiohttp.ClientSession):
    """DexScreener token profiles — mostly Solana boosted tokens."""
    try:
        profiles = await dexscreener.get_token_profiles(session)
        for token in profiles[:20]:
            chain = token.get("chainId", "unknown")
            address = token.get("tokenAddress", "")
            if not address:
                continue

            key = _dedup_key("NEW_PAIR", chain, address)
            if _is_seen(key):
                continue

            # Try to extract $SYMBOL from description
            desc = token.get("description", "")
            symbol = address[:6] + "..."
            if "$" in desc:
                for word in desc.split():
                    if word.startswith("$") and len(word) > 1:
                        symbol = word.strip("$.,!?()").upper()
                        break

            if symbol.upper() in BLACKLIST_SYMBOLS:
                continue

            protocol = _detect_protocol(chain, address)

            event = {
                "event_type": "NEW_PAIR",
                "chain": chain,
                "token_address": address,
                "pair_address": None,
                "token_symbol": symbol,
                "severity": "info",
                "details": {
                    "source": "dexscreener",
                    "url": token.get("url", ""),
                    "icon": token.get("icon", ""),
                    "description": desc[:200],
                    "protocol": protocol,
                },
            }
            await _save_and_broadcast(event)
            log.info(f"NEW_PAIR [DS]: {chain}/{symbol}" + (f" ({protocol})" if protocol else ""))
    except Exception as e:
        log.error(f"DexScreener poll error: {e}")


async def _poll_geckoterminal(session: aiohttp.ClientSession):
    """GeckoTerminal trending + new pools — rotates 2 networks per tick to stay under rate limit."""
    global _gecko_network_idx
    networks = geckoterminal.NETWORKS

    # Poll 2 networks per tick (rotating)
    for _ in range(2):
        network = networks[_gecko_network_idx % len(networks)]
        _gecko_network_idx += 1
        chain = geckoterminal.NETWORK_MAP.get(network, network)

        try:
            # New pools
            pools = await geckoterminal.get_new_pools(session, network)
            for pool in pools[:3]:
                parsed = geckoterminal.parse_pool(pool, network)
                if not parsed or not parsed["pair_address"]:
                    continue

                if parsed["token_symbol"].upper() in BLACKLIST_SYMBOLS:
                    continue

                liq = float(parsed.get("liquidity_usd") or 0)
                if liq < MIN_LIQ_NEW_POOLS:
                    continue

                key = _dedup_key("NEW_PAIR", chain, parsed["pair_address"])
                if _is_seen(key):
                    continue

                dex = _dex_label(parsed.get("dex", ""))
                protocol = dex or _detect_protocol(chain, parsed.get("token_address") or "")

                event = {
                    "event_type": "NEW_PAIR",
                    "chain": chain,
                    "token_address": parsed.get("token_address"),
                    "pair_address": parsed["pair_address"],
                    "token_symbol": parsed["token_symbol"],
                    "severity": "info",
                    "details": {
                        "source": "geckoterminal",
                        "name": parsed["name"],
                        "price_usd": parsed["price_usd"],
                        "liquidity_usd": parsed["liquidity_usd"],
                        "pool_created_at": parsed["pool_created_at"],
                        "protocol": protocol,
                    },
                }
                await _save_and_broadcast(event)
                log.info(f"NEW_PAIR [GT]: {chain}/{parsed['token_symbol']}" + (f" ({protocol})" if protocol else ""))

            # Trending pools — detect pumps/volume anomalies
            trending = await geckoterminal.get_trending_pools(session, network)
            for pool in trending[:5]:
                parsed = geckoterminal.parse_pool(pool, network)
                if not parsed or not parsed["pair_address"]:
                    continue

                if parsed["token_symbol"].upper() in BLACKLIST_SYMBOLS:
                    continue

                liq = float(parsed.get("liquidity_usd") or 0)
                if liq < MIN_LIQ_TRENDING:
                    continue

                pc = parsed.get("price_change", {})
                change_h1 = float(pc.get("h1") or 0)

                # Price pump/dump > 20% in 1h
                if abs(change_h1) > 20:
                    event_type = "PRICE_PUMP" if change_h1 > 0 else "PRICE_DUMP"
                    key = _dedup_key(event_type, chain, parsed["pair_address"])
                    if not _is_seen(key):
                        p_dex = _dex_label(parsed.get("dex", ""))
                        p_proto = p_dex or _detect_protocol(chain, parsed.get("token_address") or "")
                        await _save_and_broadcast({
                            "event_type": event_type,
                            "chain": chain,
                            "token_address": parsed.get("token_address"),
                            "pair_address": parsed["pair_address"],
                            "token_symbol": parsed["token_symbol"],
                            "severity": "warning" if abs(change_h1) < 50 else "critical",
                            "details": {
                                "source": "geckoterminal",
                                "price_change_1h": change_h1,
                                "price_usd": parsed["price_usd"],
                                "liquidity_usd": parsed["liquidity_usd"],
                                "name": parsed["name"],
                                "protocol": p_proto,
                            },
                        })
                        log.info(f"{event_type} [GT]: {chain}/{parsed['token_symbol']} {change_h1:+.1f}%")

        except Exception as e:
            log.error(f"GeckoTerminal poll {network} error: {e}")


async def _poll_whale_transfers(session: aiohttp.ClientSession):
    """Poll Etherscan + Helius for whale transfers."""
    try:
        evm_transfers = await etherscan.get_large_transfers(session, chain_id=1)
        log.info(f"Whale poll: {len(evm_transfers)} EVM transfers found")
        new_count = 0
        for event in evm_transfers[:10]:
            tx_hash = event.get("details", {}).get("tx_hash") or event.get("details", {}).get("block", "")
            if not tx_hash:
                continue
            key = _dedup_key("WHALE_TRANSFER", "ethereum", tx_hash)
            if _is_seen(key):
                continue
            await _save_and_broadcast(event)
            new_count += 1
            d = event["details"]
            log.info(f"WHALE [ETH]: {d.get('value_eth', 0)} ETH | {d.get('from_label', '?')} → {d.get('to_label', '?')}")
        if new_count == 0 and evm_transfers:
            log.debug("All EVM whale transfers were deduped")
    except Exception as e:
        log.error(f"Etherscan whale poll error: {e}", exc_info=True)

    try:
        sol_transfers = await helius.get_recent_transfers(session)
        log.info(f"Whale poll: {len(sol_transfers)} SOL transfers found")
        for event in sol_transfers[:10]:
            tx_sig = event.get("details", {}).get("tx_sig", "")
            if not tx_sig:
                continue
            key = _dedup_key("WHALE_TRANSFER", "solana", tx_sig)
            if _is_seen(key):
                continue
            await _save_and_broadcast(event)
            d = event["details"]
            log.info(f"WHALE [SOL]: {d.get('amount_sol', 0)} SOL | {d.get('from_label', '?')} → {d.get('to_label', '?')}")
    except Exception as e:
        log.error(f"Helius whale poll error: {e}", exc_info=True)


async def _poll_loop():
    log.info("Feed engine started")
    tick = 0
    async with aiohttp.ClientSession() as session:
        while True:
            # DexScreener profiles every 10s
            await _poll_dexscreener(session)

            # GeckoTerminal new+trending every 30s (2 networks per tick, rotated)
            if tick % 3 == 0:
                await _poll_geckoterminal(session)

            # Whale transfers every 60s
            if tick % 6 == 0:
                await _poll_whale_transfers(session)

            # Heartbeat
            await manager.broadcast({"type": "heartbeat", "timestamp": int(asyncio.get_event_loop().time())})

            # Flush any pending WS broadcasts
            await _maybe_flush_broadcasts()

            tick += 1
            # Cleanup expired dedup entries every ~5 minutes
            if tick % 30 == 0:
                _cleanup_seen()

            # Prune feed_events table every ~10 minutes
            if tick % 60 == 0:
                await _prune_feed_events()

            await asyncio.sleep(10)


def start():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll_loop())
        log.info("Feed engine task created")


def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
