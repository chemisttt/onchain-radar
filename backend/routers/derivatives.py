from fastapi import APIRouter, Query
from services import derivatives_service, options_service, liquidation_service, orderbook_service

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


@router.get("/derivatives/liquidation-map/{symbol}")
async def get_liquidation_map(symbol: str):
    """Theoretical liquidation levels + recent WS events."""
    return await liquidation_service.get_liquidation_map(symbol=symbol)


@router.get("/derivatives/orderbook")
async def get_orderbook():
    """Orderbook depth and skew for all symbols."""
    return await orderbook_service.get_orderbook_data()


@router.get("/derivatives/{symbol}")
async def get_symbol(
    symbol: str,
    days: int = Query(365, ge=1, le=730),
):
    """Detailed history + latest stats for a single symbol."""
    return await derivatives_service.get_symbol_detail(symbol=symbol, days=days)
