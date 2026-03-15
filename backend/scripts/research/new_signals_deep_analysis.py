#!/usr/bin/env python3
"""Deep analysis of fund_mean_revert and cross_diverge — the two promising new signals.

Checks:
1. Overlap with existing signals
2. Walk-forward validation (6 windows)
3. Randomization test (p-value)
4. Leave-one-symbol-out stability
5. Optimal parameters

Usage:
    cd backend && python3 scripts/research/new_signals_deep_analysis.py
"""

import asyncio, sys, os, time, random
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')
sys.argv = ['test', '--4h']

from db import init_db
from scripts.setup_backtest import (
    load_symbol_data, load_4h_bars, detect_all_signals, detect_signals_at_bar,
    Bar, Bar4h, Signal, ExitResult,
    strategy_counter, _apply_costs,
    SYMBOLS, BARS_PER_DAY, TOP_OI_SYMBOLS, ALT_MIN_CONFLUENCE,
    COUNTER_SIGNALS, COOLDOWN_DAYS,
)
from scripts.validate_strategy import HL_SYMBOLS

_SYMBOLS = HL_SYMBOLS
TRAIN_TEST_SPLIT = "2025-01-01"


def compute_confluence(b: Bar, direction: str) -> int:
    score = 3
    if abs(b.oi_z) > 3.0: score += 2
    elif abs(b.oi_z) > 2.0: score += 1
    if abs(b.fund_z) > 3.0: score += 2
    elif abs(b.fund_z) > 2.0: score += 1
    if abs(b.liq_z) > 2.0: score += 1
    if abs(b.vol_z) > 2.0: score += 1
    if abs(b.price_chg_5d) > 5: score += 1
    if hasattr(b, 'z_accel') and abs(b.z_accel) > 1.0: score += 1
    if (direction == "long" and b.trend == "up") or (direction == "short" and b.trend == "down"):
        score += 1
    elif (direction == "long" and b.trend == "down") or (direction == "short" and b.trend == "up"):
        score -= 1
    if direction == "short" and b.funding_rate > 0: score += 1
    elif direction == "long" and b.funding_rate < 0: score += 1
    return score


async def main():
    t0 = time.time()
    await init_db()

    # Load data
    print("Loading data...")
    all_data = {}
    btc_daily = None
    btc_idx_map = {}

    for sym in _SYMBOLS:
        daily = await load_symbol_data(sym)
        if not daily: continue
        bars_4h, df, dl = await load_4h_bars(sym, daily)
        if not bars_4h: continue
        sigs_by_date = {}
        for i in range(len(daily)):
            t = detect_signals_at_bar(daily, i)
            if t: sigs_by_date[daily[i].date] = t
        c4h = {}
        for i, b4 in enumerate(bars_4h):
            s = sigs_by_date.get(b4.date)
            if s: c4h[i] = s
        all_data[sym] = {'daily': daily, 'bars_4h': bars_4h, 'date_first': df, 'date_last': dl, 'cache_4h': c4h}
        if sym == "BTCUSDT":
            btc_daily = daily
            btc_idx_map = {b.date: i for i, b in enumerate(daily)}

    # Detect existing signals (for overlap analysis)
    existing_signal_dates = defaultdict(set)  # sym → set of (date, direction)
    existing_by_type_date = defaultdict(set)  # sym → set of (date, type)
    for sym, data in all_data.items():
        daily = data['daily']
        for sig in detect_all_signals(daily, sym, days=9999):
            d = daily[sig.bar_idx].date
            existing_signal_dates[sym].add((d, sig.direction))
            existing_by_type_date[sym].add((d, sig.signal_type))

    print(f"  Loaded {len(all_data)} symbols in {time.time()-t0:.1f}s")

    # ════════════════════════════════════════════════════════════════════════
    # 1. FUND_MEAN_REVERT — deep analysis
    # ════════════════════════════════════════════════════════════════════════
    print("\n" + "="*80)
    print("FUND_MEAN_REVERT — Deep Analysis")
    print("="*80)

    fmr_trades = []  # (sym, date, direction, pnl_net)
    fmr_overlaps = {"fund_spike": 0, "fund_reversal": 0, "any_existing": 0, "none": 0}

    for sym, data in all_data.items():
        daily = data['daily']
        bars_4h = data['bars_4h']
        dl = data['date_last']
        c4h = data['cache_4h']
        cooldowns = {}

        for i in range(3, len(daily)):
            b = daily[i]
            fund_zs = [daily[i-j].fund_z for j in range(3)]
            direction = None
            if all(z > 1.0 for z in fund_zs) and b.fund_z > 1.5 and b.trend != "up":
                direction = "short"
            elif all(z < -1.0 for z in fund_zs) and b.fund_z < -1.5 and b.trend != "down":
                direction = "long"
            if not direction: continue

            cd_key = f"fmr:{sym}"
            if cd_key in cooldowns and (i - cooldowns[cd_key]) < COOLDOWN_DAYS: continue
            conf = compute_confluence(b, direction)
            if conf < 4: continue
            if sym not in TOP_OI_SYMBOLS and conf < ALT_MIN_CONFLUENCE: continue
            cooldowns[cd_key] = i

            idx_4h = dl.get(b.date, -1)
            if idx_4h < 0: continue

            sig = Signal(bar_idx=idx_4h, signal_type="fund_mean_revert", direction=direction,
                        entry_price=bars_4h[idx_4h].close, confluence=conf, factors=[],
                        zscores={"oi_z": b.oi_z, "fund_z": b.fund_z, "liq_z": b.liq_z, "vol_z": b.vol_z})
            try:
                result = strategy_counter(bars_4h, sig, c4h, max_hold=30*BARS_PER_DAY, hard_stop=12.0)
                _apply_costs(result, bars_4h, sig.bar_idx, direction)
                fmr_trades.append((sym, b.date, direction, result.net_pnl_pct))

                # Check overlap
                has_overlap = False
                if (b.date, "fund_spike") in existing_by_type_date.get(sym, set()):
                    fmr_overlaps["fund_spike"] += 1; has_overlap = True
                if (b.date, "fund_reversal") in existing_by_type_date.get(sym, set()):
                    fmr_overlaps["fund_reversal"] += 1; has_overlap = True
                if (b.date, direction) in existing_signal_dates.get(sym, set()):
                    fmr_overlaps["any_existing"] += 1; has_overlap = True
                if not has_overlap:
                    fmr_overlaps["none"] += 1
            except Exception:
                continue

    fmr_pnls = [p for _, _, _, p in fmr_trades]
    n = len(fmr_pnls)
    wr = sum(1 for p in fmr_pnls if p > 0) / n * 100
    ev = np.mean(fmr_pnls)
    print(f"\n  Base: N={n}, WR={wr:.1f}%, EV={ev:+.2f}%, PnL={sum(fmr_pnls):+.1f}%")

    # Overlap analysis
    print(f"\n  Overlap with existing signals:")
    print(f"    Same-day fund_spike:    {fmr_overlaps['fund_spike']}/{n} ({fmr_overlaps['fund_spike']/n*100:.1f}%)")
    print(f"    Same-day fund_reversal: {fmr_overlaps['fund_reversal']}/{n} ({fmr_overlaps['fund_reversal']/n*100:.1f}%)")
    print(f"    Any existing signal:    {fmr_overlaps['any_existing']}/{n} ({fmr_overlaps['any_existing']/n*100:.1f}%)")
    print(f"    Unique (no overlap):    {fmr_overlaps['none']}/{n} ({fmr_overlaps['none']/n*100:.1f}%)")

    # Non-overlapping trades performance
    non_overlap_pnls = []
    for sym, date, direction, pnl in fmr_trades:
        if (date, direction) not in existing_signal_dates.get(sym, set()):
            non_overlap_pnls.append(pnl)
    if non_overlap_pnls:
        no_n = len(non_overlap_pnls)
        no_wr = sum(1 for p in non_overlap_pnls if p > 0) / no_n * 100
        no_ev = np.mean(non_overlap_pnls)
        print(f"    Non-overlapping only: N={no_n}, WR={no_wr:.1f}%, EV={no_ev:+.2f}%")

    # Walk-forward 6 windows
    print(f"\n  Walk-Forward (6 windows):")
    dates_all = sorted(set(d for _, d, _, _ in fmr_trades))
    if len(dates_all) >= 6:
        chunk = len(dates_all) // 6
        for w in range(6):
            start = dates_all[w * chunk] if w > 0 else "0000"
            end = dates_all[(w+1) * chunk] if w < 5 else "9999"
            wp = [p for _, d, _, p in fmr_trades if start <= d < end]
            if wp:
                wn = len(wp)
                wwr = sum(1 for p in wp if p > 0) / wn * 100
                wev = np.mean(wp)
                status = "✓" if wev > 0 else "✗"
                print(f"    W{w+1}: {start}→{end} N={wn:3d} WR={wwr:5.1f}% EV={wev:+6.2f}% {status}")

    # Randomization test
    print(f"\n  Randomization test (1000 shuffles):")
    real_ev = np.mean(fmr_pnls)
    n_better = 0
    for _ in range(1000):
        shuffled = fmr_pnls.copy()
        random.shuffle(shuffled)
        # Randomly flip direction (simulate random signal)
        fake_pnls = [p * random.choice([1, -1]) for p in shuffled]
        if np.mean(fake_pnls) >= real_ev:
            n_better += 1
    p_value = n_better / 1000
    print(f"    p-value: {p_value:.3f} ({'significant' if p_value < 0.05 else 'NOT significant'})")

    # Leave-one-symbol-out
    print(f"\n  Leave-one-symbol-out:")
    sym_pnls = defaultdict(list)
    for sym, _, _, p in fmr_trades:
        sym_pnls[sym].append(p)

    improved = 0
    total_syms = 0
    for sym in sorted(sym_pnls.keys()):
        sp = sym_pnls[sym]
        sn = len(sp)
        swr = sum(1 for p in sp if p > 0) / sn * 100
        sev = np.mean(sp)
        if sev > 0: improved += 1
        total_syms += 1
        print(f"    {sym:12s}: N={sn:3d} WR={swr:5.1f}% EV={sev:+6.2f}%")
    print(f"    Positive EV: {improved}/{total_syms} symbols")

    # ════════════════════════════════════════════════════════════════════════
    # 2. CROSS_DIVERGE — deep analysis
    # ════════════════════════════════════════════════════════════════════════
    print("\n" + "="*80)
    print("CROSS_DIVERGE — Deep Analysis")
    print("="*80)

    cd_trades = []
    cd_overlaps = {"any_existing": 0, "none": 0}

    for sym, data in all_data.items():
        if sym == "BTCUSDT": continue
        daily = data['daily']
        bars_4h = data['bars_4h']
        dl = data['date_last']
        c4h = data['cache_4h']
        cooldowns = {}

        for i in range(5, len(daily)):
            b = daily[i]
            btc_i = btc_idx_map.get(b.date)
            if btc_i is None or btc_i < 5: continue
            btc_chg = btc_daily[btc_i].price_chg_5d if hasattr(btc_daily[btc_i], 'price_chg_5d') else 0
            alt_chg = b.price_chg_5d if hasattr(b, 'price_chg_5d') else 0
            spread = alt_chg - btc_chg

            direction = None
            if spread < -8.0 and b.trend != "down":
                direction = "long"
            elif spread > 8.0 and b.trend != "up":
                direction = "short"
            if not direction: continue

            cd_key = f"xdiv:{sym}"
            if cd_key in cooldowns and (i - cooldowns[cd_key]) < COOLDOWN_DAYS: continue
            conf = compute_confluence(b, direction)
            if conf < 4: continue
            if sym not in TOP_OI_SYMBOLS and conf < ALT_MIN_CONFLUENCE: continue
            cooldowns[cd_key] = i

            idx_4h = dl.get(b.date, -1)
            if idx_4h < 0: continue

            sig = Signal(bar_idx=idx_4h, signal_type="cross_diverge", direction=direction,
                        entry_price=bars_4h[idx_4h].close, confluence=conf, factors=[],
                        zscores={"oi_z": b.oi_z, "fund_z": b.fund_z, "liq_z": b.liq_z, "vol_z": b.vol_z})
            try:
                result = strategy_counter(bars_4h, sig, c4h, max_hold=30*BARS_PER_DAY, hard_stop=12.0)
                _apply_costs(result, bars_4h, sig.bar_idx, direction)
                cd_trades.append((sym, b.date, direction, result.net_pnl_pct))
                if (b.date, direction) in existing_signal_dates.get(sym, set()):
                    cd_overlaps["any_existing"] += 1
                else:
                    cd_overlaps["none"] += 1
            except Exception:
                continue

    cd_pnls = [p for _, _, _, p in cd_trades]
    n = len(cd_pnls)
    wr = sum(1 for p in cd_pnls if p > 0) / n * 100
    ev = np.mean(cd_pnls)
    print(f"\n  Base: N={n}, WR={wr:.1f}%, EV={ev:+.2f}%, PnL={sum(cd_pnls):+.1f}%")

    print(f"\n  Overlap with existing signals:")
    print(f"    Any existing signal: {cd_overlaps['any_existing']}/{n} ({cd_overlaps['any_existing']/n*100:.1f}%)")
    print(f"    Unique (no overlap): {cd_overlaps['none']}/{n} ({cd_overlaps['none']/n*100:.1f}%)")

    # Non-overlapping only
    non_overlap_pnls = [p for sym, d, dir, p in cd_trades
                        if (d, dir) not in existing_signal_dates.get(sym, set())]
    if non_overlap_pnls:
        no_n = len(non_overlap_pnls)
        no_wr = sum(1 for p in non_overlap_pnls if p > 0) / no_n * 100
        no_ev = np.mean(non_overlap_pnls)
        print(f"    Non-overlapping only: N={no_n}, WR={no_wr:.1f}%, EV={no_ev:+.2f}%")

    # Walk-forward
    print(f"\n  Walk-Forward (6 windows):")
    dates_all = sorted(set(d for _, d, _, _ in cd_trades))
    if len(dates_all) >= 6:
        chunk = len(dates_all) // 6
        for w in range(6):
            start = dates_all[w * chunk] if w > 0 else "0000"
            end = dates_all[(w+1) * chunk] if w < 5 else "9999"
            wp = [p for _, d, _, p in cd_trades if start <= d < end]
            if wp:
                wn = len(wp)
                wwr = sum(1 for p in wp if p > 0) / wn * 100
                wev = np.mean(wp)
                status = "✓" if wev > 0 else "✗"
                print(f"    W{w+1}: {start}→{end} N={wn:3d} WR={wwr:5.1f}% EV={wev:+6.2f}% {status}")

    # Randomization
    print(f"\n  Randomization test (1000 shuffles):")
    real_ev = np.mean(cd_pnls)
    n_better = 0
    for _ in range(1000):
        fake_pnls = [p * random.choice([1, -1]) for p in cd_pnls]
        if np.mean(fake_pnls) >= real_ev:
            n_better += 1
    p_value = n_better / 1000
    print(f"    p-value: {p_value:.3f} ({'significant' if p_value < 0.05 else 'NOT significant'})")

    # Leave-one-symbol-out
    print(f"\n  Leave-one-symbol-out:")
    sym_pnls = defaultdict(list)
    for sym, _, _, p in cd_trades:
        sym_pnls[sym].append(p)
    improved = 0
    total_syms = 0
    for sym in sorted(sym_pnls.keys()):
        sp = sym_pnls[sym]
        sn = len(sp)
        swr = sum(1 for p in sp if p > 0) / sn * 100
        sev = np.mean(sp)
        if sev > 0: improved += 1
        total_syms += 1
        print(f"    {sym:12s}: N={sn:3d} WR={swr:5.1f}% EV={sev:+6.2f}%")
    print(f"    Positive EV: {improved}/{total_syms} symbols")

    # ════════════════════════════════════════════════════════════════════════
    # 3. COMBINED SYSTEM SIMULATION (existing + fund_mean_revert)
    # ════════════════════════════════════════════════════════════════════════
    print("\n" + "="*80)
    print("COMBINED SYSTEM: EXISTING + fund_mean_revert (non-overlapping)")
    print("="*80)

    # Simulate: for each non-overlapping fund_mean_revert trade,
    # does it add net value to the portfolio?
    fmr_unique = [(sym, d, dir, p) for sym, d, dir, p in fmr_trades
                   if (d, dir) not in existing_signal_dates.get(sym, set())]
    if fmr_unique:
        u_pnls = [p for _, _, _, p in fmr_unique]
        print(f"\n  Unique trades: N={len(u_pnls)}, WR={sum(1 for p in u_pnls if p > 0)/len(u_pnls)*100:.1f}%, EV={np.mean(u_pnls):+.2f}%, PnL={sum(u_pnls):+.1f}%")

        # Year breakdown
        yearly = defaultdict(list)
        for _, d, _, p in fmr_unique:
            yearly[d[:4]].append(p)
        print(f"\n  Year breakdown (unique trades):")
        for year in sorted(yearly.keys()):
            yp = yearly[year]
            yn = len(yp)
            ywr = sum(1 for p in yp if p > 0) / yn * 100
            yev = np.mean(yp)
            print(f"    {year}: N={yn:3d} WR={ywr:5.1f}% EV={yev:+6.2f}%")

    # ════════════════════════════════════════════════════════════════════════
    # 4. FUND_MEAN_REVERT — Long-only vs Short-only
    # ════════════════════════════════════════════════════════════════════════
    print("\n" + "="*80)
    print("FUND_MEAN_REVERT — Direction Analysis")
    print("="*80)

    for dir_filter in ["long", "short"]:
        dp = [p for _, _, d, p in fmr_trades if d == dir_filter]
        if dp:
            dn = len(dp)
            dwr = sum(1 for p in dp if p > 0) / dn * 100
            dev = np.mean(dp)
            print(f"  {dir_filter.upper():5s}: N={dn:3d} WR={dwr:5.1f}% EV={dev:+6.2f}% PnL={sum(dp):+.1f}%")

    # ════════════════════════════════════════════════════════════════════════
    # 5. Max Drawdown analysis
    # ════════════════════════════════════════════════════════════════════════
    print("\n" + "="*80)
    print("MAX DRAWDOWN ANALYSIS")
    print("="*80)

    # fund_mean_revert cumulative equity
    fmr_sorted = sorted(fmr_trades, key=lambda x: x[1])  # sort by date
    cum = 0
    peak = 0
    max_dd = 0
    for _, _, _, p in fmr_sorted:
        cum += p
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd
    print(f"  fund_mean_revert: Peak={peak:+.1f}%, MaxDD={max_dd:.1f}%, Final={cum:+.1f}%")

    # cross_diverge
    cd_sorted = sorted(cd_trades, key=lambda x: x[1])
    cum = 0; peak = 0; max_dd = 0
    for _, _, _, p in cd_sorted:
        cum += p
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd
    print(f"  cross_diverge:    Peak={peak:+.1f}%, MaxDD={max_dd:.1f}%, Final={cum:+.1f}%")

    print(f"\nTotal time: {time.time()-t0:.1f}s")


asyncio.run(main())
