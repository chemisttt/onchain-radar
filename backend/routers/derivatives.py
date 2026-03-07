from fastapi import APIRouter, Query
from services import derivatives_service, options_service, liquidation_service, orderbook_service, momentum_service
from services import price_service, backtest_service
from db import get_db

router = APIRouter()


@router.get("/derivatives/screener")
async def get_screener(
    sort: str = Query("oi_zscore"),
    limit: int = Query(50, ge=1, le=100),
):
    """Screener: all symbols with z-scores, sorted by extremes."""
    return await derivatives_service.get_screener(sort=sort, limit=limit)


@router.get("/derivatives/global")
async def get_global(
    days: int = Query(365, ge=1, le=730),
):
    """Global aggregated data: OI, liquidations, performance, funding heatmap."""
    return await derivatives_service.get_global_data(days=days)


@router.get("/derivatives/momentum/{symbol}")
async def get_momentum(
    symbol: str,
    days: int = Query(365, ge=1, le=730),
):
    """Momentum data: Price/IV/RV + 25d Skew Z-Score."""
    return await options_service.get_momentum_data(symbol=symbol, days=days)


@router.get("/derivatives/momentum-page/{symbol}")
async def get_momentum_page(
    symbol: str,
    days: int = Query(365, ge=1, le=730),
):
    """Full momentum page: metrics, DI/VR series, scatter plots, price distribution."""
    return await momentum_service.get_momentum_page(symbol=symbol, days=days)


@router.get("/derivatives/liquidation-map/{symbol}")
async def get_liquidation_map(symbol: str):
    """Theoretical liquidation levels + recent WS events."""
    return await liquidation_service.get_liquidation_map(symbol=symbol)


@router.get("/derivatives/orderbook")
async def get_orderbook():
    """Orderbook depth and skew for all symbols."""
    return await orderbook_service.get_orderbook_data()


@router.get("/derivatives/backtest/{symbol}")
async def get_backtest(
    symbol: str,
    range: str = Query("1M", pattern="^(1W|1M|3M)$"),
    timeframe: str = Query("1d", pattern="^(1d|4h|mtf)$"),
):
    """Backtest view: 4h candles + fired alerts + price structure."""
    candle_limits = {"1W": 42, "1M": 180, "3M": 540}
    limit = candle_limits[range]

    db = get_db()

    # Candles from ohlcv_4h, ts ms → seconds for lightweight-charts
    rows = await db.execute_fetchall(
        """SELECT ts, open, high, low, close, volume FROM ohlcv_4h
           WHERE symbol = ? ORDER BY ts DESC LIMIT ?""",
        (symbol, limit),
    )
    candles = [
        {
            "time": r["ts"] // 1000,
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
        }
        for r in reversed(rows)
    ]

    # Real alerts from alert_tracking
    min_ts = candles[0]["time"] if candles else 0
    alert_rows = await db.execute_fetchall(
        """SELECT alert_type, symbol, tier, confluence, fired_at,
                  entry_price, expected_direction,
                  price_1d, price_3d, price_7d,
                  return_1d, return_3d, return_7d
           FROM alert_tracking
           WHERE symbol = ? AND fired_at >= datetime(?, 'unixepoch')
           ORDER BY fired_at""",
        (symbol, min_ts),
    )
    real_alerts = []
    for a in alert_rows:
        from datetime import datetime as _dt
        fired_dt = _dt.fromisoformat(a["fired_at"])
        fired_ts = int(fired_dt.timestamp())
        snapped = (fired_ts // 14400) * 14400
        real_alerts.append({
            "time": snapped,
            "type": a["alert_type"],
            "tier": a["tier"],
            "confluence": a["confluence"],
            "fired_at": a["fired_at"],
            "entry_price": a["entry_price"],
            "direction": a["expected_direction"],
            "price_1d": a["price_1d"],
            "price_3d": a["price_3d"],
            "price_7d": a["price_7d"],
            "return_1d": a["return_1d"],
            "return_3d": a["return_3d"],
            "return_7d": a["return_7d"],
            "simulated": False,
            "timeframe": "1d",
        })

    sim_days = {"1W": 7, "1M": 30, "3M": 90}

    # Simulated alerts based on timeframe
    simulated_1d = []
    simulated_4h = []

    if timeframe in ("1d", "mtf"):
        simulated_1d = await backtest_service.simulate_alerts(symbol, days=sim_days[range])
        for a in simulated_1d:
            a["timeframe"] = "1d"

    if timeframe in ("4h", "mtf"):
        simulated_4h = await backtest_service.simulate_alerts_4h(symbol, days=sim_days[range])

    # MTF tier upgrade: 4h confirms 1d
    if timeframe == "mtf" and simulated_4h and simulated_1d:
        simulated_1d = backtest_service._apply_mtf_upgrade(simulated_4h, simulated_1d)

    # Merge based on timeframe mode
    if timeframe == "1d":
        simulated = simulated_1d
    elif timeframe == "4h":
        simulated = simulated_4h
    else:  # mtf — both
        simulated = simulated_4h + simulated_1d

    # Filter simulated to chart time range
    if candles:
        min_time = candles[0]["time"]
        max_time = candles[-1]["time"]
        simulated = [a for a in simulated if min_time <= a["time"] <= max_time]

    # Merge and sort by time
    all_alerts = real_alerts + simulated
    all_alerts.sort(key=lambda a: a["time"])

    # Stats helper — MAE > 5% means you got stopped out, it's a loss
    MAE_STOP = 5.0  # max adverse excursion threshold (%)

    def _directional_return(a: dict) -> float | None:
        ret = a.get("return_7d") or a.get("return_3d") or a.get("return_1d")
        if ret is None:
            return None
        if a.get("direction") == "short":
            ret = -ret
        return ret

    def _is_win(a: dict) -> bool:
        """Win = positive return AND didn't get stopped out by MAE."""
        ret = _directional_return(a)
        if ret is None or ret <= 0:
            return False
        mae = a.get("mae_return")
        if mae is not None and mae > MAE_STOP:
            return False  # stopped out — can't count as win
        return True

    with_returns = [a for a in all_alerts if _directional_return(a) is not None]
    wins = sum(1 for a in with_returns if _is_win(a))
    total_return = sum(_directional_return(a) or 0 for a in with_returns)

    # Per-type breakdown
    type_stats: dict[str, dict] = {}
    for a in all_alerts:
        t = a["type"]
        if t not in type_stats:
            type_stats[t] = {"count": 0, "wins": 0, "returns": []}
        type_stats[t]["count"] += 1
        ret = _directional_return(a)
        if ret is not None:
            type_stats[t]["returns"].append(ret)
            if _is_win(a):
                type_stats[t]["wins"] += 1

    by_type = {}
    for t, ts in type_stats.items():
        rets = ts["returns"]
        n = len(rets)
        gains = sum(r for r in rets if r > 0)
        losses = abs(sum(r for r in rets if r < 0))
        by_type[t] = {
            "count": ts["count"],
            "win_rate": round(ts["wins"] / n * 100, 1) if n > 0 else 0,
            "avg_return": round(sum(rets) / n, 2) if n > 0 else 0,
            "pf": round(gains / losses, 2) if losses > 0 else (99.0 if gains > 0 else 0),
        }

    stats = {
        "total_signals": len(all_alerts),
        "real_signals": len(real_alerts),
        "simulated_signals": len(simulated),
        "with_returns": len(with_returns),
        "wins": wins,
        "win_rate": round(wins / len(with_returns) * 100, 1) if with_returns else 0,
        "avg_return": round(total_return / len(with_returns), 2) if with_returns else 0,
        "by_type": by_type,
    }

    # Price structure
    structure = price_service.get_price_structure(symbol)

    return {
        "candles": candles,
        "alerts": all_alerts,
        "structure": structure,
        "stats": stats,
    }


@router.get("/derivatives/{symbol}")
async def get_symbol(
    symbol: str,
    days: int = Query(365, ge=1, le=730),
):
    """Detailed history + latest stats for a single symbol."""
    return await derivatives_service.get_symbol_detail(symbol=symbol, days=days)
