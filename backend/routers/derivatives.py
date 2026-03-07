from fastapi import APIRouter, Query
from services import derivatives_service, options_service, liquidation_service, orderbook_service, momentum_service
from services import price_service
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

    # Alerts from alert_tracking — snap fired_at to nearest 4h candle
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
    alerts = []
    for a in alert_rows:
        from datetime import datetime as _dt
        fired_dt = _dt.fromisoformat(a["fired_at"])
        fired_ts = int(fired_dt.timestamp())
        # Snap to nearest 4h boundary (14400s)
        snapped = (fired_ts // 14400) * 14400
        alerts.append({
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
        })

    # Price structure (key_levels, EMAs, trend)
    structure = price_service.get_price_structure(symbol)

    return {
        "candles": candles,
        "alerts": alerts,
        "structure": structure,
    }


@router.get("/derivatives/{symbol}")
async def get_symbol(
    symbol: str,
    days: int = Query(365, ge=1, le=730),
):
    """Detailed history + latest stats for a single symbol."""
    return await derivatives_service.get_symbol_detail(symbol=symbol, days=days)
