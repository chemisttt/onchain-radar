import json
import time
from fastapi import APIRouter, Query
from db import get_db
from services import funding_service

router = APIRouter()

# Round-trip fees by exchange (maker + taker)
EXCHANGE_FEES: dict[str, float] = {
    "Binance": 0.0007,
    "Bybit": 0.00075,
    "OKX": 0.0008,
    "MEXC": 0.001,
    "Hyperliquid": 0.00035,
    "Paradex": 0.0006,
    "Lighter": 0.0005,
    "Extended": 0.0007,
    "EdgeX": 0.0006,
    "Aster": 0.0007,
    "Variational": 0.0006,
}


@router.get("/funding/rates")
async def get_funding_rates(
    symbol: str | None = Query(None),
    sort: str = Query("rate", pattern="^(rate|apr|countdown)$"),
    min_rate: float = Query(0.0),
    limit: int = Query(100, ge=1, le=500),
):
    """Get latest funding rates, optionally filtered and sorted."""
    rates = await funding_service.fetch_all_rates()
    if symbol:
        symbol_upper = symbol.upper()
        rates = [r for r in rates if symbol_upper in r["symbol"].upper()]

    # Group by symbol, show all exchanges
    grouped: dict[str, dict] = {}
    for r in rates:
        sym = r["symbol"]
        if sym not in grouped:
            grouped[sym] = {"symbol": sym, "rates": {}}
        grouped[sym]["rates"][r["exchange"]] = {
            "rate": r["rate"],
            "apr": r["rate"] * 3 * 365,
            "settlement_hours": r.get("settlement_hours", 8),
            "next_funding_time": r.get("next_funding_time"),
        }

    result = list(grouped.values())

    # Filter by minimum absolute rate
    if min_rate > 0:
        result = [
            row for row in result
            if max(abs(v["rate"]) for v in row["rates"].values()) >= min_rate
        ]

    # Compute nearest countdown for each row (ms until next funding)
    now_ms = int(time.time() * 1000)
    for row in result:
        nearest = None
        for v in row["rates"].values():
            nft = v.get("next_funding_time")
            if nft and nft > now_ms:
                remaining = nft - now_ms
                if nearest is None or remaining < nearest:
                    nearest = remaining
        row["next_funding_ms"] = nearest

    # Sort
    if sort == "countdown":
        result.sort(key=lambda x: x.get("next_funding_ms") or float("inf"))
    elif sort == "apr":
        result.sort(key=lambda x: max(abs(v["apr"]) for v in x["rates"].values()), reverse=True)
    else:  # rate (default)
        result.sort(key=lambda x: max(abs(v["rate"]) for v in x["rates"].values()), reverse=True)

    return result[:limit]


@router.get("/funding/spreads")
async def get_funding_spreads(
    min_spread: float = Query(0.0001),
    position_size: float = Query(1000),
    limit: int = Query(50, ge=1, le=200),
    only_positive: bool = Query(False),
):
    """
    Compute funding rate arbitrage spreads across all exchanges.
    Returns symbols available on 2+ exchanges with long/short recommendations.
    """
    rates = await funding_service.fetch_all_rates()

    # Group by symbol
    by_symbol: dict[str, list[dict]] = {}
    for r in rates:
        by_symbol.setdefault(r["symbol"], []).append(r)

    result = []
    for symbol, sym_rates in by_symbol.items():
        if len(sym_rates) < 2:
            continue

        min_r = min(sym_rates, key=lambda x: x["rate"])
        max_r = max(sym_rates, key=lambda x: x["rate"])
        spread = max_r["rate"] - min_r["rate"]

        if spread < min_spread:
            continue
        if only_positive and spread <= 0:
            continue

        # Estimate daily USD profit: spread × 3 fundings/day × position_size
        est_daily_usd = spread * 3 * position_size

        # Round-trip fees (open+close on both sides)
        long_fee = EXCHANGE_FEES.get(min_r["exchange"], 0.001)
        short_fee = EXCHANGE_FEES.get(max_r["exchange"], 0.001)
        fees_pct = long_fee + short_fee
        # Amortize fees over 7 days (typical hold)
        fees_daily = (fees_pct * position_size * 2) / 7

        net_daily = est_daily_usd - fees_daily

        # Aggregate OI and volume from exchanges that provide it
        total_oi = sum(r.get("open_interest", 0) or 0 for r in sym_rates)
        total_vol = sum(r.get("volume_24h", 0) or 0 for r in sym_rates)

        result.append({
            "symbol": symbol,
            "long_exchange": min_r["exchange"],
            "long_rate": min_r["rate"],
            "short_exchange": max_r["exchange"],
            "short_rate": max_r["rate"],
            "spread": spread,
            "spread_pct": spread * 100,
            "est_daily_usd": round(est_daily_usd, 2),
            "fees_pct": round(fees_pct * 100, 4),
            "fees_daily": round(fees_daily, 2),
            "net_daily": round(net_daily, 2),
            "exchanges_count": len(sym_rates),
            "open_interest": round(total_oi, 2),
            "volume_24h": round(total_vol, 2),
            "all_rates": {r["exchange"]: r["rate"] for r in sym_rates},
        })

    result.sort(key=lambda x: x["net_daily"], reverse=True)
    return result[:limit]


@router.get("/funding/history")
async def get_funding_history(
    symbol: str = Query(...),
    hours: int = Query(168, ge=1, le=720),
):
    """
    Historical funding rates for a symbol, downsampled to 1 point per hour per exchange.
    Reads from funding_snapshots table.
    """
    db = get_db()

    rows = await db.execute_fetchall(
        """
        SELECT symbol, exchange, rate, fetched_at
        FROM funding_snapshots
        WHERE symbol = ?
          AND fetched_at >= datetime('now', ? || ' hours')
        ORDER BY fetched_at ASC
        """,
        (symbol.upper(), f"-{hours}"),
    )

    if not rows:
        return {"symbol": symbol, "hours": hours, "data": []}

    # Downsample: 1 point per hour per exchange (take last value in each hour bucket)
    buckets: dict[str, dict[str, dict]] = {}  # hour_key → exchange → {rate, time}
    for row in rows:
        fetched = row["fetched_at"]
        # Truncate to hour: "2026-03-04 12:34:56" → "2026-03-04 12:00"
        hour_key = fetched[:13] + ":00"
        exchange = row["exchange"]
        if hour_key not in buckets:
            buckets[hour_key] = {}
        # Last write wins (most recent snapshot in that hour)
        buckets[hour_key][exchange] = {
            "rate": row["rate"],
            "time": fetched,
        }

    # Flatten to time series
    data = []
    for hour_key in sorted(buckets.keys()):
        point = {"time": hour_key, "rates": {}}
        for exchange, info in buckets[hour_key].items():
            point["rates"][exchange] = info["rate"]
        data.append(point)

    return {"symbol": symbol, "hours": hours, "data": data}
