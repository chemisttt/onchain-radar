"""Hyperliquid auto-trading service — executes signals as positions.

Follows existing service pattern: _task, _poll_loop, start/stop.
Entry via on_signal() called from telegram_service after alert fires.
Adaptive exits polled every 60s (trail_atr, counter_sig, zscore_mr, fixed, hybrid).
Hard SL always placed on exchange as safety net.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import aiohttp
import msgpack
from eth_account import Account
from eth_account.messages import encode_typed_data

from config import settings
from db import get_db

log = logging.getLogger("trading")

_task: asyncio.Task | None = None

# ─── Hyperliquid API ────────────────────────────────────────────────────

HL_EXCHANGE = "https://api.hyperliquid.xyz/exchange"
HL_INFO = "https://api.hyperliquid.xyz/info"

_wallet = None
_address: str = ""
_asset_index: dict[str, int] = {}
_sz_decimals: dict[str, int] = {}
_meta_loaded = False
_leverage_set: set[str] = set()

# ─── Signal → Exit strategy mapping (mirrors setup_backtest.py) ─────────

ADAPTIVE_EXIT = {
    "liq_short_squeeze":   "counter_sig",
    "div_squeeze_3d":      "counter_sig",
    "div_top_1d":          "counter_sig",
    "distribution":        "fixed",
    "oi_buildup_stall":    "fixed",
    "vol_divergence":      "counter_sig",
    "overheat":            "fixed",
    "overextension":       "trail_atr",
    "fund_reversal":       "zscore_mr",
    "capitulation":        "zscore_mr",
    "momentum_divergence": "counter_sig",
    "liq_ratio_extreme":   "counter_sig",
    "fund_spike":          "trail_atr",
    "fund_mean_revert":    "counter_sig",
    "div_squeeze_1d":      "hybrid",
}

# Signal types that should NEVER be traded (permanently disabled)
BLOCKED_SIGNAL_TYPES = {"volume_spike"}

# Counter-signal sets — triggers exit for open positions in that direction
# NOTE: volume_spike included as exit trigger even though blocked as entry
COUNTER_SIGNALS = {
    "long":  {"overheat", "fund_spike", "distribution", "overextension",
              "div_top_1d", "momentum_divergence", "volume_spike",
              "fund_reversal", "fund_mean_revert"},
    "short": {"capitulation", "liq_flush", "liq_short_squeeze",
              "vol_divergence", "momentum_divergence", "volume_spike",
              "liq_ratio_extreme", "fund_reversal", "fund_mean_revert"},
}

# Z-score primary metric per signal type
SIGNAL_PRIMARY_Z = {
    "overheat": "oi_zscore", "div_squeeze_1d": "oi_zscore",
    "div_squeeze_3d": "oi_zscore", "div_top_1d": "oi_zscore",
    "oi_buildup_stall": "oi_zscore",
    "capitulation": "funding_zscore", "fund_spike": "funding_zscore",
    "fund_reversal": "funding_zscore",
    "fund_mean_revert": "funding_zscore",
    "liq_flush": "liq_zscore", "liq_short_squeeze": "liq_zscore",
    "vol_divergence": "volume_zscore", "distribution": "volume_zscore",
    "overextension": "oi_zscore",
    "momentum_divergence": "funding_zscore",
    "liq_ratio_extreme": "liq_zscore",
}
ZSCORE_TP_THRESH = {"oi_zscore": 0.5, "funding_zscore": 0.3,
                    "liq_zscore": 1.0, "volume_zscore": 0.5}

# Exit params
HARD_STOP_PCT = 8.0
COUNTER_HARD_STOP_PCT = 12.0
FIXED_TP_PCT = 5.0
FIXED_SL_PCT = 3.0
FIXED_TIMEOUT_DAYS = 7
TRAIL_ATR_MULT = 1.5
TRAIL_BE_PCT = 2.0
MAX_HOLD_DAYS = 30
ZSCORE_SL_INCREASE = 1.0

POLL_INTERVAL = 60
INITIAL_DELAY = 45

# HL tradeable symbols
HL_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT", "AAVEUSDT", "DOTUSDT",
    "FILUSDT", "ATOMUSDT", "TRXUSDT", "JUPUSDT", "SEIUSDT", "TIAUSDT",
    "INJUSDT", "TRUMPUSDT", "WIFUSDT", "TONUSDT", "RENDERUSDT", "ENAUSDT",
    "HYPEUSDT", "ZECUSDT", "TAOUSDT", "WLDUSDT",
}

# Recent signals cache for counter-signal exit detection
# {symbol: [(signal_type, direction, timestamp), ...]}
_recent_signals: dict[str, list[tuple[str, str, float]]] = {}


# ─── Hyperliquid helpers ────────────────────────────────────────────────

def _keccak256(data: bytes) -> bytes:
    from Crypto.Hash import keccak as _keccak
    h = _keccak.new(digest_bits=256)
    h.update(data)
    return h.digest()


def _address_to_bytes(addr: str) -> bytes:
    return bytes.fromhex(addr[2:] if addr.startswith("0x") else addr)


def _action_hash(action: dict, vault_address: str | None, nonce: int) -> bytes:
    data = msgpack.packb(action)
    data += nonce.to_bytes(8, "big")
    if vault_address is None:
        data += b"\x00"
    else:
        data += b"\x01"
        data += _address_to_bytes(vault_address)
    return _keccak256(data)


def _sign_l1_action(action: dict, nonce: int) -> dict:
    hash_val = _action_hash(action, None, nonce)
    phantom_agent = {"source": "a", "connectionId": hash_val}
    payload = {
        "domain": {
            "chainId": 1337, "name": "Exchange",
            "verifyingContract": "0x0000000000000000000000000000000000000000",
            "version": "1",
        },
        "types": {
            "Agent": [
                {"name": "source", "type": "string"},
                {"name": "connectionId", "type": "bytes32"},
            ],
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
        },
        "primaryType": "Agent",
        "message": phantom_agent,
    }
    structured = encode_typed_data(full_message=payload)
    signed = _wallet.sign_message(structured)
    return {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v}


def _float_to_wire(x: float, decimals: int = 8, max_sigfigs: int = 5) -> str:
    """Convert float to HL wire format. HL requires ≤5 significant figures for prices."""
    if x == 0:
        return "0"
    # Round to max_sigfigs significant figures
    from math import log10, floor
    magnitude = floor(log10(abs(x)))
    rounded = round(x, -int(magnitude) + max_sigfigs - 1)
    return f"{rounded:.{decimals}f}".rstrip("0").rstrip(".")


def _sz_to_wire(coin: str, sz: float) -> str:
    dec = _sz_decimals.get(coin, 2)
    return f"{sz:.{dec}f}"


def _normalize_coin(symbol: str) -> str:
    """BTCUSDT → BTC"""
    if symbol.endswith("USDT"):
        return symbol[:-4]
    return symbol


def _order_wire(coin: str, is_buy: bool, sz: float, px: float,
                order_type: dict, reduce_only: bool = False) -> dict:
    return {
        "a": _asset_index[coin],
        "b": is_buy,
        "p": _float_to_wire(px),
        "s": _sz_to_wire(coin, sz),
        "r": reduce_only,
        "t": order_type,
    }


async def _hl_exchange(session: aiohttp.ClientSession, action: dict) -> dict:
    nonce = int(time.time() * 1000)
    sig = _sign_l1_action(action, nonce)
    payload = {"action": action, "nonce": nonce, "signature": sig}
    async with session.post(HL_EXCHANGE, json=payload,
                            timeout=aiohttp.ClientTimeout(total=10)) as resp:
        data = await resp.json()
        if resp.status != 200:
            log.error(f"HL exchange error {resp.status}: {data}")
        # HL API sometimes returns a bare string instead of dict
        if not isinstance(data, dict):
            log.error(f"HL exchange returned non-dict: {data}")
            return {"status": "err", "response": {"error": str(data)}}
        return data


async def _hl_info(session: aiohttp.ClientSession, payload: dict) -> dict | list:
    async with session.post(HL_INFO, json=payload,
                            timeout=aiohttp.ClientTimeout(total=10)) as resp:
        data = await resp.json()
        if resp.status != 200:
            log.error(f"HL info error {resp.status}: {data}")
        return data


async def _load_meta(session: aiohttp.ClientSession) -> None:
    global _meta_loaded
    if _meta_loaded:
        return
    meta = await _hl_info(session, {"type": "meta"})
    for i, u in enumerate(meta["universe"]):
        name = u["name"]
        _asset_index[name] = i
        _sz_decimals[name] = u["szDecimals"]
    _meta_loaded = True
    log.info(f"HL meta loaded: {len(_asset_index)} assets")


async def _get_all_mids(session: aiohttp.ClientSession) -> dict[str, float]:
    data = await _hl_info(session, {"type": "allMids"})
    return {k: float(v) for k, v in data.items()}


async def _get_account_state(session: aiohttp.ClientSession) -> dict:
    return await _hl_info(session, {"type": "clearinghouseState", "user": _address})


async def _get_equity(session: aiohttp.ClientSession) -> float:
    acct = await _get_account_state(session)
    return float(acct.get("marginSummary", {}).get("accountValue", 0))


async def _set_leverage(session: aiohttp.ClientSession, coin: str) -> None:
    if coin in _leverage_set:
        return
    action = {
        "type": "updateLeverage",
        "asset": _asset_index[coin],
        "isCross": True,
        "leverage": settings.hl_leverage,
    }
    result = await _hl_exchange(session, action)
    log.info(f"Set leverage {coin} → {settings.hl_leverage}x: {result.get('status', '?')}")
    _leverage_set.add(coin)


async def _market_open(session: aiohttp.ClientSession, coin: str,
                       is_buy: bool, sz: float) -> dict:
    """Aggressive IOC market order for opening position."""
    mids = await _get_all_mids(session)
    mid = mids.get(coin)
    if not mid:
        return {"status": "err", "response": f"No mid price for {coin}"}
    px = mid * 1.05 if is_buy else mid * 0.95
    wire = _order_wire(coin, is_buy, sz, px, {"limit": {"tif": "Ioc"}})
    action = {"type": "order", "orders": [wire], "grouping": "na"}
    return await _hl_exchange(session, action)


async def _market_close(session: aiohttp.ClientSession, coin: str,
                        is_buy: bool, sz: float,
                        mids: dict | None = None) -> dict:
    """Reduce-only IOC market order for closing position."""
    if mids is None:
        mids = await _get_all_mids(session)
    mid = mids.get(coin)
    if not mid:
        return {"status": "err", "response": f"No mid price for {coin}"}
    px = mid * 1.05 if is_buy else mid * 0.95
    wire = _order_wire(coin, is_buy, sz, px,
                       {"limit": {"tif": "Ioc"}}, reduce_only=True)
    action = {"type": "order", "orders": [wire], "grouping": "na"}
    return await _hl_exchange(session, action)


async def _trigger_sl(session: aiohttp.ClientSession, coin: str,
                      is_buy: bool, sz: float, trigger_px: float) -> dict:
    """Place trigger SL order (reduce-only)."""
    close_buy = not is_buy
    order_type = {
        "trigger": {
            "isMarket": True,
            "triggerPx": _float_to_wire(trigger_px),
            "tpsl": "sl",
        }
    }
    wire = _order_wire(coin, close_buy, sz, trigger_px, order_type, reduce_only=True)
    action = {"type": "order", "orders": [wire], "grouping": "na"}
    return await _hl_exchange(session, action)


async def _cancel_order(session: aiohttp.ClientSession, coin: str, oid: int) -> dict:
    action = {"type": "cancel", "cancels": [{"a": _asset_index[coin], "o": oid}]}
    return await _hl_exchange(session, action)


# ─── Telegram notify ────────────────────────────────────────────────────

async def _notify(text: str, session: aiohttp.ClientSession) -> None:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    thread_id = settings.telegram_thread_id
    if thread_id:
        payload["message_thread_id"] = thread_id
    try:
        async with session.post(url, json=payload,
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                log.warning(f"Notify failed: {resp.status}")
    except Exception as e:
        log.warning(f"Notify error: {e}")


# ─── DB helpers ─────────────────────────────────────────────────────────

async def _count_open() -> int:
    db = get_db()
    row = await db.execute_fetchone(
        "SELECT COUNT(*) as cnt FROM trades WHERE status='open'")
    return row["cnt"] if row else 0


async def _get_open_trades() -> list[dict]:
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM trades WHERE status='open' ORDER BY opened_at")
    return [dict(r) for r in rows]


async def _insert_trade(alert_id, symbol, direction, signal_type, entry_price,
                        entry_size_usd, leverage, sl_price, sl_order_id,
                        meta=None) -> int:
    db = get_db()
    cursor = await db.execute(
        """INSERT INTO trades
           (alert_id, symbol, direction, signal_type, entry_price, entry_size_usd,
            leverage, sl_price, sl_order_id, status, opened_at, meta)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
        (alert_id, symbol, direction, signal_type, entry_price, entry_size_usd,
         leverage, sl_price, sl_order_id, datetime.now(timezone.utc).isoformat(),
         json.dumps(meta or {})),
    )
    await db.commit()
    return cursor.lastrowid


async def _close_trade(trade_id, exit_price, exit_reason, pnl_pct, pnl_usd,
                       meta_update=None):
    db = get_db()
    existing = await db.execute_fetchone(
        "SELECT meta FROM trades WHERE id=?", (trade_id,))
    merged = json.loads(existing["meta"]) if existing else {}
    if meta_update:
        merged.update(meta_update)
    await db.execute(
        """UPDATE trades SET exit_price=?, exit_reason=?, pnl_pct=?, pnl_usd=?,
           status='closed', closed_at=?, meta=? WHERE id=?""",
        (exit_price, exit_reason, pnl_pct, pnl_usd,
         datetime.now(timezone.utc).isoformat(), json.dumps(merged), trade_id),
    )
    await db.commit()


# ─── ATR + Z-score fetchers ────────────────────────────────────────────

async def _get_atr(symbol: str, period: int = 14) -> float:
    """Compute 14-period ATR from 4h OHLCV candles."""
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT high, low, close FROM ohlcv_4h WHERE symbol=? "
        "ORDER BY ts DESC LIMIT ?",
        (symbol, period + 1))
    if len(rows) < period + 1:
        return 0
    rows = list(reversed(rows))
    tr_sum = 0.0
    for i in range(1, len(rows)):
        h, l, prev_c = rows[i]["high"], rows[i]["low"], rows[i - 1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_sum += tr
    return tr_sum / period


async def _get_current_zscores(symbol: str) -> dict:
    """Get latest z-scores from derivatives_zscores."""
    db = get_db()
    row = await db.execute_fetchone(
        """SELECT oi_zscore, funding_zscore, liq_zscore, volume_zscore
           FROM derivatives_zscores WHERE symbol=? ORDER BY date DESC LIMIT 1""",
        (symbol,))
    if not row:
        return {}
    return dict(row)


# ─── Signal cache (counter-signal detection) ───────────────────────────

async def _load_recent_signals() -> None:
    """Load recent signals from alert_tracking (survive restarts)."""
    try:
        db = get_db()
        cutoff_ts = datetime.now(timezone.utc).timestamp() - MAX_HOLD_DAYS * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat()
        rows = await db.execute_fetchall(
            """SELECT alert_type, symbol, expected_direction, fired_at
               FROM alert_tracking
               WHERE fired_at >= ? AND expected_direction IS NOT NULL""",
            (cutoff_iso,))
        loaded = 0
        for row in rows:
            symbol = row["symbol"]
            sig_type = row["alert_type"]
            dir_raw = row["expected_direction"]
            direction = "long" if dir_raw == "up" else "short" if dir_raw == "down" else None
            if not direction or not symbol:
                continue
            fired_at = datetime.fromisoformat(row["fired_at"]).timestamp()
            _recent_signals.setdefault(symbol, []).append(
                (sig_type, direction, fired_at))
            loaded += 1
        log.info(f"Loaded {loaded} recent signals for counter-exit detection")
    except Exception as e:
        log.warning(f"Failed to load recent signals: {e}")


_SIGNAL_CACHE_MAX_AGE = 7 * 86400  # 7 days


def _prune_recent_signals() -> None:
    """Remove signals older than 7 days to prevent memory leak."""
    cutoff = time.time() - _SIGNAL_CACHE_MAX_AGE
    for sym in list(_recent_signals):
        _recent_signals[sym] = [
            s for s in _recent_signals[sym] if s[2] > cutoff]
        if not _recent_signals[sym]:
            del _recent_signals[sym]


def cache_signal(alert: dict) -> None:
    """Cache signal for counter-signal exit detection.

    Called for ALL alerts from telegram_service (before tier/cooldown filtering)
    so counter-exit detection sees the full signal stream.
    """
    key = alert.get("key", "")
    signal_type = key.split(":")[0] if ":" in key else key
    symbol = alert.get("symbol", "")
    direction_raw = alert.get("expected_direction")
    if not direction_raw or not symbol:
        return
    direction = ("long" if direction_raw == "up"
                 else "short" if direction_raw == "down" else None)
    if not direction:
        return
    now = time.time()
    _recent_signals.setdefault(symbol, []).append(
        (signal_type, direction, now))
    _prune_recent_signals()


# ─── Entry logic ────────────────────────────────────────────────────────

async def on_signal(alert: dict) -> tuple[str, str]:
    """Entry point — called from telegram_service when alert passes filters.

    Returns (status, reason). status: 'opened', 'skipped', 'error'.
    Signal caching for counter-exits is done separately via cache_signal()
    which is called for ALL alerts before filtering.
    """
    if not settings.hl_enabled or not _wallet:
        return ("skipped", "trading_disabled")

    key = alert.get("key", "")
    signal_type = key.split(":")[0] if ":" in key else key
    symbol = alert.get("symbol", "")
    direction_raw = alert.get("expected_direction")

    # Skip blocked signal types
    if signal_type in BLOCKED_SIGNAL_TYPES:
        return ("skipped", "blocked_type")

    # Only trade signal types with a known exit strategy
    if signal_type not in ADAPTIVE_EXIT:
        log.debug(f"Signal type {signal_type} not in ADAPTIVE_EXIT, skipping trade")
        return ("skipped", "not_tradeable")

    # Skip if no direction or symbol not tradeable
    if not direction_raw:
        return ("skipped", "no_direction")
    if symbol not in HL_SYMBOLS:
        return ("skipped", "symbol_not_on_hl")

    direction = ("long" if direction_raw == "up"
                 else "short" if direction_raw == "down" else None)
    if not direction:
        return ("skipped", "bad_direction")

    # Check max open positions
    open_count = await _count_open()
    if open_count >= settings.hl_max_positions:
        log.info(f"Max positions ({settings.hl_max_positions}) reached, "
                 f"skipping {symbol}")
        return ("skipped", "max_positions")

    # Check existing position for same symbol
    db = get_db()
    dup = await db.execute_fetchone(
        "SELECT * FROM trades WHERE symbol=? AND status='open'", (symbol,))
    if dup:
        if dup["direction"] == direction:
            log.info(f"Already {direction} on {symbol} (#{dup['id']}), skipping")
            return ("skipped", f"duplicate_symbol:{dup['id']}")
        # Opposite direction → flip: close old, then open new
        log.info(f"Flipping {symbol}: closing {dup['direction']} #{dup['id']} → opening {direction}")
        try:
            async with aiohttp.ClientSession() as flip_session:
                await _load_meta(flip_session)
                mids = await _get_all_mids(flip_session)
                mid = mids.get(_normalize_coin(symbol), 0)
                if not mid:
                    log.error(f"No mid price for {symbol}, can't flip")
                    return ("error", "flip_no_mid_price")
                if mid:
                    if dup["direction"] == "long":
                        raw_pnl = (mid - dup["entry_price"]) / dup["entry_price"] * 100
                    else:
                        raw_pnl = (dup["entry_price"] - mid) / dup["entry_price"] * 100
                    pnl_pct = raw_pnl * dup.get("leverage", 1)
                else:
                    pnl_pct = 0
                await _execute_close(
                    dict(dup), mid, f"counter_flip_{signal_type}",
                    pnl_pct, mids, flip_session)
        except Exception as e:
            log.error(f"Failed to close #{dup['id']} for flip: {e}")
            return ("error", f"flip_close_failed:{e}")

    # Get alert_id from alert_tracking
    alert_id = None
    try:
        row = await db.execute_fetchone(
            "SELECT id FROM alert_tracking WHERE alert_key=? "
            "ORDER BY id DESC LIMIT 1", (key,))
        if row:
            alert_id = row["id"]
    except Exception:
        pass

    entry_price = alert.get("entry_price", 0)
    if not entry_price or entry_price <= 0:
        log.warning(f"No entry price for {symbol}, skipping")
        return ("skipped", "no_entry_price")

    try:
        async with aiohttp.ClientSession() as session:
            await _load_meta(session)
            coin = _normalize_coin(symbol)
            if coin not in _asset_index:
                log.warning(f"{coin} not in HL asset index, skipping")
                return ("skipped", "not_in_hl_index")

            # Get equity and compute position size
            equity = await _get_equity(session)
            if equity <= 0:
                log.warning(f"HL equity is {equity}, skipping")
                return ("skipped", "no_equity")

            alloc_usd = equity * (settings.hl_alloc_pct / 100)
            size_usd = alloc_usd * settings.hl_leverage
            sz = size_usd / entry_price

            # Set leverage
            await _set_leverage(session, coin)

            # Place market order (retry up to 3 times on transient errors)
            is_buy = direction == "long"
            last_err = ""
            statuses = []
            for attempt in range(1, 4):
                result = await _market_open(session, coin, is_buy, sz)
                statuses = (result.get("response", {}).get("data", {})
                            .get("statuses", []))
                if statuses and "filled" in statuses[0]:
                    break
                last_err = str(result.get("response", result))
                if attempt < 3:
                    log.warning(f"Market order attempt {attempt}/3 failed for "
                                f"{symbol}: {last_err}, retrying in 3s...")
                    await asyncio.sleep(3)

            if not statuses or "filled" not in statuses[0]:
                log.error(f"Market order failed for {symbol} after 3 attempts: {last_err}")
                await _notify(
                    f"<b>❌ ORDER FAILED: {symbol}</b>\n"
                    f"{signal_type} {direction}\n{last_err}",
                    session)
                return ("error", f"order_failed:{last_err[:100]}")

            fill = statuses[0]["filled"]
            fill_price = float(fill["avgPx"])
            fill_sz = float(fill["totalSz"])
            actual_size_usd = fill_price * fill_sz

            if fill_sz <= 0:
                log.error(f"Zero fill on {symbol}, not recording trade")
                return ("error", "zero_fill")

            if fill_sz < sz * 0.95:
                log.warning(f"Partial fill on {symbol}: requested {sz:.6f}, "
                            f"got {fill_sz:.6f} ({fill_sz / sz * 100:.0f}%)")

            # Compute real ATR from 4h candles
            atr = await _get_atr(symbol)
            if atr <= 0:
                atr = fill_price * 0.02  # fallback 2%
                log.info(f"ATR unavailable for {symbol}, using 2% fallback")

            # Hard SL on exchange (safety net — wider than adaptive stops)
            hard_stop_frac = settings.hl_hard_stop_pct / 100
            if is_buy:
                sl_price = fill_price * (1 - hard_stop_frac)
            else:
                sl_price = fill_price * (1 + hard_stop_frac)

            sl_oid = ""
            for sl_attempt in range(1, 4):
                sl_result = await _trigger_sl(
                    session, coin, is_buy, fill_sz, sl_price)
                sl_statuses = (sl_result.get("response", {}).get("data", {})
                               .get("statuses", []))
                if sl_statuses and "resting" in sl_statuses[0]:
                    sl_oid = str(sl_statuses[0]["resting"]["oid"])
                    break
                if sl_attempt < 3:
                    log.warning(f"SL attempt {sl_attempt}/3 failed for "
                                f"{symbol}: {sl_result}, retrying in 3s...")
                    await asyncio.sleep(3)
            if not sl_oid:
                log.error(f"SL order FAILED after 3 attempts for "
                          f"{symbol}: {sl_result}")
                await _notify(
                    f"<b>⚠️ SL NOT PLACED: {symbol}</b>\n"
                    f"Position open without hard stop!", session)

            # Build meta
            exit_type = ADAPTIVE_EXIT[signal_type]
            meta = {
                "exit_strategy": exit_type,
                "fill_sz": fill_sz,
                "coin": coin,
                "atr": atr,
            }

            # Capture entry z-scores for zscore_mr exit
            if exit_type == "zscore_mr":
                meta["entry_zscore"] = await _get_current_zscores(symbol)

            # Init trail stop from ATR (matches backtest: entry ± 1.5×ATR)
            if exit_type in ("trail_atr", "hybrid"):
                if is_buy:
                    meta["trail_stop"] = fill_price - TRAIL_ATR_MULT * atr
                else:
                    meta["trail_stop"] = fill_price + TRAIL_ATR_MULT * atr
                meta["best_price"] = fill_price
                meta["be_triggered"] = False

            trade_id = await _insert_trade(
                alert_id=alert_id, symbol=symbol, direction=direction,
                signal_type=signal_type, entry_price=fill_price,
                entry_size_usd=actual_size_usd, leverage=settings.hl_leverage,
                sl_price=sl_price, sl_order_id=sl_oid, meta=meta)

            log.info(f"OPENED #{trade_id}: {direction} {symbol} "
                     f"@ {fill_price:.4f} sz=${actual_size_usd:.0f} "
                     f"SL={sl_price:.4f} exit={exit_type}")

            await _notify(
                f"<b>🟢 OPENED #{trade_id}: {symbol}</b>\n"
                f"Direction: {direction.upper()}\n"
                f"Signal: {signal_type} "
                f"(conf {alert.get('confluence', '?')})\n"
                f"Entry: {fill_price:.4f}\n"
                f"Size: ${actual_size_usd:.0f} ({settings.hl_leverage}x)\n"
                f"Hard SL: {sl_price:.4f} "
                f"(-{settings.hl_hard_stop_pct}%)\n"
                f"Exit strategy: {exit_type}",
                session)

            return ("opened", f"trade:{trade_id}")

    except Exception as e:
        log.error(f"on_signal error for {symbol}: {e}", exc_info=True)
        return ("error", str(e)[:200])


# ─── Adaptive exit checks ──────────────────────────────────────────────

async def _check_exits() -> None:
    """Poll open positions, reconcile with HL, run adaptive exit logic."""
    trades = await _get_open_trades()
    if not trades:
        return

    try:
        async with aiohttp.ClientSession() as session:
            await _load_meta(session)
            mids = await _get_all_mids(session)

            # Reconcile with actual HL positions
            acct = await _get_account_state(session)
            hl_positions: dict[str, str] = {}  # coin → "long"/"short"
            for p in acct.get("assetPositions", []):
                pos = p["position"]
                szi = float(pos["szi"])
                if szi != 0:
                    hl_positions[pos["coin"]] = "long" if szi > 0 else "short"

            for trade in trades:
                try:
                    coin = _normalize_coin(trade["symbol"])
                    hl_dir = hl_positions.get(coin)

                    # Position gone from HL → hard SL triggered
                    if not hl_dir or hl_dir != trade["direction"]:
                        await _reconcile_closed(trade, session)
                        continue

                    await _check_single_exit(trade, mids, session)
                except Exception as e:
                    log.error(f"Exit check error for trade "
                              f"#{trade['id']}: {e}")

    except Exception as e:
        log.error(f"Exit check session error: {e}")


async def _reconcile_closed(trade: dict, session: aiohttp.ClientSession):
    """Mark a trade as closed when HL position is gone (hard SL hit)."""
    trade_id = trade["id"]
    symbol = trade["symbol"]
    entry_price = trade["entry_price"]
    sl_price = trade.get("sl_price", 0)
    leverage = trade.get("leverage", 1)

    # Estimate exit at SL price
    exit_price = sl_price if sl_price else entry_price
    if trade["direction"] == "long":
        raw_pnl_pct = (exit_price - entry_price) / entry_price * 100
    else:
        raw_pnl_pct = (entry_price - exit_price) / entry_price * 100

    leveraged = raw_pnl_pct * leverage
    alloc_usd = trade["entry_size_usd"] / leverage
    pnl_usd = alloc_usd * leveraged / 100

    opened_at = datetime.fromisoformat(trade["opened_at"])
    days_held = ((datetime.now(timezone.utc) - opened_at)
                 .total_seconds() / 86400)

    await _close_trade(
        trade_id, exit_price, "hard_stop", leveraged, pnl_usd,
        {"days_held": round(days_held, 1), "reconciled": True})

    log.info(f"RECONCILED #{trade_id}: {symbol} — "
             f"position gone from HL, PnL={raw_pnl_pct:+.2f}%")

    await _notify(
        f"<b>🔴 CLOSED #{trade_id}: {symbol}</b>\n"
        f"Reason: hard_stop (reconciled)\n"
        f"PnL: {raw_pnl_pct:+.2f}% ({leveraged:+.2f}% on capital)\n"
        f"USD: ${pnl_usd:+.0f}\n"
        f"Held: {days_held:.1f} days",
        session)


async def _check_single_exit(trade: dict, mids: dict,
                             session: aiohttp.ClientSession):
    """Check adaptive exit conditions for a single trade."""
    trade_id = trade["id"]
    symbol = trade["symbol"]
    direction = trade["direction"]
    entry_price = trade["entry_price"]
    coin = _normalize_coin(symbol)
    meta = json.loads(trade.get("meta", "{}"))
    exit_strategy = meta.get("exit_strategy", "trail_atr")

    current_price = mids.get(coin)
    if not current_price:
        return

    # Unrealized PnL
    if direction == "long":
        pnl_pct = (current_price - entry_price) / entry_price * 100
    else:
        pnl_pct = (entry_price - current_price) / entry_price * 100

    opened_at = datetime.fromisoformat(trade["opened_at"])
    days_held = ((datetime.now(timezone.utc) - opened_at)
                 .total_seconds() / 86400)

    # Max hold timeout (all strategies)
    if days_held >= MAX_HOLD_DAYS:
        await _execute_close(
            trade, current_price, "timeout", pnl_pct, mids, session)
        return

    # Route to exit strategy
    should_exit = False
    exit_reason = ""

    if exit_strategy == "fixed":
        should_exit, exit_reason = _check_fixed_exit(pnl_pct, days_held)
    elif exit_strategy == "zscore_mr":
        should_exit, exit_reason = await _check_zscore_exit(trade, meta)
    elif exit_strategy == "counter_sig":
        should_exit, exit_reason = _check_counter_exit(trade, pnl_pct)
    elif exit_strategy == "trail_atr":
        should_exit, exit_reason, meta = _check_trail_exit(
            trade, current_price, meta)
    elif exit_strategy == "hybrid":
        should_exit, exit_reason, meta = await _check_hybrid_exit(
            trade, current_price, pnl_pct, meta)

    if should_exit:
        await _execute_close(
            trade, current_price, exit_reason, pnl_pct, mids, session)
    else:
        # Persist updated meta (trail tracking state)
        db = get_db()
        await db.execute("UPDATE trades SET meta=? WHERE id=?",
                         (json.dumps(meta), trade_id))
        await db.commit()


def _check_fixed_exit(pnl_pct: float, days_held: float) -> tuple[bool, str]:
    """Fixed TP/SL/timeout."""
    if pnl_pct >= FIXED_TP_PCT:
        return True, "fixed_tp"
    if pnl_pct <= -FIXED_SL_PCT:
        return True, "fixed_sl"
    if days_held >= FIXED_TIMEOUT_DAYS:
        return True, "fixed_timeout"
    return False, ""


async def _check_zscore_exit(trade: dict, meta: dict) -> tuple[bool, str]:
    """Exit when primary z-score normalizes or worsens by +1.0."""
    signal_type = trade["signal_type"]
    symbol = trade["symbol"]
    pk = SIGNAL_PRIMARY_Z.get(signal_type, "oi_zscore")
    tp_thresh = ZSCORE_TP_THRESH.get(pk, 0.5)
    entry_zscores = meta.get("entry_zscore", {})
    entry_z = entry_zscores.get(pk, 0)

    current_zscores = await _get_current_zscores(symbol)
    cur_z = current_zscores.get(pk, 0)

    # Z reverts to near-zero → TP
    if abs(cur_z) < tp_thresh:
        return True, "zscore_tp"
    # Z worsened by +1.0 → SL
    if abs(cur_z) > abs(entry_z) + ZSCORE_SL_INCREASE:
        return True, "zscore_sl"
    return False, ""


def _check_counter_exit(trade: dict, pnl_pct: float) -> tuple[bool, str]:
    """Exit on opposite-direction signal. Hard stop at 12%."""
    direction = trade["direction"]
    symbol = trade["symbol"]
    cs = COUNTER_SIGNALS.get(direction, set())
    opened_at = datetime.fromisoformat(trade["opened_at"]).timestamp()

    signals = _recent_signals.get(symbol, [])
    for sig_type, sig_dir, sig_ts in signals:
        if sig_ts > opened_at and sig_type in cs:
            return True, f"counter_{sig_type}"

    if pnl_pct <= -COUNTER_HARD_STOP_PCT:
        return True, "counter_hard_stop"
    return False, ""


def _check_trail_exit(trade: dict, current_price: float,
                      meta: dict) -> tuple[bool, str, dict]:
    """Trailing ATR stop with break-even activation.

    Uses real ATR stored in meta at entry time.
    Order: check stop (previous level) → update trail for next check.
    """
    direction = trade["direction"]
    entry_price = trade["entry_price"]
    best = meta.get("best_price", entry_price)
    trail_stop = meta.get("trail_stop", 0)
    be = meta.get("be_triggered", False)
    atr = meta.get("atr", entry_price * 0.02)

    if direction == "long":
        pnl_pct = (current_price - entry_price) / entry_price * 100
        # 1. Check stop with previous level
        if trail_stop > 0 and current_price <= trail_stop:
            meta.update({"best_price": best, "trail_stop": trail_stop,
                         "be_triggered": be})
            return True, "trail_stop", meta
        # 2. Update trail for next check
        if current_price > best:
            best = current_price
            trail_stop = max(trail_stop, best - TRAIL_ATR_MULT * atr)
        if not be and pnl_pct >= TRAIL_BE_PCT:
            trail_stop = max(trail_stop, entry_price)
            be = True
    else:
        pnl_pct = (entry_price - current_price) / entry_price * 100
        # 1. Check stop with previous level
        if trail_stop > 0 and current_price >= trail_stop:
            meta.update({"best_price": best, "trail_stop": trail_stop,
                         "be_triggered": be})
            return True, "trail_stop", meta
        # 2. Update trail for next check
        if current_price < best:
            best = current_price
            new_stop = best + TRAIL_ATR_MULT * atr
            trail_stop = (min(trail_stop, new_stop) if trail_stop > 0
                          else new_stop)
        if not be and pnl_pct >= TRAIL_BE_PCT:
            trail_stop = min(trail_stop, entry_price)
            be = True

    meta.update({"best_price": best, "trail_stop": trail_stop,
                 "be_triggered": be})
    return False, "", meta


async def _check_hybrid_exit(trade: dict, current_price: float,
                             pnl_pct: float,
                             meta: dict) -> tuple[bool, str, dict]:
    """Hybrid: trail_atr + counter_sig + zscore combined."""
    # 1. Trail
    should_exit, reason, meta = _check_trail_exit(
        trade, current_price, meta)
    if should_exit:
        return True, reason, meta

    # 2. Counter-signal
    should_exit, reason = _check_counter_exit(trade, pnl_pct)
    if should_exit:
        return True, reason, meta

    # 3. Z-score
    should_exit, reason = await _check_zscore_exit(trade, meta)
    if should_exit:
        return True, reason, meta

    return False, "", meta


# ─── Close execution ───────────────────────────────────────────────────

async def _execute_close(trade: dict, exit_price: float, exit_reason: str,
                         pnl_pct: float, mids: dict,
                         session: aiohttp.ClientSession):
    """Close position on HL (reduce-only) and update DB."""
    trade_id = trade["id"]
    symbol = trade["symbol"]
    direction = trade["direction"]
    coin = _normalize_coin(symbol)
    meta = json.loads(trade.get("meta", "{}"))
    fill_sz = meta.get("fill_sz", 0)

    if not fill_sz:
        # Fallback: try to get size from HL position
        try:
            acct = await _get_account_state(session)
            for pos in acct.get("assetPositions", []):
                p = pos.get("position", {})
                if p.get("coin") == coin and float(p.get("szi", 0)) != 0:
                    fill_sz = abs(float(p["szi"]))
                    log.warning(f"Recovered fill_sz={fill_sz} from HL for #{trade_id}")
                    break
        except Exception:
            pass
        if not fill_sz:
            log.error(f"No fill_sz for trade #{trade_id}, can't close")
            return

    try:
        # Reduce-only close (retry up to 3 times on transient errors)
        is_buy = direction != "long"
        actual_exit_price = None
        last_err = ""

        for attempt in range(1, 4):
            result = await _market_close(session, coin, is_buy, fill_sz, mids)
            statuses = (result.get("response", {}).get("data", {})
                        .get("statuses", []))
            if statuses and "filled" in statuses[0]:
                fill = statuses[0]["filled"]
                actual_exit_price = float(fill["avgPx"])
                break

            last_err = str(result.get("response", result))
            no_pos = ("No open position" in last_err
                      or "reduce only" in last_err.lower())
            if no_pos:
                log.info(f"Trade #{trade_id} already closed on HL")
                actual_exit_price = exit_price
                exit_reason = "hard_stop"
                break

            if attempt < 3:
                log.warning(f"Close attempt {attempt}/3 failed for "
                            f"#{trade_id}: {last_err}, retrying in 3s...")
                await asyncio.sleep(3)

        if actual_exit_price is None:
            log.error(f"Close order failed for #{trade_id} after 3 attempts: {last_err}")
            await _notify(
                f"<b>❌ CLOSE FAILED #{trade_id}: {symbol}</b>\n"
                f"{last_err}", session)
            return

        # Cancel SL order if still active
        sl_oid = trade.get("sl_order_id", "")
        if sl_oid:
            try:
                await _cancel_order(session, coin, int(sl_oid))
            except Exception:
                pass  # SL may already be filled/cancelled

        # PnL calculation
        # pnl_pct = return on capital (leveraged)
        # pnl_usd = absolute USD profit/loss
        entry_price = trade["entry_price"]
        leverage = trade.get("leverage", 1)
        if direction == "long":
            raw_pnl_pct = ((actual_exit_price - entry_price)
                           / entry_price * 100)
        else:
            raw_pnl_pct = ((entry_price - actual_exit_price)
                           / entry_price * 100)

        leveraged_pnl_pct = raw_pnl_pct * leverage
        alloc_usd = trade["entry_size_usd"] / leverage
        pnl_usd = alloc_usd * leveraged_pnl_pct / 100

        opened_at = datetime.fromisoformat(trade["opened_at"])
        days_held = ((datetime.now(timezone.utc) - opened_at)
                     .total_seconds() / 86400)

        await _close_trade(
            trade_id, actual_exit_price, exit_reason,
            leveraged_pnl_pct, pnl_usd,
            {"days_held": round(days_held, 1)})

        emoji = "🟢" if raw_pnl_pct > 0 else "🔴"
        log.info(f"CLOSED #{trade_id}: {symbol} {exit_reason} "
                 f"PnL={raw_pnl_pct:+.2f}% raw, "
                 f"{leveraged_pnl_pct:+.2f}% on capital "
                 f"(${pnl_usd:+.0f})")

        await _notify(
            f"<b>{emoji} CLOSED #{trade_id}: {symbol}</b>\n"
            f"Direction: {direction.upper()}\n"
            f"Signal: {trade['signal_type']}\n"
            f"Entry: {entry_price:.4f} → Exit: "
            f"{actual_exit_price:.4f}\n"
            f"PnL: {raw_pnl_pct:+.2f}% "
            f"({leveraged_pnl_pct:+.2f}% on capital)\n"
            f"USD: ${pnl_usd:+.0f}\n"
            f"Reason: {exit_reason}\n"
            f"Held: {days_held:.1f} days",
            session)

    except Exception as e:
        log.error(f"execute_close error for #{trade_id}: {e}",
                  exc_info=True)


# ─── Manual close (from router) ────────────────────────────────────────

async def close_trade_manual(trade_id: int) -> dict:
    """Manually close a trade. Returns result dict."""
    db = get_db()
    row = await db.execute_fetchone(
        "SELECT * FROM trades WHERE id=? AND status='open'", (trade_id,))
    if not row:
        return {"error": "Trade not found or already closed"}

    trade = dict(row)
    try:
        async with aiohttp.ClientSession() as session:
            await _load_meta(session)
            mids = await _get_all_mids(session)
            coin = _normalize_coin(trade["symbol"])
            current_price = mids.get(coin, 0)
            if not current_price:
                return {"error": f"No price for {coin}"}

            if trade["direction"] == "long":
                pnl_pct = ((current_price - trade["entry_price"])
                           / trade["entry_price"] * 100)
            else:
                pnl_pct = ((trade["entry_price"] - current_price)
                           / trade["entry_price"] * 100)

            await _execute_close(
                trade, current_price, "manual", pnl_pct, mids, session)

        return {"status": "ok", "trade_id": trade_id}
    except Exception as e:
        log.error(f"Manual close error: {e}", exc_info=True)
        return {"error": str(e)}


# ─── Stats ──────────────────────────────────────────────────────────────

async def get_stats() -> dict:
    db = get_db()
    open_trades = await db.execute_fetchall(
        "SELECT * FROM trades WHERE status='open' ORDER BY opened_at")
    closed_trades = await db.execute_fetchall(
        "SELECT * FROM trades WHERE status='closed' ORDER BY closed_at DESC")

    closed = [dict(r) for r in closed_trades]
    wins = [t for t in closed if (t.get("pnl_pct") or 0) > 0]
    total_pnl_usd = sum(t.get("pnl_usd", 0) or 0 for t in closed)
    total_pnl_pct = sum(t.get("pnl_pct", 0) or 0 for t in closed)

    return {
        "open_count": len(open_trades),
        "closed_count": len(closed),
        "win_count": len(wins),
        "win_rate": (round(len(wins) / len(closed) * 100, 1)
                     if closed else 0),
        "total_pnl_usd": round(total_pnl_usd, 2),
        "avg_pnl_pct": (round(total_pnl_pct / len(closed), 2)
                        if closed else 0),
    }


# ─── Service lifecycle ──────────────────────────────────────────────────

async def _poll_loop():
    log.info("Trading service started")
    await asyncio.sleep(INITIAL_DELAY)
    await _load_recent_signals()

    while True:
        try:
            await _check_exits()
        except Exception as e:
            log.error(f"Trading poll error: {e}", exc_info=True)
        await asyncio.sleep(POLL_INTERVAL)


def start():
    global _task, _wallet, _address
    if not settings.hl_enabled:
        log.info("Trading service skipped (HL_TRADING_ENABLED=false)")
        return
    if not settings.hl_wallet_key:
        log.warning("Trading service skipped (no HL_WALLET_KEY)")
        return
    try:
        _wallet = Account.from_key(settings.hl_wallet_key)
        _address = _wallet.address
        log.info(f"Trading wallet: {_address}")
    except Exception as e:
        log.error(f"Invalid HL_WALLET_KEY: {e}")
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll_loop())


def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
