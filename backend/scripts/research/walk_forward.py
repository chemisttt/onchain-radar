#!/usr/bin/env python3
"""Walk-forward validation — rolling train/test windows.

Splits 3+ years of data into overlapping windows:
  train on N months, test on M months, slide forward.

Reports per-window and aggregate statistics to detect overfitting.

Usage:
  cd backend && python3 scripts/walk_forward.py --train-months 12 --test-months 6
  cd backend && python3 scripts/walk_forward.py --4h --train-months 12 --test-months 6
"""

import asyncio
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force --4h mode if requested (must be before setup_backtest import)
USE_4H = "--4h" in sys.argv

from db import init_db
from scripts.setup_backtest import (
    load_symbol_data, load_4h_bars, detect_all_signals, detect_signals_at_bar,
    SYMBOLS, STRATEGIES, ExitResult, Signal, Bar, Bar4h,
    _compute_stats, _apply_costs, BARS_PER_DAY, USE_4H_EXIT,
)


def _parse_arg(name: str, default: int) -> int:
    for i, arg in enumerate(sys.argv):
        if arg == name and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                pass
    return default


async def walk_forward():
    await init_db()

    train_months = _parse_arg("--train-months", 12)
    test_months = _parse_arg("--test-months", 6)
    mode_label = "4h exit" if USE_4H_EXIT else "daily"

    print(f"  Walk-Forward Validation")
    print(f"  Mode: {mode_label}, Train: {train_months}mo, Test: {test_months}mo")
    print()

    # Load all data
    t0 = time.time()
    all_daily: dict[str, list[Bar]] = {}
    all_4h: dict[str, tuple] = {}  # sym → (bars_4h, date_first, date_last)

    for sym in SYMBOLS:
        daily = await load_symbol_data(sym)
        if not daily:
            continue
        all_daily[sym] = daily
        if USE_4H_EXIT:
            bars_4h, df, dl = await load_4h_bars(sym, daily)
            if bars_4h:
                all_4h[sym] = (bars_4h, df, dl)

    # Determine date range
    all_dates = set()
    for daily in all_daily.values():
        for b in daily:
            all_dates.add(b.date)
    if not all_dates:
        print("  No data found.")
        return

    min_date = min(all_dates)
    max_date = max(all_dates)
    print(f"  Data range: {min_date} to {max_date}")
    print(f"  Loaded in {time.time() - t0:.1f}s")
    print()

    # Generate windows
    window_size = train_months + test_months
    start = datetime.strptime(min_date, "%Y-%m-%d")
    end = datetime.strptime(max_date, "%Y-%m-%d")

    windows = []
    cursor = start
    while True:
        train_start = cursor
        train_end = train_start + timedelta(days=train_months * 30)
        test_start = train_end
        test_end = test_start + timedelta(days=test_months * 30)

        if test_end > end + timedelta(days=30):
            break

        windows.append({
            "train_start": train_start.strftime("%Y-%m-%d"),
            "train_end": train_end.strftime("%Y-%m-%d"),
            "test_start": test_start.strftime("%Y-%m-%d"),
            "test_end": test_end.strftime("%Y-%m-%d"),
        })

        # Slide by test_months
        cursor += timedelta(days=test_months * 30)

    if not windows:
        print("  Not enough data for walk-forward windows.")
        return

    print(f"  Windows: {len(windows)}")
    print()

    # Strategy to test (Adaptive = F)
    target_strategy = "F: Adaptive"

    # Run each window
    print(f"  {'Window':<8} {'Train':>22} {'Test':>22} {'Train_N':>8} {'Test_N':>7} {'Train_EV':>10} {'Test_EV':>9} {'Train_WR':>9} {'Test_WR':>8}")
    print("  " + "-" * 110)

    all_train_evs = []
    all_test_evs = []

    for wi, w in enumerate(windows):
        train_results = []
        test_results = []

        for sym, daily in all_daily.items():
            signals = detect_all_signals(daily, sym, days=9999)
            if not signals:
                continue

            train_sigs = [s for s in signals if w["train_start"] <= daily[s.bar_idx].date < w["train_end"]]
            test_sigs = [s for s in signals if w["test_start"] <= daily[s.bar_idx].date < w["test_end"]]

            if USE_4H_EXIT and sym in all_4h:
                bars_4h, df, dl = all_4h[sym]
                # Build signal cache for this symbol
                sigs_by_date = {}
                for i in range(len(daily)):
                    t = detect_signals_at_bar(daily, i)
                    if t:
                        sigs_by_date[daily[i].date] = t
                cache_4h = {}
                for i, b4 in enumerate(bars_4h):
                    s = sigs_by_date.get(b4.date)
                    if s:
                        cache_4h[i] = s

                for sig_set, result_list in [(train_sigs, train_results), (test_sigs, test_results)]:
                    for sig in sig_set:
                        sig_date = daily[sig.bar_idx].date
                        idx_4h = dl.get(sig_date, -1)
                        if idx_4h < 0:
                            continue
                        sig.bar_idx_4h = idx_4h
                        sig.entry_price = bars_4h[idx_4h].close
                        orig = sig.bar_idx
                        sig.bar_idx = idx_4h
                        sf = STRATEGIES[target_strategy]
                        r = sf(bars_4h, sig, cache_4h)
                        if r:
                            _apply_costs(r, bars_4h, idx_4h, sig.direction)
                            result_list.append(r)
                        sig.bar_idx = orig
            else:
                # Daily mode
                cache = {}
                for i in range(len(daily)):
                    t = detect_signals_at_bar(daily, i)
                    if t:
                        cache[i] = t

                for sig_set, result_list in [(train_sigs, train_results), (test_sigs, test_results)]:
                    for sig in sig_set:
                        sf = STRATEGIES[target_strategy]
                        r = sf(daily, sig, cache)
                        if r:
                            _apply_costs(r, daily, sig.bar_idx, sig.direction)
                            result_list.append(r)

        train_s = _compute_stats(train_results)
        test_s = _compute_stats(test_results)

        train_ev = train_s.get("net_ev", 0) if train_s else 0
        test_ev = test_s.get("net_ev", 0) if test_s else 0
        train_wr = train_s.get("wr", 0) if train_s else 0
        test_wr = test_s.get("wr", 0) if test_s else 0
        train_n = train_s.get("trades", 0) if train_s else 0
        test_n = test_s.get("trades", 0) if test_s else 0

        all_train_evs.append(train_ev)
        all_test_evs.append(test_ev)

        label = f"W{wi+1}"
        train_range = f"{w['train_start']}..{w['train_end']}"
        test_range = f"{w['test_start']}..{w['test_end']}"
        print(f"  {label:<8} {train_range:>22} {test_range:>22} {train_n:>8} {test_n:>7} "
              f"{train_ev:>+9.2f}% {test_ev:>+8.2f}% {train_wr:>8.1f}% {test_wr:>7.1f}%")

    # Summary
    print()
    print("=" * 80)
    print("  WALK-FORWARD SUMMARY")
    print("=" * 80)

    import statistics
    if all_train_evs:
        print(f"  Train EV: avg {statistics.mean(all_train_evs):+.2f}%, "
              f"std {statistics.stdev(all_train_evs):.2f}%" if len(all_train_evs) > 1 else
              f"  Train EV: {all_train_evs[0]:+.2f}%")
    if all_test_evs:
        print(f"  Test EV:  avg {statistics.mean(all_test_evs):+.2f}%, "
              f"std {statistics.stdev(all_test_evs):.2f}%" if len(all_test_evs) > 1 else
              f"  Test EV:  {all_test_evs[0]:+.2f}%")
        if all_train_evs:
            avg_train = statistics.mean(all_train_evs)
            avg_test = statistics.mean(all_test_evs)
            if avg_train != 0:
                retention = avg_test / avg_train * 100
                print(f"  EV retention: {retention:.0f}% (test/train)")
            pos_windows = sum(1 for ev in all_test_evs if ev > 0)
            print(f"  Positive test windows: {pos_windows}/{len(all_test_evs)}")
            worst = min(all_test_evs)
            print(f"  Worst test window: {worst:+.2f}%")

    print()


if __name__ == "__main__":
    asyncio.run(walk_forward())
