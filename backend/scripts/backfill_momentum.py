#!/usr/bin/env python3
"""Backfill daily_momentum for all historical dates.

Uses the same logic as momentum_service._compute_all() but iterates
over every date that has daily_derivatives data.

Usage:
  cd backend && python3 scripts/backfill_momentum.py
"""

import asyncio
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import init_db, get_db
from services.derivatives_service import SYMBOLS

MIN_HISTORY = 60


def _decile(rank_pct: float) -> int:
    return max(1, min(10, int(rank_pct * 10) + 1))


def _directional_intensity(returns: list[float], window: int = 20) -> float:
    if len(returns) < window:
        return 0.0
    recent = returns[-window:]
    pos = sum(1 for r in recent if r > 0)
    neg = sum(1 for r in recent if r < 0)
    return round((pos - neg) / len(recent), 4)


def _vol_regime(returns: list[float], short_window: int = 10, long_window: int = 30) -> float:
    if len(returns) < long_window:
        return 0.0
    short_std = (sum(r ** 2 for r in returns[-short_window:]) / short_window) ** 0.5
    long_std = (sum(r ** 2 for r in returns[-long_window:]) / long_window) ** 0.5
    if long_std == 0:
        return 0.0
    return round(short_std - long_std, 6)


def _compute_momentum_value(cs_decile, ts_decile, rel_decile, di, vr) -> float:
    decile_avg = ((cs_decile - 5.5) / 4.5 + (ts_decile - 5.5) / 4.5 + (rel_decile - 5.5) / 4.5) / 3
    vr_signal = 1.0 if vr > 0 else (-1.0 if vr < 0 else 0.0)
    raw = decile_avg * 60 + di * 30 + vr_signal * 10
    return round(max(-100, min(100, raw)), 1)


async def main():
    await init_db()
    db = get_db()
    t0 = time.time()

    # Load all daily_derivatives data per symbol
    print("Loading price + volume data...")
    all_data: dict[str, list[dict]] = {}
    for sym in SYMBOLS:
        rows = await db.execute_fetchall(
            """SELECT date, close_price, volume_usd FROM daily_derivatives
               WHERE symbol = ? AND close_price > 0
               ORDER BY date ASC""",
            (sym,),
        )
        if len(rows) >= MIN_HISTORY:
            all_data[sym] = [{"date": r["date"], "price": r["close_price"], "vol": r["volume_usd"] or 0} for r in rows]

    print(f"  {len(all_data)} symbols loaded")

    # Find all unique dates (after MIN_HISTORY warmup)
    all_dates: set[str] = set()
    for sym, rows in all_data.items():
        for r in rows[MIN_HISTORY:]:
            all_dates.add(r["date"])
    all_dates_sorted = sorted(all_dates)
    print(f"  {len(all_dates_sorted)} dates to process ({all_dates_sorted[0]} → {all_dates_sorted[-1]})")

    # Check existing
    existing = await db.execute_fetchall("SELECT DISTINCT date FROM daily_momentum")
    existing_dates = {r["date"] for r in existing}
    to_process = [d for d in all_dates_sorted if d not in existing_dates]
    print(f"  {len(to_process)} new dates (skipping {len(existing_dates)} existing)")

    if not to_process:
        print("Nothing to backfill.")
        return

    # For each date, compute momentum
    inserted = 0
    batch_size = 100
    batch_values = []

    for di, target_date in enumerate(to_process):
        # Build price/volume arrays up to target_date for each symbol
        prices_up_to: dict[str, list[float]] = {}
        volumes_up_to: dict[str, list[float]] = {}
        for sym, rows in all_data.items():
            p, v = [], []
            for r in rows:
                if r["date"] > target_date:
                    break
                p.append(r["price"])
                v.append(r["vol"])
            if len(p) >= MIN_HISTORY:
                prices_up_to[sym] = p
                volumes_up_to[sym] = v

        if len(prices_up_to) < 5:
            continue

        # 1-month returns
        one_month_returns: dict[str, float] = {}
        one_month_histories: dict[str, list[float]] = {}
        daily_returns: dict[str, list[float]] = {}

        for sym, p in prices_up_to.items():
            dr = [(p[i] - p[i - 1]) / p[i - 1] for i in range(1, len(p)) if p[i - 1] > 0]
            daily_returns[sym] = dr

            if len(p) >= 31:
                one_month_returns[sym] = (p[-1] - p[-31]) / p[-31] if p[-31] > 0 else 0

            hist_rets = []
            for i in range(30, len(p)):
                if p[i - 30] > 0:
                    hist_rets.append((p[i] - p[i - 30]) / p[i - 30])
            one_month_histories[sym] = hist_rets

        if not one_month_returns:
            continue

        # Cross-sectional ranking
        sorted_returns = sorted(one_month_returns.items(), key=lambda x: x[1])
        n_syms = len(sorted_returns)
        cs_ranks = {}
        for rank, (sym, _) in enumerate(sorted_returns):
            cs_ranks[sym] = _decile(rank / n_syms)

        btc_return = one_month_returns.get("BTCUSDT", 0)

        for sym in prices_up_to:
            if sym not in one_month_returns:
                continue
            dr = daily_returns.get(sym, [])
            if len(dr) < 20:
                continue

            cs = cs_ranks.get(sym, 5)

            # Time-series decile
            hist = one_month_histories.get(sym, [])
            current_ret = one_month_returns[sym]
            if len(hist) >= 20:
                below = sum(1 for h in hist if h < current_ret)
                ts = _decile(below / len(hist))
            else:
                ts = 5

            # Relative decile vs BTC
            if sym == "BTCUSDT":
                rel = 5
            else:
                all_rel = {s: one_month_returns.get(s, 0) - btc_return for s in one_month_returns if s != "BTCUSDT"}
                if all_rel:
                    sorted_rel = sorted(all_rel.items(), key=lambda x: x[1])
                    rel = 5
                    for rank, (s, _) in enumerate(sorted_rel):
                        if s == sym:
                            rel = _decile(rank / len(sorted_rel))
                            break
                else:
                    rel = 5

            di_val = _directional_intensity(dr)
            vr = _vol_regime(dr)

            # Relative volume
            vols = volumes_up_to.get(sym, [])
            rel_vol = 1.0
            if len(vols) >= 2:
                current_vol = vols[-1]
                avg_vol = sum(vols[-31:-1]) / min(30, len(vols) - 1) if len(vols) > 1 else 1
                rel_vol = round(current_vol / avg_vol, 2) if avg_vol > 0 else 1.0

            # Proximity to 52w high
            p = prices_up_to[sym]
            high_52w = max(p[-252:]) if len(p) >= 252 else max(p)
            prox_52w = round((high_52w - p[-1]) / high_52w * 100, 1) if high_52w > 0 else 0

            momentum = _compute_momentum_value(cs, ts, rel, di_val, vr)

            batch_values.append((sym, target_date, momentum, cs, ts, rel, di_val, vr, rel_vol, prox_52w))
            inserted += 1

        # Flush batch
        if len(batch_values) >= batch_size * 30:
            await db.executemany(
                """INSERT INTO daily_momentum
                   (symbol, date, momentum_value, cs_decile, ts_decile, rel_decile,
                    directional_intensity, vol_regime, relative_volume, proximity_52w_high)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, date) DO UPDATE SET
                     momentum_value=excluded.momentum_value,
                     cs_decile=excluded.cs_decile,
                     ts_decile=excluded.ts_decile,
                     rel_decile=excluded.rel_decile,
                     directional_intensity=excluded.directional_intensity,
                     vol_regime=excluded.vol_regime,
                     relative_volume=excluded.relative_volume,
                     proximity_52w_high=excluded.proximity_52w_high""",
                batch_values,
            )
            await db.commit()
            batch_values = []

        if (di + 1) % 100 == 0:
            print(f"  [{di+1}/{len(to_process)}] {target_date} — {inserted} rows", flush=True)

    # Final flush
    if batch_values:
        await db.executemany(
            """INSERT INTO daily_momentum
               (symbol, date, momentum_value, cs_decile, ts_decile, rel_decile,
                directional_intensity, vol_regime, relative_volume, proximity_52w_high)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET
                 momentum_value=excluded.momentum_value,
                 cs_decile=excluded.cs_decile,
                 ts_decile=excluded.ts_decile,
                 rel_decile=excluded.rel_decile,
                 directional_intensity=excluded.directional_intensity,
                 vol_regime=excluded.vol_regime,
                 relative_volume=excluded.relative_volume,
                 proximity_52w_high=excluded.proximity_52w_high""",
            batch_values,
        )
        await db.commit()

    elapsed = time.time() - t0
    print(f"\nDone: {inserted} rows inserted in {elapsed:.1f}s")

    # Verify
    count = await db.execute_fetchall("SELECT COUNT(*) as n, MIN(date) as mn, MAX(date) as mx FROM daily_momentum")
    r = count[0]
    print(f"daily_momentum: {r['n']} rows, {r['mn']} → {r['mx']}")


if __name__ == "__main__":
    asyncio.run(main())
