import aiohttp
from config import settings
from services.rate_limiter import acquire

EVM_URL = "https://api.gopluslabs.io/api/v1/token_security"
SOL_URL = "https://api.gopluslabs.io/api/v1/solana/token_security"

# DexScreener chainId → GoPlus chain_id
CHAIN_MAP = {
    "ethereum": "1",
    "bsc": "56",
    "polygon": "137",
    "arbitrum": "42161",
    "base": "8453",
    "avalanche": "43114",
    "optimism": "10",
}


async def check_evm(session: aiohttp.ClientSession, chain: str, address: str) -> dict:
    chain_id = CHAIN_MAP.get(chain)
    if not chain_id:
        return {}

    await acquire("goplus")
    headers = {}
    if settings.goplus_api_key:
        headers["Authorization"] = f"Bearer {settings.goplus_api_key}"

    async with session.get(
        f"{EVM_URL}/{chain_id}",
        params={"contract_addresses": address.lower()},
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status != 200:
            return {}
        data = await resp.json()

    result = data.get("result", {}).get(address.lower(), {})
    if not result:
        return {}

    return {
        "is_honeypot": result.get("is_honeypot") == "1",
        "is_mintable": result.get("is_mintable") == "1",
        "can_take_back_ownership": result.get("can_take_back_ownership") == "1",
        "hidden_owner": result.get("hidden_owner") == "1",
        "is_blacklisted": result.get("is_blacklisted") == "1",
        "cannot_sell_all": result.get("cannot_sell_all") == "1",
        "is_proxy": result.get("is_proxy") == "1",
        "buy_tax": float(result.get("buy_tax", "0") or "0"),
        "sell_tax": float(result.get("sell_tax", "0") or "0"),
        "holder_count": int(result.get("holder_count", "0") or "0"),
        "lp_holder_count": int(result.get("lp_holder_count", "0") or "0"),
        "is_open_source": result.get("is_open_source") == "1",
        "owner_address": result.get("owner_address", ""),
    }


async def check_solana(session: aiohttp.ClientSession, address: str) -> dict:
    await acquire("goplus")
    headers = {}
    if settings.goplus_api_key:
        headers["Authorization"] = f"Bearer {settings.goplus_api_key}"

    async with session.get(
        SOL_URL,
        params={"contract_addresses": address},
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status != 200:
            return {}
        data = await resp.json()

    return data.get("result", {}).get(address, {})
