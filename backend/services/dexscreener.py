import aiohttp
from services.rate_limiter import acquire

BASE = "https://api.dexscreener.com"


async def get_token_profiles(session: aiohttp.ClientSession) -> list[dict]:
    """Latest token profiles (boosted tokens)."""
    await acquire("dexscreener")
    async with session.get(f"{BASE}/token-profiles/latest/v1", timeout=aiohttp.ClientTimeout(total=10)) as resp:
        if resp.status != 200:
            return []
        data = await resp.json()
        return data if isinstance(data, list) else []


async def get_latest_pairs(session: aiohttp.ClientSession) -> list[dict]:
    """Latest boosted tokens (new pairs with boosts)."""
    await acquire("dexscreener")
    async with session.get(f"{BASE}/token-boosts/latest/v1", timeout=aiohttp.ClientTimeout(total=10)) as resp:
        if resp.status != 200:
            return []
        data = await resp.json()
        return data if isinstance(data, list) else []


async def get_token_pairs(session: aiohttp.ClientSession, address: str) -> list[dict]:
    """All pairs for a token address (any chain)."""
    await acquire("dexscreener")
    async with session.get(f"{BASE}/tokens/v1/{address}", timeout=aiohttp.ClientTimeout(total=10)) as resp:
        if resp.status != 200:
            return []
        data = await resp.json()
        return data if isinstance(data, list) else data.get("pairs", [])


async def get_pair(session: aiohttp.ClientSession, chain: str, pair_address: str) -> dict:
    """Get specific pair details."""
    await acquire("dexscreener")
    async with session.get(
        f"{BASE}/pairs/v1/{chain}/{pair_address}",
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status != 200:
            return {}
        data = await resp.json()
        if isinstance(data, list):
            return data[0] if data else {}
        pair = data.get("pair") or data.get("pairs")
        if isinstance(pair, list):
            return pair[0] if pair else {}
        return pair or data


async def search_tokens(session: aiohttp.ClientSession, query: str) -> list[dict]:
    """Search by token name/symbol."""
    await acquire("dexscreener")
    async with session.get(
        f"{BASE}/latest/dex/search",
        params={"q": query},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status != 200:
            return []
        data = await resp.json()
        return data.get("pairs", [])
