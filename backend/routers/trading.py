from fastapi import APIRouter
from db import get_db
from services import trading_service

router = APIRouter()


@router.get("/trading/positions")
async def get_positions():
    """Open positions."""
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM trades WHERE status='open' ORDER BY opened_at")
    return [dict(r) for r in rows]


@router.get("/trading/history")
async def get_history(limit: int = 50):
    """Closed trades."""
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM trades WHERE status='closed' ORDER BY closed_at DESC LIMIT ?",
        (limit,))
    return [dict(r) for r in rows]


@router.post("/trading/close/{trade_id}")
async def close_position(trade_id: int):
    """Manually close a trade."""
    return await trading_service.close_trade_manual(trade_id)


@router.get("/trading/stats")
async def get_stats():
    """Trading summary stats."""
    return await trading_service.get_stats()
