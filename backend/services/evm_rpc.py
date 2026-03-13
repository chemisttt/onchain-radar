"""Thin JSON-RPC client for EVM chains.

HTTP methods, WebSocket log subscription, TX building + signing.
No web3.py — raw aiohttp + eth_account for consistency with existing codebase.
"""

import asyncio
import json
import logging

import aiohttp
from eth_account import Account
from eth_account.signers.local import LocalAccount

from config import settings
from services.rate_limiter import acquire

log = logging.getLogger("evm_rpc")

# ---------------------------------------------------------------------------
# Chain configuration
# ---------------------------------------------------------------------------

CHAIN_CONFIG: dict[int, dict] = {
    1: {
        "name": "ethereum",
        "drpc": "ethereum",
        "alchemy": "eth",
        "gas": "eip1559",
        "weth": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "public_rpc": "https://eth.llamarpc.com",
    },
    8453: {
        "name": "base",
        "drpc": "base",
        "alchemy": "base",
        "gas": "eip1559",
        "weth": "0x4200000000000000000000000000000000000006",
        "public_rpc": "https://mainnet.base.org",
    },
    42161: {
        "name": "arbitrum",
        "drpc": "arbitrum",
        "alchemy": "arb",
        "gas": "eip1559",
        "weth": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "public_rpc": "https://arb1.arbitrum.io/rpc",
    },
    56: {
        "name": "bsc",
        "drpc": "bsc",
        "alchemy": None,
        "gas": "legacy",
        "weth": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "public_rpc": "https://bsc-dataseed1.binance.org",
    },
}


def get_rpc_url(chain_id: int) -> str:
    """Resolve HTTP RPC URL. Priority: DRPC → Alchemy → public RPC."""
    cfg = CHAIN_CONFIG.get(chain_id)
    if not cfg:
        raise ValueError(f"Unsupported chain_id: {chain_id}")

    if settings.drpc_api_key:
        return f"https://lb.drpc.org/ogrpc?network={cfg['drpc']}&dkey={settings.drpc_api_key}"
    if settings.alchemy_api_key and cfg["alchemy"]:
        return f"https://{cfg['alchemy']}-mainnet.g.alchemy.com/v2/{settings.alchemy_api_key}"
    return cfg["public_rpc"]


# ---------------------------------------------------------------------------
# HTTP JSON-RPC
# ---------------------------------------------------------------------------

_rpc_id = 0


async def rpc_call(session: aiohttp.ClientSession, rpc_url: str, method: str, params: list) -> any:
    """Generic JSON-RPC call with rate limiting."""
    global _rpc_id
    _rpc_id += 1
    await acquire("rpc")

    payload = {"jsonrpc": "2.0", "id": _rpc_id, "method": method, "params": params}
    async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        data = await resp.json()
        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")
        return data.get("result")


async def eth_call(session: aiohttp.ClientSession, rpc_url: str, tx_dict: dict) -> str:
    """Simulate transaction (dry-run). Returns hex output or raises."""
    return await rpc_call(session, rpc_url, "eth_call", [tx_dict, "latest"])


async def eth_send_raw(session: aiohttp.ClientSession, rpc_url: str, signed_hex: str) -> str:
    """Broadcast signed transaction. Returns tx hash."""
    return await rpc_call(session, rpc_url, "eth_sendRawTransaction", [signed_hex])


async def eth_get_receipt(
    session: aiohttp.ClientSession, rpc_url: str, tx_hash: str, timeout: int = 120
) -> dict | None:
    """Poll for transaction receipt until confirmed or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        receipt = await rpc_call(session, rpc_url, "eth_getTransactionReceipt", [tx_hash])
        if receipt:
            return receipt
        await asyncio.sleep(2)
    return None


async def eth_gas_price(session: aiohttp.ClientSession, rpc_url: str) -> int:
    """Get current gas price in wei."""
    result = await rpc_call(session, rpc_url, "eth_gasPrice", [])
    return int(result, 16)


async def eth_nonce(session: aiohttp.ClientSession, rpc_url: str, address: str) -> int:
    result = await rpc_call(session, rpc_url, "eth_getTransactionCount", [address, "latest"])
    return int(result, 16)


async def eth_balance(session: aiohttp.ClientSession, rpc_url: str, address: str) -> int:
    result = await rpc_call(session, rpc_url, "eth_getBalance", [address, "latest"])
    return int(result, 16)


async def eth_chain_id(session: aiohttp.ClientSession, rpc_url: str) -> int:
    result = await rpc_call(session, rpc_url, "eth_chainId", [])
    return int(result, 16)


async def eth_estimate_gas(session: aiohttp.ClientSession, rpc_url: str, tx_dict: dict) -> int:
    result = await rpc_call(session, rpc_url, "eth_estimateGas", [tx_dict])
    return int(result, 16)


# ---------------------------------------------------------------------------
# TX building + signing
# ---------------------------------------------------------------------------

def gas_params_eip1559(max_fee_gwei: float, priority_gwei: float) -> dict:
    """Gas params for EIP-1559 chains (ETH, Base, Arb)."""
    return {
        "maxFeePerGas": int(max_fee_gwei * 1e9),
        "maxPriorityFeePerGas": int(priority_gwei * 1e9),
    }


def gas_params_legacy(gas_price_gwei: float) -> dict:
    """Gas params for legacy chains (BSC)."""
    return {"gasPrice": int(gas_price_gwei * 1e9)}


def build_tx(
    to: str, data: str, value: int, nonce: int,
    chain_id: int, gas: int, gas_params: dict,
) -> dict:
    """Build unsigned transaction dict."""
    tx = {
        "to": to,
        "data": data,
        "value": value,
        "nonce": nonce,
        "chainId": chain_id,
        "gas": gas,
        **gas_params,
    }
    # EIP-1559 type
    if "maxFeePerGas" in gas_params:
        tx["type"] = 2
    return tx


def sign_tx(tx_dict: dict, private_key: str) -> str:
    """Sign transaction, return raw hex string (0x-prefixed)."""
    acct: LocalAccount = Account.from_key(private_key)
    signed = acct.sign_transaction(tx_dict)
    return signed.raw_transaction.hex()


def get_wallet_address(private_key: str) -> str:
    """Derive address from private key."""
    return Account.from_key(private_key).address


# ---------------------------------------------------------------------------
# WebSocket log subscription
# ---------------------------------------------------------------------------

async def ws_subscribe_logs(
    ws_url: str,
    addresses: list[str],
    topics: list[list[str] | str],
    callback,
    reconnect_delay: float = 5.0,
):
    """Subscribe to eth_subscribe('logs') with auto-reconnect.

    callback(log_entry: dict) is called for each matching log.
    Runs forever — designed to be wrapped in asyncio.create_task.
    """
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url, heartbeat=30) as ws:
                    # Subscribe
                    sub_params = {"address": addresses, "topics": topics}
                    sub_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_subscribe",
                        "params": ["logs", sub_params],
                    }
                    await ws.send_json(sub_msg)

                    # Read subscription confirmation
                    resp = await ws.receive_json(timeout=15)
                    sub_id = resp.get("result")
                    if not sub_id:
                        log.warning(f"WS subscribe failed: {resp}")
                        await asyncio.sleep(reconnect_delay)
                        continue
                    log.info(f"WS subscribed (id={sub_id}) to {len(addresses)} address(es)")

                    # Listen for logs
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            params = data.get("params", {})
                            log_entry = params.get("result")
                            if log_entry:
                                try:
                                    await callback(log_entry)
                                except Exception as e:
                                    log.error(f"WS callback error: {e}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            log.warning(f"WS closed/error: {msg.type}")
                            break

        except asyncio.CancelledError:
            log.info("WS subscribe cancelled")
            return
        except Exception as e:
            log.warning(f"WS error: {e}")

        log.info(f"WS reconnecting in {reconnect_delay}s...")
        await asyncio.sleep(reconnect_delay)
