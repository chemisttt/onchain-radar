#!/usr/bin/env python3
"""Backfill ohlcv_4h from Binance Futures API.

Extends 4h OHLCV coverage back to Jan 2022 for multi-year backtesting.
Uses paginated /fapi/v1/klines endpoint. Safe to re-run (INSERT OR IGNORE).

Usage:
  cd backend && python3 scripts/backfill_ohlcv_4h.py
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timezone

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import init_db, get_db

# Same 30 symbols as derivatives_service.py
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT", "AAVEUSDT", "DOTUSDT",
    "FILUSDT", "ATOMUSDT", "TRXUSDT", "JUPUSDT", "SEIUSDT", "TIAUSDT",
    "INJUSDT", "TRUMPUSDT", "WIFUSDT", "TONUSDT", "RENDERUSDT", "ENAUSDT",
]

# Start from Jan 2022 for multi-year backtest coverage
START = datetime(2022, 1, 1, tzinfo=timezone.utc)
PAGES = 8  # 8 × 1500 = 12000 candles max (~2000 days of 4h bars)


async def backfill():
    await init_db()
    db = get_db()

    r = await db.execute_fetchall("SELECT COUNT(*) as c FROM ohlcv_4h")
    before = r[0]["c"] if r else 0
    print(f"\n  ohlcv_4h before: {before:,} rows\n")

    start_ts = int(START.timestamp() * 1000)
    timeout = aiohttp.ClientTimeout(total=20)
    t0 = time.time()

    async with aiohttp.ClientSession() as session:
        for idx, sym in enumerate(SYMBOLS):
            all_klines: list[list] = []
            cursor = start_ts

            for page in range(PAGES):
                try:
                    async with session.get(
                        "https://fapi.binance.com/fapi/v1/klines",
                        params={
                            "symbol": sym, "interval": "4h",
                            "limit": 1500, "startTime": cursor,
                        },
                        timeout=timeout,
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            print(f"  {sym} page {page}: HTTP {resp.status} — {text[:100]}")
                            break
                        data = await resp.json()
                        if not data:
                            break
                        all_klines.extend(data)
                        cursor = int(data[-1][0]) + 1
                        if len(data) < 1500:
                            break
                except Exception as e:
                    print(f"  {sym} page {page}: {e}")
                    break
                await asyncio.sleep(0.12)

            if not all_klines:
                print(f"  [{idx+1}/{len(SYMBOLS)}] {sym}: no data")
                await asyncio.sleep(0.3)
                continue

            # Batch insert
            saved = 0
            for k in all_klines:
                ts_ms = int(k[0])
                await db.execute(
                    """INSERT OR IGNORE INTO ohlcv_4h
                       (symbol, ts, open, high, low, close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (sym, ts_ms, float(k[1]), float(k[2]), float(k[3]),
                     float(k[4]), float(k[5])),
                )
                saved += 1
            await db.commit()

            first_dt = datetime.fromtimestamp(all_klines[0][0] / 1000, tz=timezone.utc)
            last_dt = datetime.fromtimestamp(all_klines[-1][0] / 1000, tz=timezone.utc)
            print(f"  [{idx+1}/{len(SYMBOLS)}] {sym}: {saved:,} candles "
                  f"({first_dt.strftime('%Y-%m-%d')} → {last_dt.strftime('%Y-%m-%d')})")

            await asyncio.sleep(0.3)

    r = await db.execute_fetchall("SELECT COUNT(*) as c FROM ohlcv_4h")
    after = r[0]["c"] if r else 0
    added = after - before
    elapsed = time.time() - t0
    print(f"\n  ohlcv_4h after: {after:,} rows (+{added:,} new)")
    print(f"  Done in {elapsed:.1f}s\n")


if __name__ == "__main__":
    asyncio.run(backfill())
