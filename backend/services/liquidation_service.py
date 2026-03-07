import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta

import aiohttp

from db import get_db
from services.derivatives_service import SYMBOLS

log = logging.getLogger("liquidation")

_tasks: list[asyncio.Task] = []

LEVERAGE_TIERS = [5, 10, 25, 50, 100]
LEVERAGE_WEIGHTS = [0.10, 0.25, 0.30, 0.20, 0.15]


# ── Binance WS Liquidations ─────────────────────────────────────────

async def _ws_binance_liquidations():
    """Connect to Binance forceOrder stream and collect liquidation events."""
    url = "wss://fstream.binance.com/ws/!forceOrder@arr"
    db = get_db()
    batch: list[tuple] = []
    last_flush = time.time()

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=30, timeout=aiohttp.ClientTimeout(total=0)) as ws:
                    log.info("Binance liquidation WS connected")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                o = data.get("o", {})
                                sym = o.get("s", "")
                                # Normalize symbol
                                from services.derivatives_service import BINANCE_SYMBOL_MAP
                                sym = BINANCE_SYMBOL_MAP.get(sym, sym)
                                if sym not in SYMBOLS:
                                    # Time-based flush even without matches
                                    if batch and time.time() - last_flush > 10:
                                        await db.executemany(
                                            """INSERT INTO liquidation_events
                                               (symbol, side, price, quantity, usd_value, exchange, timestamp)
                                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                            batch,
                                        )
                                        await db.commit()
                                        batch.clear()
                                        last_flush = time.time()
                                    continue

                                side = "long" if o.get("S", "") == "SELL" else "short"
                                price = float(o.get("p", 0))
                                qty = float(o.get("q", 0))
                                usd_val = price * qty
                                ts = int(o.get("T", time.time() * 1000))

                                batch.append((sym, side, price, qty, usd_val, "binance", ts))

                                if len(batch) >= 3 or time.time() - last_flush > 10:
                                    await db.executemany(
                                        """INSERT INTO liquidation_events
                                           (symbol, side, price, quantity, usd_value, exchange, timestamp)
                                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                        batch,
                                    )
                                    await db.commit()
                                    batch.clear()
                                    last_flush = time.time()
                            except Exception as e:
                                log.debug(f"Binance liq parse: {e}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"Binance liq WS error: {e}")
        await asyncio.sleep(5)


# ── Bybit WS Liquidations ───────────────────────────────────────────

async def _ws_bybit_liquidations():
    """Connect to Bybit allLiquidation stream."""
    url = "wss://stream.bybit.com/v5/public/linear"
    db = get_db()
    batch: list[tuple] = []
    last_flush = time.time()

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=30, timeout=aiohttp.ClientTimeout(total=0)) as ws:
                    # Subscribe to all liquidation events
                    sub_msg = json.dumps({
                        "op": "subscribe",
                        "args": ["allLiquidation"]
                    })
                    await ws.send_str(sub_msg)
                    log.info("Bybit liquidation WS connected")

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                if data.get("topic") != "allLiquidation":
                                    # Time-based flush on non-topic messages (pong, etc.)
                                    if batch and time.time() - last_flush > 10:
                                        await db.executemany(
                                            """INSERT INTO liquidation_events
                                               (symbol, side, price, quantity, usd_value, exchange, timestamp)
                                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                            batch,
                                        )
                                        await db.commit()
                                        batch.clear()
                                        last_flush = time.time()
                                    continue
                                d = data.get("data", {})
                                sym = d.get("symbol", "")
                                if sym not in SYMBOLS:
                                    continue

                                side = "long" if d.get("side", "") == "Sell" else "short"
                                price = float(d.get("price", 0))
                                qty = float(d.get("size", 0))
                                usd_val = price * qty
                                ts = int(d.get("updatedTime", time.time() * 1000))

                                batch.append((sym, side, price, qty, usd_val, "bybit", ts))

                                if len(batch) >= 3 or time.time() - last_flush > 10:
                                    await db.executemany(
                                        """INSERT INTO liquidation_events
                                           (symbol, side, price, quantity, usd_value, exchange, timestamp)
                                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                        batch,
                                    )
                                    await db.commit()
                                    batch.clear()
                                    last_flush = time.time()
                            except Exception as e:
                                log.debug(f"Bybit liq parse: {e}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"Bybit liq WS error: {e}")
        await asyncio.sleep(5)


# ── Cleanup ──────────────────────────────────────────────────────────

async def _cleanup_loop():
    """Delete events older than 7 days periodically."""
    while True:
        try:
            db = get_db()
            cutoff = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)
            await db.execute("DELETE FROM liquidation_events WHERE timestamp < ?", (cutoff,))
            await db.commit()
        except Exception as e:
            log.warning(f"Liq cleanup error: {e}")
        await asyncio.sleep(3600)  # hourly


# ── Liquidation Cluster Map (historical OI accumulation) ─────────────

import bisect

# Cache: symbol → (ts, result)
_cluster_cache: dict[str, tuple[float, dict]] = {}
_CLUSTER_CACHE_TTL = 300  # 5 min

_CLUSTER_LEVERAGES = [5, 10, 25, 50]
_CLUSTER_LEV_WEIGHTS = [0.10, 0.25, 0.35, 0.30]


async def compute_liq_clusters(symbol: str) -> dict:
    """Estimate liquidation clusters from historical OI accumulation.

    Walks 4h OI snapshots, detects where new positions were opened (OI delta > 0),
    projects liq levels per leverage tier, checks if those levels were already
    reached (= positions liquidated), bins surviving volume into price clusters.

    Returns: {"current_price": float, "clusters": [{"level_price", "volume_usd",
              "long_vol", "short_vol", "direction", "distance_pct"}]}
    """
    sym = symbol.upper()
    now = time.time()
    cached = _cluster_cache.get(sym)
    if cached and now - cached[0] < _CLUSTER_CACHE_TTL:
        return cached[1]

    db = get_db()

    # 1. Get 4h OI snapshots (ordered by time)
    oi_rows = await db.execute_fetchall(
        """SELECT ts, close_price, open_interest_usd, funding_rate
           FROM derivatives_4h
           WHERE symbol = ? AND open_interest_usd > 0
           ORDER BY ts""",
        (sym,),
    )

    # Fallback to daily if 4h data is sparse
    if len(oi_rows) < 10:
        daily = await db.execute_fetchall(
            """SELECT date, close_price, open_interest_usd, funding_rate
               FROM daily_derivatives
               WHERE symbol = ? AND open_interest_usd > 0
               ORDER BY date""",
            (sym,),
        )
        # Convert date → fake ts for uniform processing
        oi_rows = []
        for r in daily:
            dt = datetime.strptime(r["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            oi_rows.append({
                "ts": int(dt.timestamp() * 1000),
                "close_price": r["close_price"],
                "open_interest_usd": r["open_interest_usd"],
                "funding_rate": r["funding_rate"],
            })

    if len(oi_rows) < 3:
        result = {"current_price": 0, "clusters": []}
        _cluster_cache[sym] = (now, result)
        return result

    current_price = oi_rows[-1]["close_price"]

    # 2. Get OHLCV 4h for survival check (was a liq level already hit?)
    ohlcv = await db.execute_fetchall(
        "SELECT ts, high, low FROM ohlcv_4h WHERE symbol = ? ORDER BY ts",
        (sym,),
    )
    if not ohlcv:
        result = {"current_price": current_price, "clusters": []}
        _cluster_cache[sym] = (now, result)
        return result

    ohlcv_ts = [r["ts"] for r in ohlcv]
    ohlcv_lows = [r["low"] for r in ohlcv]
    ohlcv_highs = [r["high"] for r in ohlcv]

    # Precompute suffix min(low) and suffix max(high)
    n = len(ohlcv)
    sfx_min = ohlcv_lows[:]
    sfx_max = ohlcv_highs[:]
    for i in range(n - 2, -1, -1):
        sfx_min[i] = min(sfx_min[i], sfx_min[i + 1])
        sfx_max[i] = max(sfx_max[i], sfx_max[i + 1])

    def _survived(ts_ms: int, liq_price: float, is_long: bool) -> bool:
        """Was this liq level NOT reached after time ts?"""
        idx = bisect.bisect_right(ohlcv_ts, ts_ms)
        if idx >= n:
            return True  # no candles after → still alive
        if is_long:
            return sfx_min[idx] > liq_price  # longs die if low went below liq
        return sfx_max[idx] < liq_price  # shorts die if high went above liq

    # 3. Walk OI history, project liq levels for new positions
    bin_size = current_price * 0.005  # 0.5% bins
    if bin_size <= 0:
        bin_size = 1
    liq_bins: dict[int, list[float]] = {}  # bin_idx → [long_vol, short_vol]

    prev_oi = 0
    for row in oi_rows:
        oi = row["open_interest_usd"] or 0
        price = row["close_price"] or 0
        ts = row["ts"]
        try:
            funding = row["funding_rate"] or 0
        except (KeyError, IndexError):
            funding = 0

        delta = oi - prev_oi
        prev_oi = oi

        if delta <= 0 or price <= 0:
            continue

        # Estimate long/short split from funding rate
        # funding > 0 → longs pay shorts → more longs opened
        long_pct = 0.5 + min(max(funding * 1000, -0.15), 0.15)

        for lev, weight in zip(_CLUSTER_LEVERAGES, _CLUSTER_LEV_WEIGHTS):
            vol = delta * weight

            long_liq = price * (1 - 1 / lev)
            short_liq = price * (1 + 1 / lev)

            long_vol = vol * long_pct
            short_vol = vol * (1 - long_pct)

            if long_vol > 0 and _survived(ts, long_liq, is_long=True):
                b = int(long_liq / bin_size)
                entry = liq_bins.setdefault(b, [0.0, 0.0])
                entry[0] += long_vol

            if short_vol > 0 and _survived(ts, short_liq, is_long=False):
                b = int(short_liq / bin_size)
                entry = liq_bins.setdefault(b, [0.0, 0.0])
                entry[1] += short_vol

    # 4. Convert bins to clusters, filter by proximity
    clusters = []
    for b, (lvol, svol) in liq_bins.items():
        total = lvol + svol
        if total < 1e6:
            continue
        bin_price = (b + 0.5) * bin_size
        dist_pct = (bin_price - current_price) / current_price * 100
        if abs(dist_pct) > 15:
            continue
        clusters.append({
            "level_price": round(bin_price, 2),
            "volume_usd": round(total, 0),
            "long_vol": round(lvol, 0),
            "short_vol": round(svol, 0),
            "direction": "long" if lvol > svol else "short",
            "distance_pct": round(dist_pct, 2),
        })

    clusters.sort(key=lambda x: x["volume_usd"], reverse=True)
    result = {"current_price": current_price, "clusters": clusters[:30]}
    _cluster_cache[sym] = (now, result)
    return result


async def _compute_theoretical_levels(symbol: str) -> list[dict]:
    """Legacy wrapper — returns clusters in old format for liquidation map API."""
    data = await compute_liq_clusters(symbol)
    levels = []
    for c in data["clusters"]:
        levels.append({
            "price": c["level_price"],
            "long_vol": c["long_vol"],
            "short_vol": c["short_vol"],
            "leverage": 0,  # mixed
        })
    return levels


# ── API response ─────────────────────────────────────────────────────

async def get_liquidation_map(symbol: str) -> dict:
    """Return liquidation map data for a symbol."""
    db = get_db()
    sym = symbol.upper()

    levels = await _compute_theoretical_levels(sym)

    # Get current price
    row = await db.execute_fetchall(
        "SELECT close_price FROM daily_derivatives WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (sym,),
    )
    current_price = row[0]["close_price"] if row else 0

    # Recent real liquidation events (last 24h)
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)
    events = await db.execute_fetchall(
        """SELECT price, side, usd_value, exchange, timestamp
           FROM liquidation_events
           WHERE symbol = ? AND timestamp >= ?
           ORDER BY timestamp DESC LIMIT 50""",
        (sym, cutoff),
    )

    recent = [
        {
            "price": r["price"],
            "side": r["side"],
            "usd_value": r["usd_value"],
            "exchange": r["exchange"],
            "ts": r["timestamp"],
        }
        for r in events
    ]

    return {
        "symbol": sym,
        "current_price": current_price,
        "levels": levels,
        "recent_events": recent,
    }


# ── Service lifecycle ────────────────────────────────────────────────

def start():
    global _tasks
    if not _tasks:
        _tasks = [
            asyncio.create_task(_ws_binance_liquidations()),
            asyncio.create_task(_ws_bybit_liquidations()),
            asyncio.create_task(_cleanup_loop()),
        ]
        log.info("Liquidation service started (Binance + Bybit WS)")


def stop():
    global _tasks
    for t in _tasks:
        if not t.done():
            t.cancel()
    _tasks = []
