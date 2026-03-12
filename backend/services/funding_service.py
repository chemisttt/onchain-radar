import asyncio
import json
import logging
import aiohttp
from datetime import datetime

from db import get_db

log = logging.getLogger("funding")

_task: asyncio.Task | None = None

EXTREME_THRESHOLD = 0.001  # 0.1% per 8h


async def _fetch_binance(session: aiohttp.ClientSession) -> list[dict]:
    async with session.get(
        "https://fapi.binance.com/fapi/v1/premiumIndex",
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        if resp.status != 200:
            return []
        data = await resp.json()

    return [
        {
            "symbol": item["symbol"],
            "exchange": "Binance",
            "rate": float(item["lastFundingRate"]),
            "settlement_hours": 8,
            "next_funding_time": int(item["nextFundingTime"]),
        }
        for item in data
        if item.get("lastFundingRate")
    ]


async def _fetch_bybit(session: aiohttp.ClientSession) -> list[dict]:
    async with session.get(
        "https://api.bybit.com/v5/market/tickers",
        params={"category": "linear"},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        if resp.status != 200:
            return []
        data = await resp.json()

    return [
        {
            "symbol": item["symbol"],
            "exchange": "Bybit",
            "rate": float(item.get("fundingRate", 0)),
            "settlement_hours": 8,
            "next_funding_time": None,
            "open_interest": float(item.get("openInterest", 0) or 0),
            "volume_24h": float(item.get("turnover24h", 0) or 0),
        }
        for item in data.get("result", {}).get("list", [])
        if item.get("fundingRate")
    ]


async def _fetch_okx(session: aiohttp.ClientSession) -> list[dict]:
    # OKX: funding-rate requires instId, so first get USDT-margined SWAPs then batch
    async with session.get(
        "https://www.okx.com/api/v5/public/instruments",
        params={"instType": "SWAP"},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        if resp.status != 200:
            return []
        inst_data = await resp.json()

    instruments = inst_data.get("data", [])
    # Filter to USDT-margined only for consistency
    usdt_swaps = [i["instId"] for i in instruments if "-USDT-SWAP" in i.get("instId", "")]

    results = []
    # Fetch in batches of 20 concurrently
    for batch_start in range(0, min(len(usdt_swaps), 100), 20):
        batch = usdt_swaps[batch_start:batch_start + 20]
        tasks = []
        for inst_id in batch:
            tasks.append(_fetch_okx_single(session, inst_id))
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in batch_results:
            if isinstance(r, dict) and r:
                results.append(r)

    return results


async def _fetch_okx_single(session: aiohttp.ClientSession, inst_id: str) -> dict:
    try:
        async with session.get(
            "https://www.okx.com/api/v5/public/funding-rate",
            params={"instId": inst_id},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()

        items = data.get("data", [])
        if not items:
            return {}
        item = items[0]
        rate = item.get("fundingRate")
        if not rate:
            return {}
        # Normalize: BTC-USDT-SWAP → BTCUSDT
        symbol = inst_id.replace("-SWAP", "").replace("-", "")
        return {
            "symbol": symbol,
            "exchange": "OKX",
            "rate": float(rate),
            "settlement_hours": 8,
            "next_funding_time": int(item.get("nextFundingTime", 0)) or None,
        }
    except Exception:
        return {}


async def _fetch_mexc(session: aiohttp.ClientSession) -> list[dict]:
    async with session.get(
        "https://contract.mexc.com/api/v1/contract/funding_rate",
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        if resp.status != 200:
            return []
        data = await resp.json()

    results = []
    for item in data.get("data", []):
        rate = item.get("fundingRate")
        if rate:
            symbol = item.get("symbol", "").replace("_", "")
            results.append({
                "symbol": symbol,
                "exchange": "MEXC",
                "rate": float(rate),
                "settlement_hours": 8,
                "next_funding_time": None,
            })
    return results


async def _fetch_hyperliquid(session: aiohttp.ClientSession) -> list[dict]:
    async with session.post(
        "https://api.hyperliquid.xyz/info",
        json={"type": "metaAndAssetCtxs"},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        if resp.status != 200:
            return []
        data = await resp.json()

    # Response: [meta, assetCtxs] where meta.universe has names, assetCtxs has funding
    if not isinstance(data, list) or len(data) < 2:
        return []

    meta = data[0]
    ctxs = data[1]
    universe = meta.get("universe", [])

    import time as _time
    # Compute next hourly funding time (Hyperliquid funds every hour on the hour)
    now_s = int(_time.time())
    next_hour_ms = ((now_s // 3600) + 1) * 3600 * 1000

    results = []
    for i, asset in enumerate(universe):
        if i >= len(ctxs):
            break
        ctx = ctxs[i]
        funding = ctx.get("funding")
        if not funding:
            continue

        # Hyperliquid returns funding as plain string (rate value) not dict
        rate_str = funding
        if isinstance(funding, dict):
            rate_str = funding.get("fundingRate", "0")
        try:
            rate = float(rate_str)
        except (ValueError, TypeError):
            continue
        if rate == 0:
            continue

        symbol = asset.get("name", "")
        # Normalize to match CEX format: BTC → BTCUSDT
        norm_symbol = f"{symbol}USDT" if symbol and not symbol.endswith("USDT") else symbol

        # OI and volume from assetCtxs
        oi = 0.0
        vol = 0.0
        try:
            oi = float(ctx.get("openInterest", 0) or 0)
            vol = float(ctx.get("dayNtlVlm", 0) or 0)
        except (ValueError, TypeError):
            pass

        results.append({
            "symbol": norm_symbol,
            "exchange": "Hyperliquid",
            "rate": rate,
            "settlement_hours": 1,
            "next_funding_time": next_hour_ms,
            "open_interest": oi,
            "volume_24h": vol,
        })
    return results


# ── Perp DEX fetchers ──────────────────────────────────────────────────


def _norm_hourly_to_8h(rate: float) -> float:
    """Normalize 1h funding rate to 8h equivalent for comparison with CEX."""
    return rate * 8


def _next_hour_ms() -> int:
    import time as _time
    now_s = int(_time.time())
    return ((now_s // 3600) + 1) * 3600 * 1000


async def _fetch_paradex(session: aiohttp.ClientSession) -> list[dict]:
    """Paradex — 8h settlement, no normalization needed."""
    try:
        async with session.get(
            "https://api.prod.paradex.trade/v1/markets/summary",
            params={"market": "ALL"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                log.warning(f"Paradex API returned {resp.status}")
                return []
            data = await resp.json()

        results = []
        items = data.get("results", []) if isinstance(data, dict) else data
        for item in items:
            market = item.get("symbol", "")
            rate = item.get("funding_rate")
            if rate is None:
                continue
            try:
                rate = float(rate)
            except (ValueError, TypeError):
                continue
            if rate == 0:
                continue

            # BTC-USD-PERP → BTCUSDT
            symbol = market.replace("-USD-PERP", "").replace("-PERP", "").replace("-", "")
            if not symbol.endswith("USDT"):
                symbol += "USDT"

            results.append({
                "symbol": symbol,
                "exchange": "Paradex",
                "rate": rate,
                "settlement_hours": 8,
                "next_funding_time": None,
            })
        return results
    except Exception as e:
        log.warning(f"Paradex fetch error: {e}")
        return []


async def _fetch_lighter(session: aiohttp.ClientSession) -> list[dict]:
    """Lighter — returns rates for multiple exchanges, filter to lighter only."""
    try:
        async with session.get(
            "https://mainnet.zklighter.elliot.ai/api/v1/funding-rates",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                log.warning(f"Lighter API returned {resp.status}")
                return []
            data = await resp.json()

        results = []
        items = data.get("funding_rates", []) if isinstance(data, dict) else data
        for item in items:
            # Filter to Lighter's own rates only
            if item.get("exchange", "").lower() != "lighter":
                continue
            symbol_raw = item.get("symbol", "")
            rate = item.get("rate")
            if rate is None:
                continue
            try:
                rate_val = float(rate)
            except (ValueError, TypeError):
                continue
            if rate_val == 0:
                continue

            # BTC-USD → BTCUSDT
            symbol = symbol_raw.replace("-", "").replace("_", "").replace("/", "").replace("USD", "USDT")
            if not symbol.endswith("USDT"):
                symbol += "USDT"
            # Avoid double USDT
            symbol = symbol.replace("USDTUSDT", "USDT")

            results.append({
                "symbol": symbol,
                "exchange": "Lighter",
                "rate": rate_val,
                "settlement_hours": 1,
                "next_funding_time": _next_hour_ms(),
            })
        return results
    except Exception as e:
        log.warning(f"Lighter fetch error: {e}")
        return []


async def _fetch_extended(session: aiohttp.ClientSession) -> list[dict]:
    """Extended Exchange (Starknet) — per-market funding, batch top markets."""
    EXTENDED_MARKETS = ["BTC-USD", "ETH-USD", "SOL-USD", "STRK-USD", "DOGE-USD",
                        "SUI-USD", "PEPE-USD", "WIF-USD", "AVAX-USD", "LINK-USD"]
    try:
        import time as _time
        now_ms = int(_time.time() * 1000)
        start_ms = now_ms - 3600_000  # last hour

        results = []
        for market in EXTENDED_MARKETS:
            try:
                async with session.get(
                    f"https://api.starknet.extended.exchange/api/v1/info/{market}/funding",
                    params={"startTime": start_ms, "endTime": now_ms, "limit": 1},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

                items = data.get("data", []) if isinstance(data, dict) else data
                if not items:
                    continue
                # Take the latest funding entry
                latest = items[-1] if isinstance(items, list) else items
                rate = latest.get("f", latest.get("funding_rate"))
                if rate is None:
                    continue
                rate_1h = float(rate)
                if rate_1h == 0:
                    continue

                # BTC-USD → BTCUSDT
                symbol = market.replace("-USD", "USDT").replace("-", "")

                results.append({
                    "symbol": symbol,
                    "exchange": "Extended",
                    "rate": _norm_hourly_to_8h(rate_1h),
                    "settlement_hours": 1,
                    "next_funding_time": _next_hour_ms(),
                })
            except Exception:
                continue
        return results
    except Exception as e:
        log.warning(f"Extended fetch error: {e}")
        return []


EDGEX_CONTRACTS: dict[str, str] = {
    "10000001": "BTCUSDT",
    "10000002": "ETHUSDT",
    "10000003": "SOLUSDT",
    "10000004": "BNBUSDT",
    "10000005": "LTCUSDT",
    "10000006": "DOGEUSDT",
    "10000007": "APTUSDT",
    "10000008": "TRXUSDT",
    "10000009": "ADAUSDT",
    "10000010": "SHIBUSDT",
    "10000011": "PEPEUSDT",
    "10000012": "AAVEUSDT",
    "10000013": "SUIUSDT",
    "10000014": "NEARUSDT",
    "10000015": "SEIUSDT",
    "10000016": "WIFUSDT",
    "10000017": "ARBUSDT",
    "10000018": "JUPUSDT",
    "10000019": "TRUMPUSDT",
    "10000020": "VIRTUALUSDT",
}


async def _fetch_edgex(session: aiohttp.ClientSession) -> list[dict]:
    """EdgeX — numeric contractId-based API, query top contracts."""
    try:
        results = []
        for contract_id, symbol in EDGEX_CONTRACTS.items():
            try:
                async with session.get(
                    "https://pro.edgex.exchange/api/v1/public/funding/getLatestFundingRate",
                    params={"contractId": contract_id},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

                if data.get("code") != "SUCCESS":
                    continue

                items = data.get("data", [])
                if not items or not isinstance(items, list):
                    continue

                item = items[0]
                rate = item.get("fundingRate")
                if rate is None:
                    continue
                rate_val = float(rate)
                if rate_val == 0:
                    continue

                funding_time = item.get("fundingTime")
                results.append({
                    "symbol": symbol,
                    "exchange": "EdgeX",
                    "rate": rate_val,
                    "settlement_hours": 8,
                    "next_funding_time": int(funding_time) if funding_time else None,
                })
            except Exception:
                continue
        return results
    except Exception as e:
        log.warning(f"EdgeX fetch error: {e}")
        return []


async def _fetch_aster(session: aiohttp.ClientSession) -> list[dict]:
    """Aster (asterdex.com) — Binance-compatible API format, 8h settlement."""
    try:
        async with session.get(
            "https://fapi.asterdex.com/fapi/v1/premiumIndex",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                log.warning(f"Aster API returned {resp.status}")
                return []
            data = await resp.json()

        results = []
        items = data if isinstance(data, list) else [data]
        for item in items:
            rate = item.get("lastFundingRate")
            if rate is None:
                continue
            try:
                rate_val = float(rate)
            except (ValueError, TypeError):
                continue
            if rate_val == 0:
                continue

            symbol = item.get("symbol", "")
            # Normalize: GNSUSD → GNSUSDT, keep BTCUSDT as-is
            if symbol.endswith("USD") and not symbol.endswith("USDT"):
                symbol = symbol[:-3] + "USDT"
            nft = item.get("nextFundingTime")

            results.append({
                "symbol": symbol,
                "exchange": "Aster",
                "rate": rate_val,
                "settlement_hours": 8,
                "next_funding_time": int(nft) if nft else None,
            })
        return results
    except Exception as e:
        log.warning(f"Aster fetch error: {e}")
        return []


async def _fetch_variational(session: aiohttp.ClientSession) -> list[dict]:
    """Variational — 8h settlement (funding_interval_s=28800), stats endpoint."""
    try:
        async with session.get(
            "https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                log.warning(f"Variational API returned {resp.status}")
                return []
            data = await resp.json()

        results = []
        listings = data.get("listings", []) if isinstance(data, dict) else []
        for item in listings:
            ticker = item.get("ticker", "")
            rate = item.get("funding_rate")
            if rate is None:
                continue
            try:
                # Variational returns rate as percentage string (e.g. "0.037347" = 0.037347%)
                rate_val = float(rate) / 100
            except (ValueError, TypeError):
                continue
            if rate_val == 0:
                continue
            # Skip absurd rates (>5% per period = bad data)
            if abs(rate_val) > 0.05:
                continue

            interval = item.get("funding_interval_s", 28800)
            settlement_h = interval // 3600 if interval else 8

            symbol = f"{ticker}USDT" if ticker and not ticker.endswith("USDT") else ticker

            results.append({
                "symbol": symbol,
                "exchange": "Variational",
                "rate": rate_val,
                "settlement_hours": settlement_h,
                "next_funding_time": None,
            })
        return results
    except Exception as e:
        log.warning(f"Variational fetch error: {e}")
        return []


# In-memory cache for rates — poll loop refreshes, API endpoints read from cache
_cached_rates: list[dict] = []
_cache_ts: float = 0
_CACHE_TTL = 45  # seconds — poll loop runs every 60s
_fetch_lock: asyncio.Lock | None = None


def _get_fetch_lock() -> asyncio.Lock:
    global _fetch_lock
    if _fetch_lock is None:
        _fetch_lock = asyncio.Lock()
    return _fetch_lock


async def _fetch_all_rates_live() -> list[dict]:
    """Actually fetch from all 11 exchanges. Called only by poll loop or on cache miss."""
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            # CEX (5)
            _fetch_binance(session),
            _fetch_bybit(session),
            _fetch_okx(session),
            _fetch_mexc(session),
            # Hyperliquid L1
            _fetch_hyperliquid(session),
            # Perp DEX (6)
            _fetch_paradex(session),
            _fetch_lighter(session),
            _fetch_extended(session),
            _fetch_edgex(session),
            _fetch_aster(session),
            _fetch_variational(session),
            return_exceptions=True,
        )

    all_rates = []
    for r in results:
        if isinstance(r, list):
            all_rates.extend(r)
        elif isinstance(r, Exception):
            log.warning(f"Funding fetch error: {r}")

    return all_rates


async def fetch_all_rates() -> list[dict]:
    """Return cached rates if fresh, otherwise fetch live (with lock to avoid concurrent fetches)."""
    global _cached_rates, _cache_ts
    import time as _t

    now = _t.time()
    if _cached_rates and now - _cache_ts < _CACHE_TTL:
        return _cached_rates

    lock = _get_fetch_lock()
    if lock.locked():
        # Another coroutine is already fetching — return stale cache
        return _cached_rates

    async with lock:
        # Double-check after acquiring lock
        now = _t.time()
        if _cached_rates and now - _cache_ts < _CACHE_TTL:
            return _cached_rates

        rates = await _fetch_all_rates_live()
        if rates:
            _cached_rates = rates
            _cache_ts = _t.time()
        return _cached_rates


SPREAD_THRESHOLD = 0.002  # 0.2% spread triggers alert (was 0.05% — too noisy)
EXTREME_RATE_ALERT = 0.003  # 0.3% per 8h — only truly extreme rates

# Dedup for funding alerts: key → monotonic timestamp
_FUNDING_ALERT_TTL = 3600  # 1 hour dedup
_seen_funding_alerts: dict[str, float] = {}


def _funding_alert_seen(key: str) -> bool:
    import time as _t
    now = _t.monotonic()
    if key in _seen_funding_alerts and now - _seen_funding_alerts[key] < _FUNDING_ALERT_TTL:
        return True
    _seen_funding_alerts[key] = now
    return False


async def _save_rates(rates: list[dict]):
    """Save funding snapshots. Only save tracked symbols to keep DB small."""
    from services.derivatives_service import SYMBOLS as _TRACKED
    _tracked_set = set(_TRACKED)

    db = get_db()

    # Only save symbols that are in our tracked list AND appear on 2+ exchanges
    symbol_count: dict[str, int] = {}
    for r in rates:
        if r["symbol"] in _tracked_set:
            symbol_count[r["symbol"]] = symbol_count.get(r["symbol"], 0) + 1
    multi_exchange = {s for s, c in symbol_count.items() if c >= 2}

    saved = 0
    for r in rates:
        if r["symbol"] not in multi_exchange:
            continue
        await db.execute(
            """INSERT INTO funding_snapshots (symbol, exchange, rate, next_funding_time)
               VALUES (?, ?, ?, ?)""",
            (r["symbol"], r["exchange"], r["rate"], r.get("next_funding_time")),
        )
        saved += 1
    await db.commit()

    # Prune old snapshots (keep 7 days — matches frontend default of 168h)
    await db.execute("DELETE FROM funding_snapshots WHERE fetched_at < datetime('now', '-7 days')")
    await db.commit()

    return saved


async def _check_extremes(rates: list[dict]):
    """Broadcast alerts for truly extreme rates and large spreads. Rate-limited with dedup."""
    from services.feed_engine import _save_and_broadcast

    alerts_sent = 0

    # Only alert on truly extreme rates (top 5 max)
    extreme = sorted(
        [r for r in rates if abs(r["rate"]) >= EXTREME_RATE_ALERT],
        key=lambda x: abs(x["rate"]),
        reverse=True,
    )
    for r in extreme[:5]:
        key = f"extreme:{r['symbol']}:{r['exchange']}"
        if _funding_alert_seen(key):
            continue
        direction = "LONG PAYS" if r["rate"] > 0 else "SHORT PAYS"
        event = {
            "event_type": "FUNDING_EXTREME",
            "chain": "perp",
            "token_address": None,
            "pair_address": None,
            "token_symbol": r["symbol"],
            "severity": "warning",
            "details": {
                "exchange": r["exchange"],
                "rate": r["rate"],
                "apr": r["rate"] * 3 * 365,
                "direction": direction,
            },
        }
        await _save_and_broadcast(event)
        alerts_sent += 1

    # Only alert on top 5 spreads
    by_symbol: dict[str, list[dict]] = {}
    for r in rates:
        by_symbol.setdefault(r["symbol"], []).append(r)

    spreads = []
    for symbol, sym_rates in by_symbol.items():
        if len(sym_rates) < 2:
            continue
        min_r = min(sym_rates, key=lambda x: x["rate"])
        max_r = max(sym_rates, key=lambda x: x["rate"])
        spread = max_r["rate"] - min_r["rate"]
        if spread >= SPREAD_THRESHOLD:
            spreads.append((symbol, min_r, max_r, spread))

    spreads.sort(key=lambda x: x[3], reverse=True)
    for symbol, min_r, max_r, spread in spreads[:5]:
        key = f"spread:{symbol}"
        if _funding_alert_seen(key):
            continue
        event = {
            "event_type": "FUNDING_SPREAD",
            "chain": "perp",
            "token_address": None,
            "pair_address": None,
            "token_symbol": symbol,
            "severity": "info" if spread < 0.005 else "warning",
            "details": {
                "spread": spread,
                "long_exchange": min_r["exchange"],
                "long_rate": min_r["rate"],
                "short_exchange": max_r["exchange"],
                "short_rate": max_r["rate"],
                "est_daily_pct": spread * 3,
            },
        }
        await _save_and_broadcast(event)
        alerts_sent += 1

    # Cleanup old dedup entries
    import time as _t
    now = _t.monotonic()
    expired = [k for k, ts in _seen_funding_alerts.items() if now - ts >= _FUNDING_ALERT_TTL]
    for k in expired:
        del _seen_funding_alerts[k]

    return alerts_sent


# ── Historical backfill ──────────────────────────────────────────────

# Top symbols to fetch history for (Bybit/OKX/HL require per-symbol calls)
_TOP_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT", "SUIUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT",
    "AAVEUSDT", "FILUSDT", "ATOMUSDT", "FTMUSDT", "TRXUSDT", "SHIBUSDT",
    "PEPEUSDT", "WIFUSDT", "JUPUSDT", "SEIUSDT", "TIAUSDT", "INJUSDT",
]


async def _backfill_binance(session: aiohttp.ClientSession, start_ms: int) -> int:
    """Binance supports all-symbol fetch. Paginate with limit=1000."""
    saved = 0
    cursor = start_ms
    db = get_db()
    for _ in range(20):  # max 20 pages
        try:
            async with session.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"startTime": cursor, "limit": 1000},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    break
                data = await resp.json()
            if not data:
                break
            for item in data:
                sym = item.get("symbol", "")
                rate = item.get("fundingRate")
                ft = item.get("fundingTime")
                if not rate or not ft:
                    continue
                from datetime import datetime, timezone
                ts = datetime.fromtimestamp(int(ft) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                await db.execute(
                    "INSERT OR IGNORE INTO funding_snapshots (symbol, exchange, rate, next_funding_time, fetched_at) VALUES (?,?,?,?,?)",
                    (sym, "Binance", float(rate), int(ft), ts),
                )
                saved += 1
            cursor = int(data[-1]["fundingTime"]) + 1
            if len(data) < 1000:
                break
        except Exception as e:
            log.warning(f"Binance backfill error: {e}")
            break
        await asyncio.sleep(0.5)
    await db.commit()
    return saved


async def _backfill_bybit(session: aiohttp.ClientSession, start_ms: int) -> int:
    """Bybit: per-symbol, limit=200, top symbols only."""
    saved = 0
    db = get_db()
    for sym in _TOP_SYMBOLS:
        try:
            async with session.get(
                "https://api.bybit.com/v5/market/funding/history",
                params={"category": "linear", "symbol": sym, "startTime": start_ms, "endTime": int(asyncio.get_event_loop().time() * 1000) + 1000, "limit": 200},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
            items = data.get("result", {}).get("list", [])
            for item in items:
                rate = item.get("fundingRate")
                ft = item.get("fundingRateTimestamp")
                if not rate or not ft:
                    continue
                from datetime import datetime, timezone
                ts = datetime.fromtimestamp(int(ft) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                await db.execute(
                    "INSERT OR IGNORE INTO funding_snapshots (symbol, exchange, rate, next_funding_time, fetched_at) VALUES (?,?,?,?,?)",
                    (sym, "Bybit", float(rate), int(ft), ts),
                )
                saved += 1
        except Exception as e:
            log.warning(f"Bybit backfill {sym}: {e}")
        await asyncio.sleep(0.1)
    await db.commit()
    return saved


async def _backfill_okx(session: aiohttp.ClientSession, start_ms: int) -> int:
    """OKX: per-instrument, limit=100. Rate limit: 20/2s."""
    saved = 0
    db = get_db()
    for sym in _TOP_SYMBOLS:
        inst_id = sym.replace("USDT", "-USDT-SWAP")
        try:
            async with session.get(
                "https://www.okx.com/api/v5/public/funding-rate-history",
                params={"instId": inst_id, "limit": "100"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
            for item in data.get("data", []):
                rate = item.get("realizedRate") or item.get("fundingRate")
                ft = item.get("fundingTime")
                if not rate or not ft:
                    continue
                norm_sym = inst_id.replace("-SWAP", "").replace("-", "")
                from datetime import datetime, timezone
                ts = datetime.fromtimestamp(int(ft) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                await db.execute(
                    "INSERT OR IGNORE INTO funding_snapshots (symbol, exchange, rate, next_funding_time, fetched_at) VALUES (?,?,?,?,?)",
                    (norm_sym, "OKX", float(rate), int(ft), ts),
                )
                saved += 1
        except Exception as e:
            log.warning(f"OKX backfill {inst_id}: {e}")
        await asyncio.sleep(0.15)
    await db.commit()
    return saved


async def _backfill_hyperliquid(session: aiohttp.ClientSession, start_ms: int) -> int:
    """Hyperliquid: per-coin POST, 1h intervals, up to 500 records."""
    saved = 0
    db = get_db()
    # Get coin list from meta
    coins = []
    try:
        async with session.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "meta"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                meta = await resp.json()
                coins = [a["name"] for a in meta.get("universe", [])][:50]
    except Exception:
        coins = [s.replace("USDT", "") for s in _TOP_SYMBOLS]

    for coin in coins:
        try:
            async with session.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "fundingHistory", "coin": coin, "startTime": start_ms},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
            for item in data:
                rate = item.get("fundingRate")
                ft = item.get("time")
                if not rate or not ft:
                    continue
                from datetime import datetime, timezone
                ts = datetime.fromtimestamp(int(ft) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                norm_sym = f"{coin}USDT" if not coin.endswith("USDT") else coin
                await db.execute(
                    "INSERT OR IGNORE INTO funding_snapshots (symbol, exchange, rate, next_funding_time, fetched_at) VALUES (?,?,?,?,?)",
                    (norm_sym, "Hyperliquid", float(rate), int(ft), ts),
                )
                saved += 1
        except Exception as e:
            log.warning(f"HL backfill {coin}: {e}")
        await asyncio.sleep(0.1)
    await db.commit()
    return saved


async def backfill_history():
    """Fetch 7-day historical funding rates if DB has little data. Run once on startup."""
    db = get_db()
    row = await db.execute_fetchall(
        "SELECT MIN(fetched_at) as oldest FROM funding_snapshots"
    )
    oldest = row[0]["oldest"] if row and row[0]["oldest"] else None

    # Skip if we already have 6+ hours of data
    if oldest:
        from datetime import datetime, timezone
        oldest_dt = datetime.strptime(oldest, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        hours = (now_dt - oldest_dt).total_seconds() / 3600
        if hours >= 6:
            log.info(f"Backfill skipped: already have {hours:.0f}h of data")
            return

    import time as _t
    start_ms = int((_t.time() - 7 * 86400) * 1000)
    log.info("Starting 7-day funding rate backfill...")

    async with aiohttp.ClientSession() as session:
        bn = await _backfill_binance(session, start_ms)
        log.info(f"Backfill Binance: {bn} records")

        bb = await _backfill_bybit(session, start_ms)
        log.info(f"Backfill Bybit: {bb} records")

        okx = await _backfill_okx(session, start_ms)
        log.info(f"Backfill OKX: {okx} records")

        hl = await _backfill_hyperliquid(session, start_ms)
        log.info(f"Backfill Hyperliquid: {hl} records")

    log.info(f"Backfill complete: {bn + bb + okx + hl} total records")


async def _poll_loop():
    log.info("Funding rate polling started")
    # Backfill historical data on first run
    try:
        await backfill_history()
    except Exception as e:
        log.error(f"Backfill error: {e}")

    while True:
        try:
            rates = await fetch_all_rates()
            if rates:
                saved = await _save_rates(rates)
                alerts = await _check_extremes(rates)
                log.info(f"Funding: {len(rates)} fetched, {saved} saved, {alerts} alerts")
        except Exception as e:
            log.error(f"Funding poll error: {e}")
        await asyncio.sleep(60)


def start():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll_loop())


def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
