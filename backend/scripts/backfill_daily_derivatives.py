#!/usr/bin/env python3
"""Backfill daily_derivatives from Feb 2022.

Data sources:
  1. Binance /fapi/v1/klines 1d → close_price, volume_usd
  2. Binance /fapi/v1/fundingRate → funding_rate (3/day → avg)
  3. Coinalyze /v1/liquidation-history → liq_long, liq_short, liq_delta
  4. Coinalyze /v1/open-interest-history → oi_binance_usd + open_interest_usd

Safe to re-run: ON CONFLICT DO UPDATE with COALESCE/CASE — won't overwrite good data with zeros.

Usage:
  cd backend && python3 scripts/backfill_daily_derivatives.py
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

COINALYZE_API_KEY = os.environ.get("COINALYZE_API_KEY", "")
START = datetime(2022, 1, 1, tzinfo=timezone.utc)

UPSERT_SQL = """
INSERT INTO daily_derivatives
  (symbol, date, close_price, open_interest_usd, funding_rate,
   liquidations_long, liquidations_short, liquidations_delta,
   volume_usd, oi_binance_usd)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, date) DO UPDATE SET
  close_price = CASE WHEN excluded.close_price > 0
    THEN excluded.close_price ELSE daily_derivatives.close_price END,
  open_interest_usd = CASE WHEN excluded.open_interest_usd > 0
    THEN excluded.open_interest_usd ELSE daily_derivatives.open_interest_usd END,
  oi_binance_usd = CASE WHEN excluded.oi_binance_usd > 0
    THEN excluded.oi_binance_usd ELSE daily_derivatives.oi_binance_usd END,
  funding_rate = CASE WHEN excluded.funding_rate != 0
    THEN excluded.funding_rate ELSE daily_derivatives.funding_rate END,
  liquidations_long = CASE WHEN excluded.liquidations_long > 0
    THEN excluded.liquidations_long ELSE daily_derivatives.liquidations_long END,
  liquidations_short = CASE WHEN excluded.liquidations_short > 0
    THEN excluded.liquidations_short ELSE daily_derivatives.liquidations_short END,
  liquidations_delta = CASE WHEN excluded.liquidations_long > 0
    THEN excluded.liquidations_delta ELSE daily_derivatives.liquidations_delta END,
  volume_usd = CASE WHEN excluded.volume_usd > 0
    THEN excluded.volume_usd ELSE daily_derivatives.volume_usd END
"""


async def fetch_binance_klines(session: aiohttp.ClientSession, symbol: str, start_ts: int):
    """Fetch daily klines from Binance Futures. Returns {date: (close, volume)}."""
    result: dict[str, tuple[float, float]] = {}
    timeout = aiohttp.ClientTimeout(total=20)
    cursor = start_ts

    # 1500 bars/page, ~4.1 years. 2 pages covers 2022→2030.
    for page in range(2):
        try:
            async with session.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": symbol, "interval": "1d", "limit": 1500, "startTime": cursor},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    break
                data = await resp.json()
                if not data:
                    break
                for k in data:
                    ts = int(k[0])
                    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                    result[dt] = (float(k[4]), float(k[7]))  # close, quote_volume
                cursor = int(data[-1][0]) + 1
                if len(data) < 1500:
                    break
        except Exception as e:
            print(f"    klines {symbol} page {page}: {e}")
            break
        await asyncio.sleep(0.12)

    return result


async def fetch_binance_funding(session: aiohttp.ClientSession, symbol: str, start_ts: int):
    """Fetch funding rate history. Returns {date: avg_rate}."""
    funding_all: list[dict] = []
    timeout = aiohttp.ClientTimeout(total=20)
    page_ts = start_ts

    # 1000/page, 3 rates/day = ~333 days/page. 5 pages = ~1665 days (~4.5 years).
    for _ in range(5):
        try:
            async with session.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": symbol, "limit": 1000, "startTime": page_ts},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    break
                page = await resp.json()
                if not page:
                    break
                funding_all.extend(page)
                page_ts = int(page[-1].get("fundingTime", 0)) + 1
                if len(page) < 1000:
                    break
        except Exception as e:
            print(f"    funding {symbol}: {e}")
            break
        await asyncio.sleep(0.12)

    # Aggregate 3x daily to average
    daily: dict[str, list[float]] = {}
    for item in funding_all:
        ts = int(item.get("fundingTime", 0))
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        rate = float(item.get("fundingRate", 0) or 0)
        daily.setdefault(dt, []).append(rate)

    return {dt: sum(rs) / len(rs) for dt, rs in daily.items()}


async def fetch_coinalyze_liquidations(session: aiohttp.ClientSession, symbols: list[str],
                                        start_ts: int, end_ts: int):
    """Fetch liquidation history from Coinalyze. Returns {symbol: {date: (long, short, delta)}}."""
    result: dict[str, dict[str, tuple[float, float, float]]] = {}

    for batch_start in range(0, len(symbols), 20):
        batch = symbols[batch_start:batch_start + 20]
        ca_symbols = ",".join(f"{s}_PERP.A" for s in batch)

        try:
            url = (
                f"https://api.coinalyze.net/v1/liquidation-history"
                f"?symbols={ca_symbols}&interval=daily"
                f"&from={start_ts}&to={end_ts}&convert_to_usd=true"
                f"&api_key={COINALYZE_API_KEY}"
            )
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data:
                        sym = item["symbol"].replace("_PERP.A", "")
                        sym_data: dict[str, tuple[float, float, float]] = {}
                        for h in item.get("history", []):
                            dt = datetime.fromtimestamp(h["t"], tz=timezone.utc).strftime("%Y-%m-%d")
                            liq_long = h.get("l", 0) or 0
                            liq_short = h.get("s", 0) or 0
                            sym_data[dt] = (liq_long, liq_short, liq_long - liq_short)
                        result[sym] = sym_data
                else:
                    text = await resp.text()
                    print(f"    Coinalyze liq HTTP {resp.status}: {text[:100]}")
        except Exception as e:
            print(f"    Coinalyze liq error: {e}")

        if batch_start + 20 < len(symbols):
            await asyncio.sleep(2)

    return result


async def fetch_coinalyze_oi(session: aiohttp.ClientSession, symbols: list[str],
                              start_ts: int, end_ts: int):
    """Fetch OI history from Coinalyze. Returns {symbol: {date: oi_usd}}."""
    result: dict[str, dict[str, float]] = {}

    for batch_start in range(0, len(symbols), 20):
        batch = symbols[batch_start:batch_start + 20]
        ca_symbols = ",".join(f"{s}_PERP.A" for s in batch)

        try:
            url = (
                f"https://api.coinalyze.net/v1/open-interest-history"
                f"?symbols={ca_symbols}&interval=daily"
                f"&from={start_ts}&to={end_ts}&convert_to_usd=true"
                f"&api_key={COINALYZE_API_KEY}"
            )
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data:
                        sym = item["symbol"].replace("_PERP.A", "")
                        sym_data: dict[str, float] = {}
                        for h in item.get("history", []):
                            dt = datetime.fromtimestamp(h["t"], tz=timezone.utc).strftime("%Y-%m-%d")
                            oi_close = h.get("c", 0) or 0
                            if oi_close > 0:
                                sym_data[dt] = oi_close
                        result[sym] = sym_data
                else:
                    text = await resp.text()
                    print(f"    Coinalyze OI HTTP {resp.status}: {text[:100]}")
        except Exception as e:
            print(f"    Coinalyze OI error: {e}")

        if batch_start + 20 < len(symbols):
            await asyncio.sleep(2)

    return result


async def backfill():
    await init_db()
    db = get_db()

    r = await db.execute_fetchall("SELECT COUNT(*) as c FROM daily_derivatives")
    before = r[0]["c"] if r else 0
    print(f"\n  daily_derivatives before: {before:,} rows\n")

    start_ts = int(START.timestamp())  # unix seconds for Coinalyze
    start_ms = start_ts * 1000         # ms for Binance
    end_ts = int(datetime.now(timezone.utc).timestamp())
    t0 = time.time()

    async with aiohttp.ClientSession() as session:
        # ── Phase 1: Coinalyze batch calls (liquidations + OI) ──
        print("  [1/3] Fetching Coinalyze liquidations + OI...")
        liq_data = await fetch_coinalyze_liquidations(session, SYMBOLS, start_ts, end_ts)
        oi_data = await fetch_coinalyze_oi(session, SYMBOLS, start_ts, end_ts)
        liq_count = sum(len(v) for v in liq_data.values())
        oi_count = sum(len(v) for v in oi_data.values())
        print(f"         Liquidations: {liq_count:,} data points across {len(liq_data)} symbols")
        print(f"         OI: {oi_count:,} data points across {len(oi_data)} symbols\n")

        # ── Phase 2: Binance per-symbol (klines + funding) ──
        print("  [2/3] Fetching Binance klines + funding per symbol...")
        for idx, sym in enumerate(SYMBOLS):
            klines = await fetch_binance_klines(session, sym, start_ms)
            await asyncio.sleep(0.3)
            funding = await fetch_binance_funding(session, sym, start_ms)
            await asyncio.sleep(0.3)

            sym_liq = liq_data.get(sym, {})
            sym_oi = oi_data.get(sym, {})

            # ── Phase 3: Insert/update ──
            saved = 0
            for dt, (close, volume) in sorted(klines.items()):
                fund = funding.get(dt, 0)
                liq = sym_liq.get(dt, (0, 0, 0))
                oi = sym_oi.get(dt, 0)

                await db.execute(UPSERT_SQL, (
                    sym, dt, close, oi, fund,
                    liq[0], liq[1], liq[2],
                    volume, oi,
                ))
                saved += 1

            # Also insert liq/OI days that don't have kline rows (shouldn't happen much, but safe)
            extra_dates = (set(sym_liq.keys()) | set(sym_oi.keys())) - set(klines.keys())
            for dt in sorted(extra_dates):
                liq = sym_liq.get(dt, (0, 0, 0))
                oi = sym_oi.get(dt, 0)
                await db.execute(UPSERT_SQL, (
                    sym, dt, 0, oi, 0,
                    liq[0], liq[1], liq[2],
                    0, oi,
                ))
                saved += 1

            await db.commit()

            dates = sorted(klines.keys())
            date_range = f"{dates[0]} → {dates[-1]}" if dates else "no data"
            fund_days = len(funding)
            liq_days = len(sym_liq)
            oi_days = len(sym_oi)
            print(f"  [{idx+1}/{len(SYMBOLS)}] {sym}: {saved} days ({date_range}) "
                  f"fund={fund_days} liq={liq_days} oi={oi_days}")

    r = await db.execute_fetchall("SELECT COUNT(*) as c FROM daily_derivatives")
    after = r[0]["c"] if r else 0
    added = after - before
    elapsed = time.time() - t0
    print(f"\n  daily_derivatives after: {after:,} rows (+{added:,} new)")
    print(f"  Done in {elapsed:.1f}s\n")


if __name__ == "__main__":
    asyncio.run(backfill())
