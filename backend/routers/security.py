import asyncio
import json
from datetime import datetime, timezone
from fastapi import APIRouter
import aiohttp

from db import get_db
from services import goplus, honeypot, rugcheck

router = APIRouter()

CACHE_TTL_SECONDS = 900  # 15 min


@router.get("/security/{chain}/{address}")
async def get_security(chain: str, address: str):
    db = get_db()

    # Check cache
    row = await db.execute_fetchall(
        "SELECT goplus, honeypot, rugcheck, fetched_at FROM security_cache WHERE chain = ? AND address = ?",
        (chain, address.lower()),
    )
    if row:
        fetched = datetime.fromisoformat(row[0]["fetched_at"])
        age = (datetime.now(timezone.utc) - fetched.replace(tzinfo=timezone.utc)).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return {
                "chain": chain,
                "address": address,
                "goplus": json.loads(row[0]["goplus"]),
                "honeypot": json.loads(row[0]["honeypot"]),
                "rugcheck": json.loads(row[0]["rugcheck"]),
                "cached": True,
            }

    # Fetch in parallel
    async with aiohttp.ClientSession() as session:
        tasks = {}

        if chain == "solana":
            tasks["goplus"] = goplus.check_solana(session, address)
            tasks["rugcheck"] = rugcheck.check(session, address)
        else:
            tasks["goplus"] = goplus.check_evm(session, chain, address)
            tasks["honeypot"] = honeypot.check(session, chain, address)

        results = {}
        for key, coro in tasks.items():
            try:
                results[key] = await coro
            except Exception:
                results[key] = {}

    gp = results.get("goplus", {})
    hp = results.get("honeypot", {})
    rc = results.get("rugcheck", {})

    # Save to cache
    await db.execute(
        """INSERT OR REPLACE INTO security_cache (chain, address, goplus, honeypot, rugcheck, fetched_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))""",
        (chain, address.lower(), json.dumps(gp), json.dumps(hp), json.dumps(rc)),
    )
    await db.commit()

    return {
        "chain": chain,
        "address": address,
        "goplus": gp,
        "honeypot": hp,
        "rugcheck": rc,
        "cached": False,
    }
