import asyncio
import logging
import time
import aiohttp

log = logging.getLogger("protocol_tracker")

_task: asyncio.Task | None = None

TVL_CHANGE_THRESHOLD = 15  # 15% change triggers alert (DefiLlama returns as percentage)
MIN_TVL = 10_000_000  # $10M minimum TVL to report
HIGH_APY_THRESHOLD = 100  # 100% APY for yield alerts
MIN_YIELD_TVL = 500_000  # $500k minimum TVL for yield alerts
POLL_INTERVAL = 300  # 5 minutes

# Dedup: slug → last_alert_timestamp (prevent re-alerting same protocol within 6h)
_DEDUP_TTL = 6 * 3600
_seen_protocols: dict[str, float] = {}
_seen_pools: dict[str, float] = {}


def _is_seen(cache: dict[str, float], key: str) -> bool:
    now = time.monotonic()
    if key in cache and now - cache[key] < _DEDUP_TTL:
        return True
    cache[key] = now
    return False


def _cleanup_cache(cache: dict[str, float]):
    now = time.monotonic()
    expired = [k for k, ts in cache.items() if now - ts >= _DEDUP_TTL]
    for k in expired:
        del cache[k]


async def _fetch_protocols(session: aiohttp.ClientSession) -> list[dict]:
    try:
        async with session.get(
            "https://api.llama.fi/protocols",
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                log.warning(f"DefiLlama protocols returned {resp.status}")
                return []
            return await resp.json()
    except Exception as e:
        log.warning(f"DefiLlama protocols fetch error: {e}")
        return []


async def _fetch_yield_pools(session: aiohttp.ClientSession) -> list[dict]:
    try:
        async with session.get(
            "https://yields.llama.fi/pools",
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                log.warning(f"DefiLlama yields returned {resp.status}")
                return []
            data = await resp.json()
            return data.get("data", [])
    except Exception as e:
        log.warning(f"DefiLlama yields fetch error: {e}")
        return []


async def _check_tvl_spikes(protocols: list[dict]):
    from services.feed_engine import _save_and_broadcast

    count = 0
    for proto in protocols:
        name = proto.get("name", "")
        slug = proto.get("slug", "")
        tvl = float(proto.get("tvl", 0) or 0)
        change_1d = float(proto.get("change_1d", 0) or 0)

        if tvl < MIN_TVL:
            continue
        if abs(change_1d) < TVL_CHANGE_THRESHOLD:
            continue
        if _is_seen(_seen_protocols, slug):
            continue

        # Limit to 10 events per poll
        count += 1
        if count > 10:
            break

        chains = proto.get("chains", [])
        chain = chains[0].lower() if chains else proto.get("chain", "unknown")

        event = {
            "event_type": "PROTOCOL_TVL_SPIKE",
            "chain": chain,
            "token_address": None,
            "pair_address": None,
            "token_symbol": name[:20],
            "severity": "warning" if abs(change_1d) >= 25 else "info",
            "details": {
                "protocol": name,
                "slug": slug,
                "tvl": tvl,
                "change_1d_pct": change_1d,
                "direction": "up" if change_1d > 0 else "down",
                "category": proto.get("category", ""),
                "chains": chains[:5],
                "url": f"https://defillama.com/protocol/{slug}",
            },
        }
        await _save_and_broadcast(event)
        log.info(f"TVL_SPIKE: {name} {change_1d:+.1f}% (${tvl:,.0f})")


async def _check_yield_opportunities(pools: list[dict]):
    from services.feed_engine import _save_and_broadcast

    count = 0
    for pool in pools:
        apy = float(pool.get("apy", 0) or 0)
        tvl = float(pool.get("tvlUsd", 0) or 0)

        if apy < HIGH_APY_THRESHOLD or tvl < MIN_YIELD_TVL:
            continue

        # Must have significant APY increase recently
        apy_change_1d = float(pool.get("apyPct1D", 0) or 0)
        if apy_change_1d < 100:  # APY doubled in last day
            continue

        pool_id = pool.get("pool", "")
        if _is_seen(_seen_pools, pool_id):
            continue

        count += 1
        if count > 5:
            break

        event = {
            "event_type": "PROTOCOL_YIELD_NEW",
            "chain": pool.get("chain", "unknown").lower(),
            "token_address": None,
            "pair_address": None,
            "token_symbol": pool.get("symbol", "?")[:20],
            "severity": "info",
            "details": {
                "protocol": pool.get("project", ""),
                "pool": pool_id,
                "symbol": pool.get("symbol", ""),
                "apy": round(apy, 2),
                "tvl": tvl,
                "apy_change_1d": apy_change_1d,
                "chain": pool.get("chain", ""),
            },
        }
        await _save_and_broadcast(event)
        log.info(f"YIELD_NEW: {pool.get('project')}/{pool.get('symbol')} APY={apy:.1f}% TVL=${tvl:,.0f}")


async def _poll_loop():
    log.info("Protocol tracker started")
    # Skip first 30s to let other services stabilize
    await asyncio.sleep(30)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                protocols, pools = await asyncio.gather(
                    _fetch_protocols(session),
                    _fetch_yield_pools(session),
                    return_exceptions=True,
                )

                if isinstance(protocols, list) and protocols:
                    await _check_tvl_spikes(protocols)

                if isinstance(pools, list) and pools:
                    await _check_yield_opportunities(pools)

            # Cleanup old dedup entries
            _cleanup_cache(_seen_protocols)
            _cleanup_cache(_seen_pools)
        except Exception as e:
            log.error(f"Protocol tracker poll error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


def start():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll_loop())


def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
