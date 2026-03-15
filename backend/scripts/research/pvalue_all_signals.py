#!/usr/bin/env python3
"""Randomization p-value test for ALL existing signal types.

For each signal type: shuffle direction 1000 times, measure how often
random EV ≥ real EV. Lower p = stronger edge.

Usage:
    cd backend && python3 scripts/research/pvalue_all_signals.py
"""

import asyncio, sys, os, time, random
from collections import defaultdict

import numpy as np

sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')
sys.argv = ['test', '--4h']

from db import init_db
from scripts.setup_backtest import (
    load_symbol_data, load_4h_bars, detect_all_signals, detect_signals_at_bar,
    Signal, strategy_fixed, strategy_zscore, strategy_counter,
    strategy_trailing, strategy_hybrid, strategy_trail_counter,
    _apply_costs, BARS_PER_DAY, ADAPTIVE_EXIT,
)
from scripts.validate_strategy import HL_SYMBOLS

_SYMBOLS = HL_SYMBOLS
N_SHUFFLES = 10000

EXIT_STRATEGIES = {
    "fixed": lambda bars, sig, cache: strategy_fixed(bars, sig, timeout=7 * BARS_PER_DAY),
    "zscore_mr": lambda bars, sig, cache: strategy_zscore(bars, sig, max_hold=30 * BARS_PER_DAY),
    "counter_sig": lambda bars, sig, cache: strategy_counter(bars, sig, cache,
                                                              max_hold=30 * BARS_PER_DAY, hard_stop=12.0),
    "trail_atr": lambda bars, sig, cache: strategy_trailing(bars, sig, max_hold=30 * BARS_PER_DAY),
    "hybrid": lambda bars, sig, cache: strategy_hybrid(bars, sig, cache, max_hold=30 * BARS_PER_DAY),
    "trail_counter": lambda bars, sig, cache: strategy_trail_counter(bars, sig, cache,
                                                                      max_hold=30 * BARS_PER_DAY, hard_stop=12.0),
}


async def main():
    t0 = time.time()
    await init_db()

    print("Loading data...")
    all_data = {}
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
        all_data[sym] = {'daily': daily, 'bars_4h': bars_4h, 'date_last': dl, 'cache_4h': c4h}

    print(f"  Loaded {len(all_data)} symbols in {time.time()-t0:.1f}s\n")

    # Collect trades per signal type
    trades_by_type = defaultdict(list)  # sig_type → list of net_pnl
    all_trades = []

    for sym, data in all_data.items():
        daily = data['daily']
        bars_4h = data['bars_4h']
        dl = data['date_last']
        c4h = data['cache_4h']

        signals = detect_all_signals(daily, sym, days=9999)
        for sig in signals:
            idx_4h = dl.get(daily[sig.bar_idx].date, -1)
            if idx_4h < 0: continue
            sig.bar_idx = idx_4h
            sig.entry_price = bars_4h[idx_4h].close

            strat_name = ADAPTIVE_EXIT.get(sig.signal_type, "trail_atr")
            strat_fn = EXIT_STRATEGIES.get(strat_name, EXIT_STRATEGIES["trail_atr"])
            try:
                result = strat_fn(bars_4h, sig, c4h)
                _apply_costs(result, bars_4h, sig.bar_idx, sig.direction)
                trades_by_type[sig.signal_type].append(result.net_pnl_pct)
                all_trades.append(result.net_pnl_pct)
            except Exception:
                continue

    # Run randomization test per signal type
    print(f"{'Signal Type':25s} | {'N':>4s} | {'WR':>5s} | {'EV':>7s} | {'PnL':>8s} | {'p-value':>8s} | Verdict")
    print("-" * 90)

    results = []
    for sig_type in sorted(trades_by_type.keys(), key=lambda x: -len(trades_by_type[x])):
        pnls = trades_by_type[sig_type]
        n = len(pnls)
        if n < 3:
            continue
        wr = sum(1 for p in pnls if p > 0) / n * 100
        ev = np.mean(pnls)
        total_pnl = sum(pnls)

        # Randomization: flip direction randomly
        real_ev = ev
        n_better = 0
        for _ in range(N_SHUFFLES):
            fake_pnls = [p * random.choice([1, -1]) for p in pnls]
            if np.mean(fake_pnls) >= real_ev:
                n_better += 1
        p_value = n_better / N_SHUFFLES

        if p_value < 0.01:
            verdict = "*** STRONG"
        elif p_value < 0.05:
            verdict = "**  significant"
        elif p_value < 0.10:
            verdict = "*   marginal"
        else:
            verdict = "    not significant"

        results.append((sig_type, n, wr, ev, total_pnl, p_value, verdict))
        print(f"{sig_type:25s} | {n:4d} | {wr:5.1f}% | {ev:+6.2f}% | {total_pnl:+7.1f}% | {p_value:8.4f} | {verdict}")

    # Full system
    print("-" * 90)
    n = len(all_trades)
    wr = sum(1 for p in all_trades if p > 0) / n * 100
    ev = np.mean(all_trades)
    total_pnl = sum(all_trades)
    n_better = 0
    for _ in range(N_SHUFFLES):
        fake_pnls = [p * random.choice([1, -1]) for p in all_trades]
        if np.mean(fake_pnls) >= ev:
            n_better += 1
    p_value = n_better / N_SHUFFLES
    print(f"{'FULL SYSTEM':25s} | {n:4d} | {wr:5.1f}% | {ev:+6.2f}% | {total_pnl:+7.1f}% | {p_value:8.4f} | {'*** STRONG' if p_value < 0.01 else '** sig' if p_value < 0.05 else 'not sig'}")

    print(f"\nShuffles: {N_SHUFFLES}, Time: {time.time()-t0:.1f}s")


asyncio.run(main())
