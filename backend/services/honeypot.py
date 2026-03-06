import aiohttp
from services.rate_limiter import acquire

URL = "https://api.honeypot.is/v2/IsHoneypot"

CHAIN_MAP = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "base": 8453,
    "arbitrum": 42161,
}


async def check(session: aiohttp.ClientSession, chain: str, address: str) -> dict:
    chain_id = CHAIN_MAP.get(chain)
    if not chain_id:
        return {}

    await acquire("honeypot")
    async with session.get(
        URL,
        params={"address": address, "chainID": chain_id},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status != 200:
            return {}
        data = await resp.json()

    hp = data.get("honeypotResult", {})
    sim = data.get("simulationResult", {})

    return {
        "is_honeypot": hp.get("isHoneypot", False),
        "honeypot_reason": hp.get("honeypotReason", ""),
        "buy_tax": float(sim.get("buyTax", 0)),
        "sell_tax": float(sim.get("sellTax", 0)),
        "buy_gas": int(sim.get("buyGas", 0)),
        "sell_gas": int(sim.get("sellGas", 0)),
    }
