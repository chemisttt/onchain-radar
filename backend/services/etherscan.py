import aiohttp
from config import settings
from services.rate_limiter import acquire

BASE = "https://api.etherscan.io/v2/api"

CHAIN_NAMES = {
    1: "ethereum",
    56: "bsc",
    137: "polygon",
    42161: "arbitrum",
    8453: "base",
}

# Known protocol addresses (lowercase) → label
KNOWN_PROTOCOLS: dict[str, str] = {
    # DEXes
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d": "Uniswap V2 Router",
    "0xe592427a0aece92de3edee1f18e0157c05861564": "Uniswap V3 Router",
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45": "Uniswap Universal Router",
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad": "Uniswap Universal Router V2",
    "0xdef1c0ded9bec7f1a1670819833240f027b25eff": "0x Exchange Proxy",
    "0x1111111254eeb25477b68fb85ed929f73a960582": "1inch V5",
    "0x111111125421ca6dc452d289314280a0f8842a65": "1inch V6",
    "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f": "SushiSwap Router",
    # Lending
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2": "Aave V3 Pool",
    "0x7d2768de32b0b80b7a3454c06bdac94a69ddc7a9": "Aave V2 Pool",
    "0xa17581a9e3356d9a858b789d68b4d866e593ae94": "Compound V3 (WETH)",
    "0xc3d688b66703497daa19211eedff47f25384cdc3": "Compound V3 (USDC)",
    # Staking / Liquid Staking
    "0xae7ab96520de3a18e5e111b5eaab095312d7fe84": "Lido stETH",
    "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": "Lido wstETH",
    "0xbe9895146f7af43049ca1c1ae358b0541ea49704": "Coinbase cbETH",
    "0xae78736cd615f374d3085123a210448e74fc6393": "Rocket Pool rETH",
    # Bridges
    "0x3ee18b2214aff97000d974cf647e7c347e8fa585": "Wormhole Bridge",
    "0x40ec5b33f54e0e8a33a975908c5ba1c14e5bbbdf": "Polygon Bridge",
    "0x4dbd4fc535ac27206064b68ffcf827b0a60bab3f": "Arbitrum Bridge",
    "0x99c9fc46f92e8a1c0dec1b1747d010903e884be1": "Optimism Bridge",
    "0x3154cf16ccdb4c6d922629664174b904d80f2c35": "Base Bridge",
    "0xabea9132b05a70803a4e85094fd0e1800777fbef": "zkSync Bridge",
    "0xd19d4b5d358258f05d7b411e21a1460d11b0876f": "Across Bridge",
    "0x5427fefa711eff984124bfbb1ab6fbf5e3da1820": "Stargate Bridge",
    # CEX hot wallets
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance Hot Wallet",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance Hot Wallet 2",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance Hot Wallet 3",
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": "Binance Cold Wallet",
    "0x974caa59e49682cda0ad2bbe82983419a2ecc400": "Coinbase",
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": "Coinbase 10",
    "0x1ab4973a48dc892cd9971ece8e01dcc7688f8f23": "Kraken Hot Wallet",
    "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0": "Kraken Cold Wallet",
    "0x2faf487a4414fe77e2327f0bf4ae2a264a776ad2": "FTX (Alameda)",
    # Misc
    "0xba12222222228d8ba445958a75a0704d566bf2c8": "Balancer Vault",
    "0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7": "Curve 3pool",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC Contract",
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT Contract",
}


def _label_address(addr: str) -> str:
    """Return known protocol/wallet label or truncated address."""
    if not addr:
        return "?"
    return KNOWN_PROTOCOLS.get(addr.lower(), addr[:10] + "...")


async def _call(session: aiohttp.ClientSession, chain_id: int, module: str, action: str, **kwargs) -> dict | list:
    await acquire("etherscan")
    params = {
        "chainid": chain_id,
        "module": module,
        "action": action,
        "apikey": settings.etherscan_api_key or "",
        **kwargs,
    }
    async with session.get(BASE, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        if resp.status != 200:
            return {}
        data = await resp.json()

        # proxy module returns jsonrpc format (no "status" field)
        if module == "proxy":
            return data.get("result", {})

        # standard modules return {"status": "1", "result": ...}
        if data.get("status") == "1":
            return data.get("result", {})
        return {}


async def get_large_transfers(session: aiohttp.ClientSession, chain_id: int = 1) -> list[dict]:
    """Get recent large ETH transfers (last ~100 blocks)."""
    result = await _call(session, chain_id, "proxy", "eth_blockNumber")
    if not result or not isinstance(result, str):
        return []

    latest_block = int(result, 16)
    start_block = latest_block - 100

    txns = await _call(
        session, chain_id, "account", "txlistinternal",
        startblock=str(start_block),
        endblock=str(latest_block),
        sort="desc",
    )
    if not isinstance(txns, list):
        return []

    # Sort by value descending — internal txns can be 10k+ entries
    txns.sort(key=lambda x: int(x.get("value", "0")), reverse=True)

    chain_name = CHAIN_NAMES.get(chain_id, "ethereum")
    transfers = []
    for tx in txns[:50]:
        value_wei = int(tx.get("value", "0"))
        value_eth = value_wei / 1e18
        if value_eth >= 50:  # ~$100k+ at current ETH price
            from_addr = tx.get("from", "")
            to_addr = tx.get("to", "")
            from_label = _label_address(from_addr)
            to_label = _label_address(to_addr)
            transfers.append({
                "event_type": "WHALE_TRANSFER",
                "chain": chain_name,
                "token_address": None,
                "pair_address": None,
                "token_symbol": "ETH",
                "severity": "warning" if value_eth < 500 else "critical",
                "details": {
                    "from": from_addr,
                    "to": to_addr,
                    "from_label": from_label,
                    "to_label": to_label,
                    "value_eth": round(value_eth, 4),
                    "tx_hash": tx.get("hash"),
                    "block": tx.get("blockNumber"),
                },
            })

    return transfers


async def get_contract_source(session: aiohttp.ClientSession, chain_id: int, address: str) -> dict:
    """Get verified contract source code."""
    result = await _call(session, chain_id, "contract", "getsourcecode", address=address)
    if isinstance(result, list) and result:
        return result[0]
    return {}


async def get_bytecode(session: aiohttp.ClientSession, chain_id: int, address: str) -> str:
    """Get runtime bytecode via eth_getCode."""
    result = await _call(session, chain_id, "proxy", "eth_getCode",
                         address=address, tag="latest")
    return result if isinstance(result, str) else ""
