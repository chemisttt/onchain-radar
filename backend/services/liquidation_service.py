import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta

import aiohttp

from db import get_db
from services.derivatives_service import SYMBOLS

log = logging.getLogger("liquidation")

_tasks: list[asyncio.Task] = []

LEVERAGE_TIERS = [5, 10, 25, 50, 100]
LEVERAGE_WEIGHTS = [0.10, 0.25, 0.30, 0.20, 0.15]


# ── Binance WS Liquidations ─────────────────────────────────────────

async def _ws_binance_liquidations():
    """Connect to Binance forceOrder stream and collect liquidation events."""
    url = "wss://fstream.binance.com/ws/!forceOrder@arr"
    db = get_db()
    batch: list[tuple] = []
    last_flush = time.time()

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=30, timeout=aiohttp.ClientTimeout(total=0)) as ws:
                    log.info("Binance liquidation WS connected")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                o = data.get("o", {})
                                sym = o.get("s", "")
                                # Normalize symbol
                                from services.derivatives_service import BINANCE_SYMBOL_MAP
                                sym = BINANCE_SYMBOL_MAP.get(sym, sym)
                                if sym not in SYMBOLS:
                                    # Time-based flush even without matches
                                    if batch and time.time() - last_flush > 10:
                                        await db.executemany(
                                            """INSERT INTO liquidation_events
                                               (symbol, side, price, quantity, usd_value, exchange, timestamp)
                                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                            batch,
                                        )
                                        await db.commit()
                                        batch.clear()
                                        last_flush = time.time()
                                    continue

                                side = "long" if o.get("S", "") == "SELL" else "short"
                                price = float(o.get("p", 0))
                                qty = float(o.get("q", 0))
                                usd_val = price * qty
                                ts = int(o.get("T", time.time() * 1000))

                                batch.append((sym, side, price, qty, usd_val, "binance", ts))

                                if len(batch) >= 3 or time.time() - last_flush > 10:
                                    await db.executemany(
                                        """INSERT INTO liquidation_events
                                           (symbol, side, price, quantity, usd_value, exchange, timestamp)
                                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                        batch,
                                    )
                                    await db.commit()
                                    batch.clear()
                                    last_flush = time.time()
                            except Exception as e:
                                log.debug(f"Binance liq parse: {e}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"Binance liq WS error: {e}")
        await asyncio.sleep(5)


# ── Bybit WS Liquidations ───────────────────────────────────────────

async def _ws_bybit_liquidations():
    """Connect to Bybit allLiquidation stream."""
    url = "wss://stream.bybit.com/v5/public/linear"
    db = get_db()
    batch: list[tuple] = []
    last_flush = time.time()

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=30, timeout=aiohttp.ClientTimeout(total=0)) as ws:
                    # Subscribe to all liquidation events
                    sub_msg = json.dumps({
                        "op": "subscribe",
                        "args": ["allLiquidation"]
                    })
                    await ws.send_str(sub_msg)
                    log.info("Bybit liquidation WS connected")

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                if data.get("topic") != "allLiquidation":
                                    # Time-based flush on non-topic messages (pong, etc.)
                                    if batch and time.time() - last_flush > 10:
                                        await db.executemany(
                                            """INSERT INTO liquidation_events
                                               (symbol, side, price, quantity, usd_value, exchange, timestamp)
                                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                            batch,
                                        )
                                        await db.commit()
                                        batch.clear()
                                        last_flush = time.time()
                                    continue
                                d = data.get("data", {})
                                sym = d.get("symbol", "")
                                if sym not in SYMBOLS:
                                    continue

                                side = "long" if d.get("side", "") == "Sell" else "short"
                                price = float(d.get("price", 0))
                                qty = float(d.get("size", 0))
                                usd_val = price * qty
                                ts = int(d.get("updatedTime", time.time() * 1000))

                                batch.append((sym, side, price, qty, usd_val, "bybit", ts))

                                if len(batch) >= 3 or time.time() - last_flush > 10:
                                    await db.executemany(
                                        """INSERT INTO liquidation_events
                                           (symbol, side, price, quantity, usd_value, exchange, timestamp)
                                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                        batch,
                                    )
                                    await db.commit()
                                    batch.clear()
                                    last_flush = time.time()
                            except Exception as e:
                                log.debug(f"Bybit liq parse: {e}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"Bybit liq WS error: {e}")
        await asyncio.sleep(5)


# ── Cleanup ──────────────────────────────────────────────────────────

async def _cleanup_loop():
    """Delete events older than 7 days periodically."""
    while True:
        try:
            db = get_db()
            cutoff = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)
            await db.execute("DELETE FROM liquidation_events WHERE timestamp < ?", (cutoff,))
            await db.commit()
        except Exception as e:
            log.warning(f"Liq cleanup error: {e}")
        await asyncio.sleep(3600)  # hourly


# ── Theoretical Liquidation Map ──────────────────────────────────────

async def _compute_theoretical_levels(symbol: str) -> list[dict]:
    """Estimate liquidation levels based on current price + OI + leverage distribution."""
    db = get_db()
    sym = symbol.upper()

    # Get current price + OI
    row = await db.execute_fetchall(
        """SELECT close_price, open_interest_usd FROM daily_derivatives
           WHERE symbol = ? ORDER BY date DESC LIMIT 1""",
        (sym,),
    )
    if not row or not row[0]["close_price"]:
        return []

    price = row[0]["close_price"]
    oi = row[0]["open_interest_usd"] or 0

    if oi <= 0:
        return []

    levels = []
    for lev, weight in zip(LEVERAGE_TIERS, LEVERAGE_WEIGHTS):
        liq_long_price = price * (1 - 1 / lev)
        liq_short_price = price * (1 + 1 / lev)
        volume = oi * weight

        levels.append({
            "price": round(liq_long_price, 2),
            "long_vol": round(volume, 0),
            "short_vol": 0,
            "leverage": lev,
        })
        levels.append({
            "price": round(liq_short_price, 2),
            "long_vol": 0,
            "short_vol": round(volume, 0),
            "leverage": lev,
        })

    # Sort by price
    levels.sort(key=lambda x: x["price"])
    return levels


# ── API response ─────────────────────────────────────────────────────

async def get_liquidation_map(symbol: str) -> dict:
    """Return liquidation map data for a symbol."""
    db = get_db()
    sym = symbol.upper()

    levels = await _compute_theoretical_levels(sym)

    # Get current price
    row = await db.execute_fetchall(
        "SELECT close_price FROM daily_derivatives WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (sym,),
    )
    current_price = row[0]["close_price"] if row else 0

    # Recent real liquidation events (last 24h)
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)
    events = await db.execute_fetchall(
        """SELECT price, side, usd_value, exchange, timestamp
           FROM liquidation_events
           WHERE symbol = ? AND timestamp >= ?
           ORDER BY timestamp DESC LIMIT 50""",
        (sym, cutoff),
    )

    recent = [
        {
            "price": r["price"],
            "side": r["side"],
            "usd_value": r["usd_value"],
            "exchange": r["exchange"],
            "ts": r["timestamp"],
        }
        for r in events
    ]

    return {
        "symbol": sym,
        "current_price": current_price,
        "levels": levels,
        "recent_events": recent,
    }


# ── Service lifecycle ────────────────────────────────────────────────

def start():
    global _tasks
    if not _tasks:
        _tasks = [
            asyncio.create_task(_ws_binance_liquidations()),
            asyncio.create_task(_ws_bybit_liquidations()),
            asyncio.create_task(_cleanup_loop()),
        ]
        log.info("Liquidation service started (Binance + Bybit WS)")


def stop():
    global _tasks
    for t in _tasks:
        if not t.done():
            t.cancel()
    _tasks = []
