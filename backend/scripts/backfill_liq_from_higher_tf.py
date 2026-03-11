#!/usr/bin/env python3
"""Backfill 4h liquidation data by downsampling higher timeframes from Coinalyze.

Problem: Coinalyze keeps only ~1500 data points per interval. For 4h that's ~8 months.
But 12h gives ~25 months and daily gives 4+ years.

Strategy:
  1. Keep existing native 4h data (Jul 2025+) untouched
  2. Fill Feb 2024 — Jul 2025 from 12h data (each 12h bar → 3 equal 4h bars)
  3. Fill Jan 2022 — Feb 2024 from daily data (each daily bar → 6 equal 4h bars)

Only updates rows where liquidations are currently zero.

Usage:
  cd backend && python3 scripts/backfill_liq_from_higher_tf.py
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timezone

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import init_db, get_db

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT", "AAVEUSDT", "DOTUSDT",
    "FILUSDT", "ATOMUSDT", "TRXUSDT", "JUPUSDT", "SEIUSDT", "TIAUSDT",
    "INJUSDT", "TRUMPUSDT", "WIFUSDT", "TONUSDT", "RENDERUSDT", "ENAUSDT",
]

COINALYZE_API_KEY = os.environ.get("COINALYZE_API_KEY", "")
FOUR_HOURS_MS = 4 * 3600 * 1000
TWELVE_HOURS_MS = 12 * 3600 * 1000
ONE_DAY_MS = 24 * 3600 * 1000

# Only update rows where liq data is zero/null
UPDATE_ZERO_LIQ_SQL = """
UPDATE derivatives_4h
SET liquidations_long = ?,
    liquidations_short = ?,
    liquidations_delta = ?
WHERE symbol = ? AND ts = ?
  AND (liquidations_long IS NULL OR liquidations_long = 0)
  AND (liquidations_short IS NULL OR liquidations_short = 0)
"""

# Insert new rows that don't exist yet (liq-only, no price/oi data)
INSERT_LIQ_ONLY_SQL = """
INSERT OR IGNORE INTO derivatives_4h
  (symbol, ts, close_price, open_interest_usd, funding_rate,
   liquidations_long, liquidations_short, liquidations_delta,
   volume_usd, oi_binance_usd)
VALUES (?, ?, 0, 0, 0, ?, ?, ?, 0, 0)
"""


async def fetch_coinalyze_liquidations(
    session: aiohttp.ClientSession,
    symbols: list[str],
    interval: str,
    start_ts: int,
    end_ts: int,
) -> dict[str, list[tuple[int, float, float]]]:
    """Fetch liquidation history at given interval. Returns {symbol: [(ts_sec, long, short), ...]}."""
    result: dict[str, list[tuple[int, float, float]]] = {}

    for batch_start in range(0, len(symbols), 20):
        batch = symbols[batch_start:batch_start + 20]
        ca_symbols = ",".join(f"{s}_PERP.A" for s in batch)

        try:
            url = (
                f"https://api.coinalyze.net/v1/liquidation-history"
                f"?symbols={ca_symbols}&interval={interval}"
                f"&from={start_ts}&to={end_ts}&convert_to_usd=true"
                f"&api_key={COINALYZE_API_KEY}"
            )
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data:
                        sym = item["symbol"].replace("_PERP.A", "")
                        bars = []
                        for h in item.get("history", []):
                            liq_long = h.get("l", 0) or 0
                            liq_short = h.get("s", 0) or 0
                            bars.append((h["t"], liq_long, liq_short))
                        result[sym] = bars
                else:
                    text = await resp.text()
                    print(f"    Coinalyze liq {interval} HTTP {resp.status}: {text[:200]}")
        except Exception as e:
            print(f"    Coinalyze liq {interval} error: {e}")

        if batch_start + 20 < len(symbols):
            await asyncio.sleep(2)

    return result


def split_12h_to_4h(bars: list[tuple[int, float, float]]) -> list[tuple[int, float, float]]:
    """Split 12h bars into 3 × 4h bars (equal distribution)."""
    result = []
    for ts_sec, liq_long, liq_short in bars:
        ts_ms = ts_sec * 1000
        third_long = liq_long / 3
        third_short = liq_short / 3
        # 12h bar at 00:00 → 4h bars at 00:00, 04:00, 08:00
        # 12h bar at 12:00 → 4h bars at 12:00, 16:00, 20:00
        for offset in range(3):
            bar_ts = ts_ms + offset * FOUR_HOURS_MS
            result.append((bar_ts, third_long, third_short))
    return result


def split_daily_to_4h(bars: list[tuple[int, float, float]]) -> list[tuple[int, float, float]]:
    """Split daily bars into 6 × 4h bars (equal distribution)."""
    result = []
    for ts_sec, liq_long, liq_short in bars:
        ts_ms = ts_sec * 1000
        sixth_long = liq_long / 6
        sixth_short = liq_short / 6
        # Daily bar at 00:00 → 4h bars at 00:00, 04:00, 08:00, 12:00, 16:00, 20:00
        for offset in range(6):
            bar_ts = ts_ms + offset * FOUR_HOURS_MS
            result.append((bar_ts, sixth_long, sixth_short))
    return result


async def backfill():
    await init_db()
    db = get_db()

    # Check current state
    r = await db.execute_fetchall(
        "SELECT COUNT(*) as total FROM derivatives_4h"
    )
    total = r[0]["total"] if r else 0

    r = await db.execute_fetchall(
        "SELECT COUNT(*) as zeros FROM derivatives_4h "
        "WHERE (liquidations_long IS NULL OR liquidations_long = 0) "
        "AND (liquidations_short IS NULL OR liquidations_short = 0)"
    )
    zeros = r[0]["zeros"] if r else 0

    print(f"\n  derivatives_4h: {total:,} rows total, {zeros:,} with zero liquidations ({zeros*100//max(total,1)}%)\n")

    start_ts = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.now(timezone.utc).timestamp())
    t0 = time.time()

    async with aiohttp.ClientSession() as session:
        # ── Phase 1: Fetch 12h liquidation data (~25 months) ──
        print("  [1/2] Fetching 12h liquidations from Coinalyze...")
        liq_12h = await fetch_coinalyze_liquidations(session, SYMBOLS, "12hour", start_ts, end_ts)
        total_12h = sum(len(v) for v in liq_12h.values())
        if liq_12h:
            sample = next(iter(liq_12h.values()))
            if sample:
                first_dt = datetime.fromtimestamp(sample[0][0], tz=timezone.utc)
                last_dt = datetime.fromtimestamp(sample[-1][0], tz=timezone.utc)
                print(f"         {total_12h:,} bars across {len(liq_12h)} symbols")
                print(f"         Range: {first_dt:%Y-%m-%d} → {last_dt:%Y-%m-%d}")
        print()

        # Rate limit between Coinalyze calls (60s to avoid 429)
        await asyncio.sleep(60)

        # ── Phase 2: Fetch daily liquidation data (4+ years) ──
        print("  [2/2] Fetching daily liquidations from Coinalyze...")
        liq_daily = await fetch_coinalyze_liquidations(session, SYMBOLS, "daily", start_ts, end_ts)
        total_daily = sum(len(v) for v in liq_daily.values())
        if liq_daily:
            sample = next(iter(liq_daily.values()))
            if sample:
                first_dt = datetime.fromtimestamp(sample[0][0], tz=timezone.utc)
                last_dt = datetime.fromtimestamp(sample[-1][0], tz=timezone.utc)
                print(f"         {total_daily:,} bars across {len(liq_daily)} symbols")
                print(f"         Range: {first_dt:%Y-%m-%d} → {last_dt:%Y-%m-%d}")
        print()

    # ── Phase 3: Split and insert ──
    print("  Splitting and inserting into derivatives_4h...")
    updated_total = 0
    inserted_total = 0

    for sym in SYMBOLS:
        updated = 0
        inserted = 0

        # Process daily first (lower priority — will be overwritten by 12h if overlap)
        daily_bars = liq_daily.get(sym, [])
        bars_4h_from_daily = split_daily_to_4h(daily_bars)

        for ts_ms, liq_long, liq_short in bars_4h_from_daily:
            if liq_long <= 0 and liq_short <= 0:
                continue
            delta = liq_long - liq_short

            # Try updating existing row with zero liq
            cursor = await db.execute(UPDATE_ZERO_LIQ_SQL, (
                liq_long, liq_short, delta, sym, ts_ms
            ))
            if cursor.rowcount > 0:
                updated += 1
            else:
                # Try inserting new row (IGNORE if row exists with non-zero liq)
                await db.execute(INSERT_LIQ_ONLY_SQL, (
                    sym, ts_ms, liq_long, liq_short, delta
                ))
                if cursor.rowcount >= 0:
                    inserted += 1

        # Then process 12h (higher priority — overwrites daily splits if both zero)
        h12_bars = liq_12h.get(sym, [])
        bars_4h_from_12h = split_12h_to_4h(h12_bars)

        for ts_ms, liq_long, liq_short in bars_4h_from_12h:
            if liq_long <= 0 and liq_short <= 0:
                continue
            delta = liq_long - liq_short

            cursor = await db.execute(UPDATE_ZERO_LIQ_SQL, (
                liq_long, liq_short, delta, sym, ts_ms
            ))
            if cursor.rowcount > 0:
                updated += 1
            else:
                await db.execute(INSERT_LIQ_ONLY_SQL, (
                    sym, ts_ms, liq_long, liq_short, delta
                ))
                if cursor.rowcount >= 0:
                    inserted += 1

        await db.commit()

        daily_range = ""
        if daily_bars:
            d0 = datetime.fromtimestamp(daily_bars[0][0], tz=timezone.utc)
            d1 = datetime.fromtimestamp(daily_bars[-1][0], tz=timezone.utc)
            daily_range = f"daily={len(daily_bars)}({d0:%Y-%m-%d}→{d1:%Y-%m-%d})"
        h12_range = ""
        if h12_bars:
            h0 = datetime.fromtimestamp(h12_bars[0][0], tz=timezone.utc)
            h1 = datetime.fromtimestamp(h12_bars[-1][0], tz=timezone.utc)
            h12_range = f"12h={len(h12_bars)}({h0:%Y-%m-%d}→{h1:%Y-%m-%d})"

        print(f"    {sym}: updated={updated} inserted={inserted} | {daily_range} {h12_range}")
        updated_total += updated
        inserted_total += inserted

    # Final stats
    r = await db.execute_fetchall(
        "SELECT COUNT(*) as zeros FROM derivatives_4h "
        "WHERE (liquidations_long IS NULL OR liquidations_long = 0) "
        "AND (liquidations_short IS NULL OR liquidations_short = 0)"
    )
    zeros_after = r[0]["zeros"] if r else 0

    elapsed = time.time() - t0
    print(f"\n  Summary:")
    print(f"    Updated: {updated_total:,} rows (were zero, now have liq data)")
    print(f"    Inserted: {inserted_total:,} new rows")
    print(f"    Zero-liq rows: {zeros:,} → {zeros_after:,} (fixed {zeros - zeros_after:,})")
    print(f"    Done in {elapsed:.1f}s\n")


if __name__ == "__main__":
    asyncio.run(backfill())
