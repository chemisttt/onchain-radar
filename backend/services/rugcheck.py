import aiohttp
from services.rate_limiter import acquire

URL = "https://api.rugcheck.xyz/v1/tokens"


async def check(session: aiohttp.ClientSession, address: str) -> dict:
    await acquire("rugcheck")
    async with session.get(
        f"{URL}/{address}/report/summary",
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status != 200:
            return {}
        data = await resp.json()

    return {
        "score": data.get("score"),
        "risks": data.get("risks", []),
        "token_name": data.get("tokenMeta", {}).get("name"),
        "token_symbol": data.get("tokenMeta", {}).get("symbol"),
        "top_holders": data.get("topHolders", []),
    }
