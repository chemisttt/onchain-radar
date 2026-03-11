#!/usr/bin/env python3
"""Backfill derivatives_4h from Jan 2022.

Data sources:
  1. Binance /fapi/v1/klines 4h → close_price, volume_usd
  2. Binance /fapi/v1/fundingRate → funding_rate (3/day → map to nearest 4h bar)
  3. Coinalyze /v1/liquidation-history?interval=4hour → liq_long, liq_short, liq_delta
  4. Coinalyze /v1/open-interest-history?interval=4hour → oi_binance_usd + open_interest_usd

Safe to re-run: ON CONFLICT DO UPDATE with CASE — won't overwrite good data with zeros.

Usage:
  cd backend && python3 scripts/backfill_4h_derivatives.py
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

# 4h bar boundary = ts snapped to 4h (0, 4, 8, 12, 16, 20 UTC)
FOUR_HOURS_MS = 4 * 3600 * 1000

UPSERT_SQL = """
INSERT INTO derivatives_4h
  (symbol, ts, close_price, open_interest_usd, funding_rate,
   liquidations_long, liquidations_short, liquidations_delta,
   volume_usd, oi_binance_usd)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, ts) DO UPDATE SET
  close_price = CASE WHEN excluded.close_price > 0
    THEN excluded.close_price ELSE derivatives_4h.close_price END,
  open_interest_usd = CASE WHEN excluded.open_interest_usd > 0
    THEN excluded.open_interest_usd ELSE derivatives_4h.open_interest_usd END,
  oi_binance_usd = CASE WHEN excluded.oi_binance_usd > 0
    THEN excluded.oi_binance_usd ELSE derivatives_4h.oi_binance_usd END,
  funding_rate = CASE WHEN excluded.funding_rate != 0
    THEN excluded.funding_rate ELSE derivatives_4h.funding_rate END,
  liquidations_long = CASE WHEN excluded.liquidations_long > 0
    THEN excluded.liquidations_long ELSE derivatives_4h.liquidations_long END,
  liquidations_short = CASE WHEN excluded.liquidations_short > 0
    THEN excluded.liquidations_short ELSE derivatives_4h.liquidations_short END,
  liquidations_delta = CASE WHEN excluded.liquidations_long > 0
    THEN excluded.liquidations_delta ELSE derivatives_4h.liquidations_delta END,
  volume_usd = CASE WHEN excluded.volume_usd > 0
    THEN excluded.volume_usd ELSE derivatives_4h.volume_usd END
"""


def _snap_to_4h(ts_ms: int) -> int:
    """Snap timestamp to nearest 4h bar boundary (ms)."""
    return (ts_ms // FOUR_HOURS_MS) * FOUR_HOURS_MS


async def fetch_binance_klines_4h(session: aiohttp.ClientSession, symbol: str, start_ts: int):
    """Fetch 4h klines from Binance Futures. Returns {ts_ms: (close, volume)}."""
    result: dict[int, tuple[float, float]] = {}
    timeout = aiohttp.ClientTimeout(total=20)
    cursor = start_ts

    # 1500 bars/page × 4h = 250 days. 8 pages ≈ 2000 days covers Jan 2022 → Mar 2026.
    for page in range(8):
        try:
            async with session.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": symbol, "interval": "4h", "limit": 1500, "startTime": cursor},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    break
                data = await resp.json()
                if not data:
                    break
                for k in data:
                    ts_ms = int(k[0])
                    result[ts_ms] = (float(k[4]), float(k[7]))  # close, quote_volume
                cursor = int(data[-1][0]) + 1
                if len(data) < 1500:
                    break
        except Exception as e:
            print(f"    klines 4h {symbol} page {page}: {e}")
            break
        await asyncio.sleep(0.12)

    return result


async def fetch_binance_funding(session: aiohttp.ClientSession, symbol: str, start_ts: int):
    """Fetch funding rate history. Returns {ts_ms_snapped: rate} mapped to nearest 4h bar."""
    funding_all: list[dict] = []
    timeout = aiohttp.ClientTimeout(total=20)
    page_ts = start_ts

    # 1000/page, 3 rates/day = ~333 days/page. 5 pages ≈ 1665 days.
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

    # Map each funding settlement to its nearest 4h bar
    result: dict[int, float] = {}
    for item in funding_all:
        ts_ms = int(item.get("fundingTime", 0))
        rate = float(item.get("fundingRate", 0) or 0)
        bar_ts = _snap_to_4h(ts_ms)
        # If multiple funding events map to same bar, average them
        if bar_ts in result:
            result[bar_ts] = (result[bar_ts] + rate) / 2
        else:
            result[bar_ts] = rate

    return result


async def fetch_coinalyze_liquidations_4h(session: aiohttp.ClientSession, symbols: list[str],
                                           start_ts: int, end_ts: int):
    """Fetch 4h liquidation history from Coinalyze. Returns {symbol: {ts_ms: (long, short, delta)}}."""
    result: dict[str, dict[int, tuple[float, float, float]]] = {}

    for batch_start in range(0, len(symbols), 20):
        batch = symbols[batch_start:batch_start + 20]
        ca_symbols = ",".join(f"{s}_PERP.A" for s in batch)

        try:
            url = (
                f"https://api.coinalyze.net/v1/liquidation-history"
                f"?symbols={ca_symbols}&interval=4hour"
                f"&from={start_ts}&to={end_ts}&convert_to_usd=true"
                f"&api_key={COINALYZE_API_KEY}"
            )
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data:
                        sym = item["symbol"].replace("_PERP.A", "")
                        sym_data: dict[int, tuple[float, float, float]] = {}
                        for h in item.get("history", []):
                            ts_sec = h["t"]
                            ts_ms = ts_sec * 1000
                            liq_long = h.get("l", 0) or 0
                            liq_short = h.get("s", 0) or 0
                            sym_data[ts_ms] = (liq_long, liq_short, liq_long - liq_short)
                        result[sym] = sym_data
                else:
                    text = await resp.text()
                    print(f"    Coinalyze liq 4h HTTP {resp.status}: {text[:100]}")
        except Exception as e:
            print(f"    Coinalyze liq 4h error: {e}")

        if batch_start + 20 < len(symbols):
            await asyncio.sleep(2)

    return result


async def fetch_coinalyze_oi_4h(session: aiohttp.ClientSession, symbols: list[str],
                                  start_ts: int, end_ts: int):
    """Fetch 4h OI history from Coinalyze. Returns {symbol: {ts_ms: oi_usd}}."""
    result: dict[str, dict[int, float]] = {}

    for batch_start in range(0, len(symbols), 20):
        batch = symbols[batch_start:batch_start + 20]
        ca_symbols = ",".join(f"{s}_PERP.A" for s in batch)

        try:
            url = (
                f"https://api.coinalyze.net/v1/open-interest-history"
                f"?symbols={ca_symbols}&interval=4hour"
                f"&from={start_ts}&to={end_ts}&convert_to_usd=true"
                f"&api_key={COINALYZE_API_KEY}"
            )
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data:
                        sym = item["symbol"].replace("_PERP.A", "")
                        sym_data: dict[int, float] = {}
                        for h in item.get("history", []):
                            ts_sec = h["t"]
                            ts_ms = ts_sec * 1000
                            oi_close = h.get("c", 0) or 0
                            if oi_close > 0:
                                sym_data[ts_ms] = oi_close
                        result[sym] = sym_data
                else:
                    text = await resp.text()
                    print(f"    Coinalyze OI 4h HTTP {resp.status}: {text[:100]}")
        except Exception as e:
            print(f"    Coinalyze OI 4h error: {e}")

        if batch_start + 20 < len(symbols):
            await asyncio.sleep(2)

    return result


async def backfill():
    await init_db()
    db = get_db()

    r = await db.execute_fetchall("SELECT COUNT(*) as c FROM derivatives_4h")
    before = r[0]["c"] if r else 0
    print(f"\n  derivatives_4h before: {before:,} rows\n")

    start_ts = int(START.timestamp())  # unix seconds for Coinalyze
    start_ms = start_ts * 1000         # ms for Binance
    end_ts = int(datetime.now(timezone.utc).timestamp())
    t0 = time.time()

    async with aiohttp.ClientSession() as session:
        # ── Phase 1: Coinalyze batch calls (liquidations + OI, 4h) ──
        print("  [1/3] Fetching Coinalyze 4h liquidations + OI...")
        liq_data = await fetch_coinalyze_liquidations_4h(session, SYMBOLS, start_ts, end_ts)
        print("         Waiting 30s before OI request (rate limit)...")
        await asyncio.sleep(30)
        oi_data = await fetch_coinalyze_oi_4h(session, SYMBOLS, start_ts, end_ts)
        liq_count = sum(len(v) for v in liq_data.values())
        oi_count = sum(len(v) for v in oi_data.values())
        print(f"         Liquidations 4h: {liq_count:,} data points across {len(liq_data)} symbols")
        print(f"         OI 4h: {oi_count:,} data points across {len(oi_data)} symbols\n")

        # ── Phase 2: Binance per-symbol (4h klines + funding) ──
        print("  [2/3] Fetching Binance 4h klines + funding per symbol...")
        for idx, sym in enumerate(SYMBOLS):
            klines = await fetch_binance_klines_4h(session, sym, start_ms)
            await asyncio.sleep(0.3)
            funding = await fetch_binance_funding(session, sym, start_ms)
            await asyncio.sleep(0.3)

            sym_liq = liq_data.get(sym, {})
            sym_oi = oi_data.get(sym, {})

            # ── Phase 3: Insert/update ──
            saved = 0
            for ts_ms, (close, volume) in sorted(klines.items()):
                fund = funding.get(ts_ms, 0)
                liq = sym_liq.get(ts_ms, (0, 0, 0))
                oi = sym_oi.get(ts_ms, 0)

                await db.execute(UPSERT_SQL, (
                    sym, ts_ms, close, oi, fund,
                    liq[0], liq[1], liq[2],
                    volume, oi,
                ))
                saved += 1

            # Also insert liq/OI bars that don't have kline rows
            extra_ts = (set(sym_liq.keys()) | set(sym_oi.keys())) - set(klines.keys())
            for ts_ms in sorted(extra_ts):
                liq = sym_liq.get(ts_ms, (0, 0, 0))
                oi = sym_oi.get(ts_ms, 0)
                await db.execute(UPSERT_SQL, (
                    sym, ts_ms, 0, oi, 0,
                    liq[0], liq[1], liq[2],
                    0, oi,
                ))
                saved += 1

            await db.commit()

            ts_list = sorted(klines.keys())
            if ts_list:
                first_dt = datetime.fromtimestamp(ts_list[0] / 1000, tz=timezone.utc)
                last_dt = datetime.fromtimestamp(ts_list[-1] / 1000, tz=timezone.utc)
                date_range = f"{first_dt.strftime('%Y-%m-%d')} → {last_dt.strftime('%Y-%m-%d')}"
            else:
                date_range = "no data"
            fund_bars = len(funding)
            liq_bars = len(sym_liq)
            oi_bars = len(sym_oi)
            print(f"  [{idx+1}/{len(SYMBOLS)}] {sym}: {saved} bars ({date_range}) "
                  f"fund={fund_bars} liq={liq_bars} oi={oi_bars}")

    r = await db.execute_fetchall("SELECT COUNT(*) as c FROM derivatives_4h")
    after = r[0]["c"] if r else 0
    added = after - before
    elapsed = time.time() - t0
    print(f"\n  derivatives_4h after: {after:,} rows (+{added:,} new)")
    print(f"  Done in {elapsed:.1f}s\n")


if __name__ == "__main__":
    asyncio.run(backfill())
