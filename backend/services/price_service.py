"""Price service — 4h OHLCV polling, swing detection, key levels, EMA, ATR.

Provides price structure analysis for trade setup building:
- get_price_structure(symbol) → dict with trend, swings, key_levels, EMAs, ATR
"""

import asyncio
import logging
import time as _time

import aiohttp

from db import get_db
from services.derivatives_service import SYMBOLS

log = logging.getLogger("price_service")

POLL_INTERVAL = 300  # 5 min
BACKFILL_LIMIT = 500  # ~83 days of 4h candles
LIVE_LIMIT = 3
FETCH_DELAY = 0.2  # between symbols on backfill

SWING_LOOKBACK = 3  # fractal: 3 candles each side
ANALYSIS_CANDLES = 100  # last 100 candles for swing detection
ATR_PERIOD = 14
CLUSTER_ATR_MULT = 0.5  # two swings within 0.5*ATR → merge

_task: asyncio.Task | None = None
_cache: dict[str, dict] = {}  # symbol → price structure
_cache_ts: float = 0


def start():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll_loop())


def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None


def get_price_structure(symbol: str) -> dict | None:
    """Return cached price structure for symbol, or None if not ready."""
    return _cache.get(symbol)


# ── Poll loop ────────────────────────────────────────────────────────

async def _poll_loop():
    log.info("Price service started")
    try:
        await _backfill()
    except Exception as e:
        log.error(f"Backfill error: {e}")

    while True:
        try:
            await _fetch_live()
            await _update_all_structures()
        except Exception as e:
            log.error(f"Price poll error: {e}")
        await asyncio.sleep(POLL_INTERVAL)


async def _backfill():
    """Fetch 500 4h candles per symbol on first run."""
    db = get_db()
    count = 0
    async with aiohttp.ClientSession() as session:
        for sym in SYMBOLS:
            # Check if already have data
            row = await db.execute_fetchone(
                "SELECT COUNT(*) as cnt FROM ohlcv_4h WHERE symbol = ?", (sym,)
            )
            if row and row["cnt"] >= BACKFILL_LIMIT * 0.8:
                count += 1
                continue

            candles = await _fetch_klines(session, sym, BACKFILL_LIMIT)
            if candles:
                await _upsert_candles(db, sym, candles)
                count += 1
            await asyncio.sleep(FETCH_DELAY)

    await db.commit()
    log.info(f"Price service: backfilled {count} symbols")


async def _fetch_live():
    """Fetch last 3 candles for all symbols."""
    db = get_db()
    async with aiohttp.ClientSession() as session:
        for sym in SYMBOLS:
            candles = await _fetch_klines(session, sym, LIVE_LIMIT)
            if candles:
                await _upsert_candles(db, sym, candles)
            await asyncio.sleep(0.05)
    await db.commit()


async def _fetch_klines(
    session: aiohttp.ClientSession, symbol: str, limit: int
) -> list[tuple]:
    """Fetch 4h klines from Binance futures."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": "4h", "limit": limit}
    try:
        async with session.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            # [open_time, open, high, low, close, volume, ...]
            return [
                (
                    int(k[0]),       # ts
                    float(k[1]),     # open
                    float(k[2]),     # high
                    float(k[3]),     # low
                    float(k[4]),     # close
                    float(k[5]),     # volume
                )
                for k in data
            ]
    except Exception as e:
        log.warning(f"Klines fetch error {symbol}: {e}")
        return []


async def _upsert_candles(db, symbol: str, candles: list[tuple]):
    """Insert or replace candles into ohlcv_4h."""
    for ts, o, h, l, c, v in candles:
        await db.execute(
            """INSERT OR REPLACE INTO ohlcv_4h (symbol, ts, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (symbol, ts, o, h, l, c, v),
        )


# ── Analysis ─────────────────────────────────────────────────────────

async def _update_all_structures():
    """Recompute price structure for all symbols."""
    global _cache, _cache_ts
    db = get_db()
    new_cache: dict[str, dict] = {}
    updated = 0

    for sym in SYMBOLS:
        structure = await _compute_structure(db, sym)
        if structure:
            new_cache[sym] = structure
            updated += 1

    _cache = new_cache
    _cache_ts = _time.time()
    log.info(f"Price structure updated for {updated} symbols")


async def _compute_structure(db, symbol: str) -> dict | None:
    """Compute full price structure from stored 4h candles."""
    rows = await db.execute_fetchall(
        """SELECT ts, open, high, low, close, volume FROM ohlcv_4h
           WHERE symbol = ? ORDER BY ts DESC LIMIT ?""",
        (symbol, BACKFILL_LIMIT),
    )
    if not rows or len(rows) < 50:
        return None

    # Reverse to chronological order
    candles = list(reversed(rows))
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    current_price = closes[-1]

    # EMAs
    ema_21 = _compute_ema(closes, 21)
    ema_50 = _compute_ema(closes, 50)
    ema_200 = _compute_ema(closes, 200) if len(closes) >= 200 else None

    # ATR
    atr = _compute_atr(candles, ATR_PERIOD)
    if not atr or atr <= 0:
        return None

    # Swings (last ANALYSIS_CANDLES)
    analysis_slice = candles[-ANALYSIS_CANDLES:] if len(candles) >= ANALYSIS_CANDLES else candles
    swings = _detect_swings(analysis_slice)

    # Key levels
    key_levels = _cluster_levels(swings, atr, current_price)

    # Trend
    trend = _determine_trend(swings, current_price, ema_50)

    return {
        "trend": trend,
        "swings": swings[-20:],  # last 20 swings
        "key_levels": key_levels,
        "ema_21": round(ema_21, 6),
        "ema_50": round(ema_50, 6),
        "ema_200": round(ema_200, 6) if ema_200 else None,
        "atr_14": round(atr, 6),
        "current_price": current_price,
    }


# ── EMA ──────────────────────────────────────────────────────────────

def _compute_ema(values: list[float], period: int) -> float:
    """Standard exponential moving average."""
    if len(values) < period:
        return values[-1] if values else 0
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


# ── ATR ──────────────────────────────────────────────────────────────

def _compute_atr(candles: list[dict], period: int = 14) -> float | None:
    """Average true range over last `period` candles."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    # Use last `period` true ranges
    return sum(trs[-period:]) / period


# ── Swing detection ──────────────────────────────────────────────────

def _detect_swings(candles: list[dict]) -> list[dict]:
    """Fractal swing detection with lookback=3."""
    swings = []
    n = len(candles)
    lb = SWING_LOOKBACK

    for i in range(lb, n - lb):
        h = candles[i]["high"]
        l = candles[i]["low"]

        # Swing high: higher than all 3 neighbors on each side
        is_high = all(h > candles[i - j]["high"] for j in range(1, lb + 1)) and \
                  all(h > candles[i + j]["high"] for j in range(1, lb + 1))

        # Swing low: lower than all 3 neighbors on each side
        is_low = all(l < candles[i - j]["low"] for j in range(1, lb + 1)) and \
                 all(l < candles[i + j]["low"] for j in range(1, lb + 1))

        if is_high:
            swings.append({"ts": candles[i]["ts"], "price": h, "type": "high"})
        if is_low:
            swings.append({"ts": candles[i]["ts"], "price": l, "type": "low"})

    return swings


# ── Key level clustering ─────────────────────────────────────────────

def _cluster_levels(
    swings: list[dict], atr: float, current_price: float
) -> list[dict]:
    """Cluster nearby swing points into key levels."""
    if not swings:
        return []

    threshold = CLUSTER_ATR_MULT * atr
    prices = sorted([s["price"] for s in swings])

    clusters: list[list[float]] = []
    current_cluster: list[float] = [prices[0]]

    for p in prices[1:]:
        if p - current_cluster[-1] <= threshold:
            current_cluster.append(p)
        else:
            clusters.append(current_cluster)
            current_cluster = [p]
    clusters.append(current_cluster)

    levels = []
    for cluster in clusters:
        avg_price = sum(cluster) / len(cluster)
        touches = len(cluster)
        level_type = "support" if avg_price < current_price else "resistance"
        levels.append({
            "price": round(avg_price, 6),
            "type": level_type,
            "touches": touches,
        })

    # Sort by distance from current price (nearest first)
    levels.sort(key=lambda x: abs(x["price"] - current_price))
    return levels


# ── Trend determination ──────────────────────────────────────────────

def _determine_trend(
    swings: list[dict], current_price: float, ema_50: float
) -> str:
    """Determine trend from swing sequence + EMA confirmation."""
    # Need at least 4 swings
    if len(swings) < 4:
        return "range"

    # Get last 4+ alternating swings
    recent_highs = [s["price"] for s in swings if s["type"] == "high"][-3:]
    recent_lows = [s["price"] for s in swings if s["type"] == "low"][-3:]

    if len(recent_highs) < 2 or len(recent_lows) < 2:
        return "range"

    hh = recent_highs[-1] > recent_highs[-2]  # higher high
    hl = recent_lows[-1] > recent_lows[-2]     # higher low
    lh = recent_highs[-1] < recent_highs[-2]   # lower high
    ll = recent_lows[-1] < recent_lows[-2]     # lower low

    # Swing structure
    if hh and hl:
        swing_trend = "up"
    elif lh and ll:
        swing_trend = "down"
    else:
        swing_trend = "range"

    # EMA confirmation
    ema_trend = "up" if current_price > ema_50 else "down"

    # Both agree → confirmed, otherwise → range
    if swing_trend == ema_trend:
        return swing_trend
    if swing_trend != "range":
        return swing_trend  # trust swings over EMA
    return "range"
