import json
from datetime import datetime, timezone
from fastapi import APIRouter, Query
import aiohttp

from db import get_db
from services import dexscreener

router = APIRouter()

CACHE_TTL_SECONDS = 300  # 5 min


def _extract_pair_data(best: dict, total_pairs: int) -> dict:
    return {
        "pair_address": best.get("pairAddress"),
        "dex": best.get("dexId"),
        "base_token": best.get("baseToken", {}),
        "quote_token": best.get("quoteToken", {}),
        "price_usd": best.get("priceUsd"),
        "price_native": best.get("priceNative"),
        "volume": best.get("volume", {}),
        "price_change": best.get("priceChange", {}),
        "liquidity": best.get("liquidity", {}),
        "fdv": best.get("fdv"),
        "market_cap": best.get("marketCap"),
        "txns": best.get("txns", {}),
        "pair_created_at": best.get("pairCreatedAt"),
        "url": best.get("url"),
        "info": best.get("info", {}),
        "all_pairs_count": total_pairs,
    }


@router.get("/tokens/{chain}/{address}")
async def get_token(chain: str, address: str, pair_address: str | None = Query(None)):
    db = get_db()

    cache_key = address.lower() if address != "_" else (pair_address or "").lower()

    # Check cache
    row = await db.execute_fetchall(
        "SELECT data, fetched_at FROM token_cache WHERE chain = ? AND address = ?",
        (chain, cache_key),
    )
    if row:
        fetched = datetime.fromisoformat(row[0]["fetched_at"])
        age = (datetime.now(timezone.utc) - fetched.replace(tzinfo=timezone.utc)).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return {"chain": chain, "address": cache_key, "data": json.loads(row[0]["data"]), "cached": True}

    async with aiohttp.ClientSession() as session:
        # Primary: search by token address
        if address and address != "_":
            pairs = await dexscreener.get_token_pairs(session, address)
        else:
            pairs = []

        # Fallback: search by pair address if no token results
        if not pairs and pair_address:
            pair_data = await dexscreener.get_pair(session, chain, pair_address)
            if pair_data and pair_data.get("pairAddress"):
                pairs = [pair_data]

    if not pairs:
        return {"chain": chain, "address": cache_key, "data": {}, "cached": False}

    # Find best pair for this chain (highest liquidity)
    chain_pairs = [p for p in pairs if p.get("chainId") == chain]
    if not chain_pairs:
        chain_pairs = pairs

    best = max(chain_pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
    data = _extract_pair_data(best, len(pairs))

    # Save to cache
    await db.execute(
        """INSERT OR REPLACE INTO token_cache (chain, address, data, fetched_at)
           VALUES (?, ?, ?, datetime('now'))""",
        (chain, cache_key, json.dumps(data)),
    )
    await db.commit()

    return {"chain": chain, "address": cache_key, "data": data, "cached": False}
