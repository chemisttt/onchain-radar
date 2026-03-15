#!/usr/bin/env python3
"""Optimize fund_mean_revert — test all filter/exit combinations.

Variants:
  A: Baseline (current: full confluence, momentum filter active)
  B: Momentum filter exempt (add to exempt list like fund_spike)
  C: Confluence bonus +2 (like research had base=3)
  D: Both (exempt + bonus +2)

Each variant × 6 exit strategies = 24 cells.

Usage:
    cd backend && python3 scripts/lab/fund_mean_revert_optimize.py
"""

import asyncio, os, sys, time
from collections import defaultdict
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.argv = ['test', '--4h']

from db import init_db
from scripts.setup_backtest import (
    load_symbol_data, load_4h_bars, detect_signals_at_bar,
    Bar, Bar4h, Signal, ExitResult,
    strategy_fixed, strategy_zscore, strategy_counter, strategy_trailing,
    strategy_hybrid, strategy_trail_counter,
    _apply_costs, _bar_to_signal_input, ROUND_TRIP_FEE_PCT,
    SYMBOLS, BARS_PER_DAY, TOP_OI_SYMBOLS, ALT_MIN_CONFLUENCE,
    COUNTER_SIGNALS, COOLDOWN_DAYS,
)
from services.signal_conditions import (
    SignalInput, detect_signals, compute_confluence,
    FUND_Z_MEAN_REVERT, FUND_Z_SUSTAINED,
)

EXIT_STRATEGIES = {
    "fixed":       lambda b, s, c: strategy_fixed(b, s, timeout=7 * BARS_PER_DAY),
    "zscore_mr":   lambda b, s, c: strategy_zscore(b, s, max_hold=30 * BARS_PER_DAY),
    "counter_sig": lambda b, s, c: strategy_counter(b, s, c, max_hold=30 * BARS_PER_DAY, hard_stop=12.0),
    "trail_atr":   lambda b, s, c: strategy_trailing(b, s, max_hold=30 * BARS_PER_DAY),
    "hybrid":      lambda b, s, c: strategy_hybrid(b, s, c, max_hold=30 * BARS_PER_DAY),
    "trail_counter": lambda b, s, c: strategy_trail_counter(b, s, c, max_hold=30 * BARS_PER_DAY, hard_stop=12.0),
}

CONFLUENCE_SIGNAL = 4

# Variant configs: (name, momentum_exempt, confluence_bonus)
VARIANTS = [
    ("A: Baseline",              False, 0),
    ("B: MomFilter exempt",      True,  0),
    ("C: Confluence +2",         False, 2),
    ("D: Exempt + Confluence +2", True,  2),
]


async def main():
    t0 = time.time()
    await init_db()

    # ── Load all data ──
    print("Loading data...")
    all_data = {}
    for sym in SYMBOLS:
        daily = await load_symbol_data(sym)
        if not daily:
            continue
        bars_4h, df, dl = await load_4h_bars(sym, daily)
        if not bars_4h:
            continue

        # Build counter-signal cache from ALL signals (for counter exits)
        sigs_by_date = {}
        for i in range(len(daily)):
            t = detect_signals_at_bar(daily, i)
            if t:
                sigs_by_date[daily[i].date] = t
        c4h = {}
        for i, b4 in enumerate(bars_4h):
            s = sigs_by_date.get(b4.date)
            if s:
                c4h[i] = s

        all_data[sym] = {
            'daily': daily,
            'bars_4h': bars_4h,
            'date_first': df,
            'date_last': dl,
            'cache_4h': c4h,
        }
    print(f"  Loaded {len(all_data)} symbols in {time.time()-t0:.1f}s\n")

    # ── Run variants ──
    for var_name, mom_exempt, conf_bonus in VARIANTS:
        print(f"{'='*80}")
        print(f"  {var_name}")
        print(f"{'='*80}")

        # Detect fund_mean_revert signals with this variant's filters
        signals = []  # list of (sym, Signal, bars_4h, cache_4h)

        for sym, data in all_data.items():
            daily = data['daily']
            bars_4h = data['bars_4h']
            dl = data['date_last']
            c4h = data['cache_4h']

            cooldowns = {}
            for i in range(30, len(daily)):
                b = daily[i]
                if b.close <= 0:
                    continue

                # Detect only fund_mean_revert
                inp = _bar_to_signal_input(daily, i)
                triggered = detect_signals(inp)
                fmr = [(st, d) for st, d in triggered if st == "fund_mean_revert"]
                if not fmr:
                    continue

                for sig_type, direction in fmr:
                    # Momentum filter (unless exempt)
                    if not mom_exempt:
                        if direction == "long" and b.trend == "down":
                            continue
                        if direction == "short" and b.trend == "up":
                            continue

                    # Confluence
                    confluence, factors = compute_confluence(inp, direction)
                    confluence += conf_bonus

                    if confluence < CONFLUENCE_SIGNAL:
                        continue
                    if sym not in TOP_OI_SYMBOLS and confluence < ALT_MIN_CONFLUENCE:
                        continue

                    # Cooldown
                    cd_key = f"{sig_type}:{sym}"
                    if cd_key in cooldowns and (i - cooldowns[cd_key]) < COOLDOWN_DAYS:
                        continue
                    cooldowns[cd_key] = i

                    # Map to 4h
                    idx_4h = dl.get(b.date, -1)
                    if idx_4h < 0:
                        continue

                    sig = Signal(
                        bar_idx=i,
                        signal_type=sig_type,
                        direction=direction,
                        entry_price=bars_4h[idx_4h].close,
                        confluence=confluence,
                        factors=factors[:5],
                        zscores={"oi_z": b.oi_z, "fund_z": b.fund_z,
                                 "liq_z": b.liq_z, "vol_z": b.vol_z},
                        bar_idx_4h=idx_4h,
                    )
                    signals.append((sym, sig, bars_4h, c4h))

        n_long = sum(1 for _, s, _, _ in signals if s.direction == "long")
        n_short = len(signals) - n_long
        print(f"  Signals: {len(signals)} (long={n_long}, short={n_short})")

        if not signals:
            print("  No signals — skipping\n")
            continue

        # Run all exit strategies
        print(f"\n  {'Strategy':<16s} {'N':>5s} {'WR':>6s} {'GrossEV':>9s} {'NetEV':>9s} {'PF':>7s} {'Hold':>6s} {'ExitReasons'}")
        print(f"  {'-'*75}")

        for exit_name, exit_fn in EXIT_STRATEGIES.items():
            results = []
            exit_reasons = defaultdict(int)

            for sym, sig, bars_4h, c4h in signals:
                r = exit_fn(bars_4h, sig, c4h)
                if r is None:
                    continue
                _apply_costs(r, bars_4h, sig.bar_idx_4h, sig.direction)
                results.append(r.net_pnl_pct)
                exit_reasons[r.exit_reason] += 1

            if not results:
                continue

            n = len(results)
            wins = sum(1 for r in results if r > 0)
            wr = wins / n * 100
            gross_ev = sum(results) / n
            # Approximate net (costs already applied)
            net_ev = gross_ev
            total_pnl = sum(results)
            losses = sum(r for r in results if r < 0)
            gains = sum(r for r in results if r > 0)
            pf = gains / abs(losses) if losses != 0 else 999

            avg_hold = 0  # not tracked in simplified version

            reasons_str = ", ".join(f"{k}={v}" for k, v in sorted(exit_reasons.items(), key=lambda x: -x[1])[:3])
            print(f"  {exit_name:<16s} {n:>5d} {wr:>5.1f}% {gross_ev:>+8.2f}% {net_ev:>+8.2f}% {pf:>6.2f}x {reasons_str}")

        # Walk-forward split
        print(f"\n  Walk-forward (train < 2025-01-01, test >= 2025-01-01):")
        for exit_name in ["counter_sig", "zscore_mr", "hybrid"]:
            exit_fn = EXIT_STRATEGIES[exit_name]
            train_r, test_r = [], []
            for sym, sig, bars_4h, c4h in signals:
                date = daily_date_from_sig(all_data[sym]['daily'], sig)
                r = exit_fn(bars_4h, sig, c4h)
                if r is None:
                    continue
                _apply_costs(r, bars_4h, sig.bar_idx_4h, sig.direction)
                if date < "2025-01-01":
                    train_r.append(r.net_pnl_pct)
                else:
                    test_r.append(r.net_pnl_pct)

            if train_r and test_r:
                tr_wr = sum(1 for r in train_r if r > 0) / len(train_r) * 100
                tr_ev = sum(train_r) / len(train_r)
                te_wr = sum(1 for r in test_r if r > 0) / len(test_r) * 100
                te_ev = sum(test_r) / len(test_r)
                print(f"    {exit_name:<16s}  train: N={len(train_r):>4d} WR={tr_wr:>5.1f}% EV={tr_ev:>+6.2f}%  |  test: N={len(test_r):>4d} WR={te_wr:>5.1f}% EV={te_ev:>+6.2f}%")
            else:
                print(f"    {exit_name:<16s}  insufficient data (train={len(train_r)}, test={len(test_r)})")

        print()

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s")


def daily_date_from_sig(daily: list[Bar], sig: Signal) -> str:
    """Get date string from signal's bar index."""
    if sig.bar_idx < len(daily):
        return daily[sig.bar_idx].date
    return ""


if __name__ == "__main__":
    asyncio.run(main())
