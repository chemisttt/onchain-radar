import json
from fastapi import APIRouter, HTTPException
import aiohttp

from db import get_db
from models import WatchlistItem
from services import dexscreener

router = APIRouter()


@router.get("/watchlist")
async def get_watchlist():
    db = get_db()
    rows = await db.execute_fetchall("SELECT * FROM watchlist ORDER BY added_at DESC")
    return [dict(r) for r in rows]


@router.post("/watchlist")
async def add_to_watchlist(item: WatchlistItem):
    db = get_db()
    try:
        await db.execute(
            """INSERT INTO watchlist (chain, address, symbol, name, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (item.chain, item.address.lower(), item.symbol, item.name, item.notes),
        )
        await db.commit()
    except Exception:
        raise HTTPException(400, "Already in watchlist")

    rows = await db.execute_fetchall(
        "SELECT * FROM watchlist WHERE chain = ? AND address = ?",
        (item.chain, item.address.lower()),
    )
    return dict(rows[0]) if rows else {}


@router.delete("/watchlist/{item_id}")
async def remove_from_watchlist(item_id: int):
    db = get_db()
    await db.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))
    await db.commit()
    return {"deleted": item_id}


@router.get("/watchlist/prices")
async def get_watchlist_prices():
    """Batch fetch current prices for all watchlist items."""
    db = get_db()
    rows = await db.execute_fetchall("SELECT chain, address, symbol FROM watchlist")
    if not rows:
        return {}

    prices = {}
    async with aiohttp.ClientSession() as session:
        for row in rows:
            addr = row["address"]
            try:
                pairs = await dexscreener.get_token_pairs(session, addr)
                if pairs:
                    chain_pairs = [p for p in pairs if p.get("chainId") == row["chain"]]
                    best = chain_pairs[0] if chain_pairs else pairs[0]
                    prices[addr] = {
                        "price_usd": best.get("priceUsd"),
                        "price_change_24h": (best.get("priceChange", {}) or {}).get("h24"),
                        "volume_24h": (best.get("volume", {}) or {}).get("h24"),
                    }
            except Exception:
                continue

    return prices
