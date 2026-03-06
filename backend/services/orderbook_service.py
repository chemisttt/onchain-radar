import asyncio
import logging
from collections import deque

import aiohttp

from services.derivatives_service import SYMBOLS

log = logging.getLogger("orderbook")

_task: asyncio.Task | None = None

POLL_INTERVAL = 30  # seconds
HISTORY_SIZE = 288  # 24h at 30s intervals

# In-memory cache
_ob_cache: dict[str, dict] = {}
_ob_skew_history: dict[str, deque] = {}


# ── OB Depth Fetch ───────────────────────────────────────────────────

async def _fetch_all_depth(session: aiohttp.ClientSession):
    """Fetch orderbook depth for all symbols from Binance futures."""
    global _ob_cache

    async def _fetch_one(sym: str):
        try:
            # Use the original Binance symbol for API call
            from services.derivatives_service import BINANCE_SYMBOL_MAP, _binance_sym
            bn_sym = _binance_sym(sym)

            async with session.get(
                "https://fapi.binance.com/fapi/v1/depth",
                params={"symbol": bn_sym, "limit": 50},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return sym, None
                data = await resp.json()

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            if not bids or not asks:
                return sym, None

            # Get mid price
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid = (best_bid + best_ask) / 2

            if mid <= 0:
                return sym, None

            # Compute depth within 2% of mid price
            bid_threshold = mid * 0.98
            ask_threshold = mid * 1.02

            bid_depth = sum(float(b[0]) * float(b[1]) for b in bids if float(b[0]) >= bid_threshold)
            ask_depth = sum(float(a[0]) * float(a[1]) for a in asks if float(a[0]) <= ask_threshold)

            total_depth = bid_depth + ask_depth
            ob_skew = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0

            return sym, {
                "ob_depth": round(total_depth, 0),
                "bid_depth": round(bid_depth, 0),
                "ask_depth": round(ask_depth, 0),
                "ob_skew": round(ob_skew, 4),
            }
        except Exception:
            return sym, None

    # Batch in groups of 10 to avoid rate limits
    for i in range(0, len(SYMBOLS), 10):
        batch = SYMBOLS[i:i + 10]
        results = await asyncio.gather(*[_fetch_one(s) for s in batch], return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                continue
            sym, data = r
            if data:
                _ob_cache[sym] = data

                # Track skew history for z-score
                if sym not in _ob_skew_history:
                    _ob_skew_history[sym] = deque(maxlen=HISTORY_SIZE)
                _ob_skew_history[sym].append(data["ob_skew"])
        await asyncio.sleep(0.2)


def _compute_skew_zscore(sym: str) -> float:
    """Compute OB skew z-score from rolling history."""
    history = _ob_skew_history.get(sym)
    if not history or len(history) < 30:
        return 0.0
    vals = list(history)
    mean = sum(vals) / len(vals)
    std = (sum((x - mean) ** 2 for x in vals) / len(vals)) ** 0.5
    if std == 0:
        return 0.0
    return round((vals[-1] - mean) / std, 4)


# ── Poll loop ────────────────────────────────────────────────────────

async def _poll_loop():
    log.info("OB depth polling started")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                await _fetch_all_depth(session)
            log.debug(f"OB depth: {len(_ob_cache)} symbols cached")
        except Exception as e:
            log.error(f"OB depth poll error: {e}")
        await asyncio.sleep(POLL_INTERVAL)


# ── API responses ────────────────────────────────────────────────────

async def get_orderbook_data() -> list[dict]:
    """Return OB depth data for all symbols."""
    result = []
    for sym in SYMBOLS:
        data = _ob_cache.get(sym, {})
        if data:
            result.append({
                "symbol": sym,
                "ob_depth_usd": data.get("ob_depth", 0),
                "ob_skew": data.get("ob_skew", 0),
                "ob_skew_zscore": _compute_skew_zscore(sym),
            })
    return result


def get_cache() -> dict[str, dict]:
    """Return raw cache dict for screener merge."""
    result = {}
    for sym, data in _ob_cache.items():
        result[sym] = {
            "ob_depth": data.get("ob_depth", 0),
            "ob_skew": data.get("ob_skew", 0),
            "ob_skew_zscore": _compute_skew_zscore(sym),
        }
    return result


# ── Service lifecycle ────────────────────────────────────────────────

def start():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll_loop())


def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
