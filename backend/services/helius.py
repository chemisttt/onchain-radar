import aiohttp
from config import settings
from services.rate_limiter import acquire

# Known Solana protocol program IDs → label
KNOWN_PROGRAMS: dict[str, str] = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter V6",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "Jupiter V4",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP": "Orca V2",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "routeUGWgWzqBWFcrCfv8tritsqukccJPu3q5GPP3xS": "Raydium Route",
    "So1endDq2YkqhipRh3WViPa8hFvz0XP1SOJyGZW9V9c": "Solend",
    "MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA": "Marginfi",
    "jCebN34bUfdeUYJT13J1yG16XWQpt5PDx6Mse9GUqhR": "Solana Staking",
    "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX": "Serum/OpenBook",
    "TSWAPaqyCSx2KABk68Shruf4rp7CxcNi8hAsbdwmHbN": "Tensor Swap",
    "M2mx93ekt1fmXSVkTrUL9xVFHkmME8HTUi5Cyc5aF7K": "Magic Eden V2",
    "wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb": "Wormhole",
    "wooMRYmQrB9v8FCHbxVhHoxSAZUxyNMgkVJP3bQx7g9": "WooFi",
    "DjVE6JNiYqPL2QXyCUUh8rNjHrbz9hXHNYt99MQ59qw1": "Drift Protocol",
    "PhoeNiXZ8ByJGLkxNfZRnkUfjvmuYqLR89jjFHGqdXY": "Phoenix DEX",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora Pools",
}


def _label_account(addr: str) -> str:
    """Return known program/wallet label or truncated address."""
    if not addr:
        return "?"
    return KNOWN_PROGRAMS.get(addr, addr[:8] + "...")


async def get_recent_transfers(session: aiohttp.ClientSession, min_sol: float = 200) -> list[dict]:
    """Get recent large SOL transfers via Helius enhanced transactions."""
    if not settings.helius_api_key:
        return []

    await acquire("helius")
    url = f"https://api.helius.xyz/v0/addresses/So11111111111111111111111111111111111111112/transactions"
    params = {"api-key": settings.helius_api_key, "limit": 50}

    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        if resp.status != 200:
            return []
        txns = await resp.json()

    transfers = []
    for tx in txns:
        tx_type = tx.get("type", "UNKNOWN")
        source = tx.get("source", "")

        # Check native SOL transfers
        for transfer in tx.get("nativeTransfers", []):
            amount = transfer.get("amount", 0) / 1e9  # lamports → SOL
            if amount >= min_sol:
                from_addr = transfer.get("fromUserAccount", "")
                to_addr = transfer.get("toUserAccount", "")
                from_label = _label_account(from_addr)
                to_label = _label_account(to_addr)

                transfers.append({
                    "event_type": "WHALE_TRANSFER",
                    "chain": "solana",
                    "token_address": None,
                    "pair_address": None,
                    "token_symbol": "SOL",
                    "severity": "warning" if amount < 2000 else "critical",
                    "details": {
                        "from": from_addr,
                        "to": to_addr,
                        "from_label": from_label,
                        "to_label": to_label,
                        "amount_sol": round(amount, 2),
                        "tx_sig": tx.get("signature"),
                        "type": tx_type,
                        "source": source,
                    },
                })

    return transfers
