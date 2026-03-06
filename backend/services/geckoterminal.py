import aiohttp
from services.rate_limiter import acquire, TokenBucket, limiters

BASE = "https://api.geckoterminal.com/api/v2"

# GeckoTerminal: 30 req/min
limiters["geckoterminal"] = TokenBucket(rate=0.4, capacity=3)

# GeckoTerminal network → DexScreener chainId
NETWORK_MAP = {
    "eth": "ethereum",
    "bsc": "bsc",
    "polygon_pos": "polygon",
    "arbitrum": "arbitrum",
    "base": "base",
    "solana": "solana",
    "avalanche": "avalanche",
    "optimism": "optimism",
}

NETWORKS = list(NETWORK_MAP.keys())


async def get_trending_pools(session: aiohttp.ClientSession, network: str) -> list[dict]:
    """Get trending pools for a specific network."""
    await acquire("geckoterminal")
    async with session.get(
        f"{BASE}/networks/{network}/trending_pools",
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status != 200:
            return []
        data = await resp.json()
    return data.get("data", [])


async def get_new_pools(session: aiohttp.ClientSession, network: str) -> list[dict]:
    """Get newest pools for a specific network."""
    await acquire("geckoterminal")
    async with session.get(
        f"{BASE}/networks/{network}/new_pools",
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status != 200:
            return []
        data = await resp.json()
    return data.get("data", [])


def parse_pool(pool: dict, network: str) -> dict | None:
    """Parse GeckoTerminal pool into a normalized event-ready dict."""
    attrs = pool.get("attributes", {})
    name = attrs.get("name", "")
    address = attrs.get("address", "")

    if not address:
        return None

    # Extract base token symbol from name (e.g. "WETH / USDC 0.05%" → "WETH")
    symbol = name.split("/")[0].strip().split(" ")[0] if name else address[:8]

    chain = NETWORK_MAP.get(network, network)

    # Extract base_token address from relationships: id format is "{network}_{address}"
    # e.g. "polygon_pos_0xABC..." or "eth_0xABC..."
    token_address = None
    try:
        base_id = pool.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
        prefix = f"{network}_"
        if base_id.startswith(prefix):
            token_address = base_id[len(prefix):]
        elif "_" in base_id:
            # Fallback: strip everything before last occurrence of 0x
            idx = base_id.rfind("0x")
            if idx >= 0:
                token_address = base_id[idx:]
    except (AttributeError, IndexError):
        pass

    # Extract DEX name from relationships
    dex_id = ""
    try:
        dex_id = pool.get("relationships", {}).get("dex", {}).get("data", {}).get("id", "")
    except (AttributeError, IndexError):
        pass

    return {
        "chain": chain,
        "pair_address": address,
        "token_address": token_address,
        "token_symbol": symbol,
        "price_usd": attrs.get("base_token_price_usd"),
        "volume_24h": (attrs.get("volume_usd") or {}).get("h24"),
        "liquidity_usd": attrs.get("reserve_in_usd"),
        "fdv": attrs.get("fdv_usd"),
        "price_change": attrs.get("price_change_percentage", {}),
        "txns": attrs.get("transactions", {}),
        "pool_created_at": attrs.get("pool_created_at"),
        "name": name,
        "dex": dex_id,
    }
