#!/usr/bin/env python3
"""
Lab: ZSCORE_SL_INCREASE sensitivity analysis.

Question: Fixed +1.0 threshold treats all entry z-scores equally.
At entry_z=-4.45, +1.0 is just 22% relative move.
At entry_z=-1.5, +1.0 is 67% relative move.

Test variants:
  A) Fixed +1.0 (current baseline)
  B) Fixed +1.5
  C) Fixed +2.0
  D) Proportional: max(1.0, abs(entry_z) * 0.25)
  E) Proportional: max(1.0, abs(entry_z) * 0.33)

Only affects zscore_mr trades: capitulation, fund_reversal.
"""
import sys, os, pickle
sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')
sys.argv = ['test', '--4h']

from scripts.setup_backtest import (
    BARS_PER_DAY, HARD_STOP_PCT, SIGNAL_PRIMARY_Z, ZSCORE_TP_THRESH,
    ExitResult, _fav_adv, _walk_pnl
)

CACHE_PATH = '/tmp/signal_cache.pkl'
with open(CACHE_PATH, 'rb') as f:
    cache = pickle.load(f)

meta = cache['meta']
print(f"Cache: {meta['n_signals']} signals, {meta['n_symbols']} symbols, split={meta['split']}")
print(f"Bars per day: {meta['bars_per_day']}\n")

MH = 30 * BARS_PER_DAY
ZSCORE_TYPES = {"capitulation", "fund_reversal"}

# Extract zscore signals with their 4h bars
zscore_entries = []
for entry in cache['all_signals']:
    sig = entry['sig']
    if sig.signal_type in ZSCORE_TYPES:
        zscore_entries.append(entry)

print(f"Zscore-MR signals: {len(zscore_entries)} ({', '.join(sorted(ZSCORE_TYPES))})\n")

# Show entry z-score distribution
print("Entry |z| distribution:")
buckets = [0, 1, 2, 3, 4, 5, 99]
for i in range(len(buckets) - 1):
    lo, hi = buckets[i], buckets[i+1]
    cnt = 0
    for e in zscore_entries:
        sig = e['sig']
        pk = SIGNAL_PRIMARY_Z.get(sig.signal_type, "oi_z")
        ez = abs(sig.zscores.get(pk, 0.0))
        if lo <= ez < hi:
            cnt += 1
    if cnt:
        print(f"  |z| {lo}-{hi}: {cnt} signals")
print()


def run_zscore_variant(entries, sl_increase=1.0, proportional=False, prop_factor=0.25):
    """Run zscore exit variant on all entries, return list of ExitResult."""
    results = []
    for entry in entries:
        sig = entry['sig']
        bars = entry['bars_4h']
        ei = sig.bar_idx_4h
        d = sig.direction
        pk = SIGNAL_PRIMARY_Z.get(sig.signal_type, "oi_z")
        tp_t = ZSCORE_TP_THRESH.get(pk, 0.5)
        entry_z = sig.zscores.get(pk, 0.0)
        mf, ma = 0.0, 0.0

        if proportional:
            thresh = max(1.0, abs(entry_z) * prop_factor)
        else:
            thresh = sl_increase

        exited = False
        for j in range(ei + 1, min(ei + MH + 1, len(bars))):
            fav, adv = _fav_adv(bars, ei, d, j)
            mf, ma = max(mf, fav), max(ma, adv)

            if adv >= HARD_STOP_PCT:
                results.append(ExitResult(j, bars[j].close, "hard_stop", -HARD_STOP_PCT, j - ei, ma, mf))
                exited = True
                break

            cur_z = getattr(bars[j], pk, 0.0)
            if abs(cur_z) < tp_t:
                pnl = _walk_pnl(bars, ei, d, j)
                results.append(ExitResult(j, bars[j].close, "zscore_tp", pnl, j - ei, ma, mf))
                exited = True
                break
            if abs(cur_z) > abs(entry_z) + thresh:
                pnl = _walk_pnl(bars, ei, d, j)
                results.append(ExitResult(j, bars[j].close, "zscore_sl", pnl, j - ei, ma, mf))
                exited = True
                break

        if not exited:
            last = min(ei + MH, len(bars) - 1)
            pnl = _walk_pnl(bars, ei, d, last)
            results.append(ExitResult(last, bars[last].close, "timeout", pnl, last - ei, ma, mf))

    return results


VARIANTS = [
    ("A) fixed +1.0 (current)", dict(sl_increase=1.0)),
    ("B) fixed +1.5",           dict(sl_increase=1.5)),
    ("C) fixed +2.0",           dict(sl_increase=2.0)),
    ("D) prop 25%",             dict(proportional=True, prop_factor=0.25)),
    ("E) prop 33%",             dict(proportional=True, prop_factor=0.33)),
]

# ─── Main results ────────────────────────────────────────────────────────
print(f"{'Variant':<28} {'N':>4} {'WR%':>6} {'EV%':>7} {'PF':>6} {'AvgDays':>8} {'TP':>4} {'SL':>4} {'TO':>4} {'HS':>4}")
print("-" * 95)

for name, kwargs in VARIANTS:
    results = run_zscore_variant(zscore_entries, **kwargs)
    exits = {}
    for r in results:
        exits[r.exit_reason] = exits.get(r.exit_reason, 0) + 1

    wins = [r for r in results if r.pnl_pct > 0]
    losses = [r for r in results if r.pnl_pct <= 0]
    n = len(results)
    wr = len(wins) / n * 100 if n else 0
    ev = sum(r.pnl_pct for r in results) / n if n else 0
    avg_win = sum(r.pnl_pct for r in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(r.pnl_pct for r in losses) / len(losses)) if losses else 1
    pf = avg_win * len(wins) / (avg_loss * len(losses)) if losses else 99
    avg_days = sum(r.bars_held for r in results) / n / BARS_PER_DAY if n else 0

    print(f"{name:<28} {n:>4} {wr:>5.1f}% {ev:>+6.2f}% {pf:>5.2f}x {avg_days:>7.1f}d "
          f"{exits.get('zscore_tp', 0):>4} {exits.get('zscore_sl', 0):>4} "
          f"{exits.get('timeout', 0):>4} {exits.get('hard_stop', 0):>4}")

# ─── Per signal type ─────────────────────────────────────────────────────
print(f"\n{'─'*95}")
print("Per signal type breakdown:\n")

for sig_type in sorted(ZSCORE_TYPES):
    sigs = [e for e in zscore_entries if e['sig'].signal_type == sig_type]
    if not sigs:
        continue
    print(f"  {sig_type} ({len(sigs)} signals):")
    print(f"  {'Variant':<28} {'WR%':>6} {'EV%':>7} {'AvgDays':>8} {'TP':>4} {'SL':>4} {'TO':>4} {'HS':>4}")
    print(f"  {'-'*75}")

    for vname, kwargs in VARIANTS:
        results = run_zscore_variant(sigs, **kwargs)
        wins = sum(1 for r in results if r.pnl_pct > 0)
        n = len(results)
        wr = wins / n * 100 if n else 0
        ev = sum(r.pnl_pct for r in results) / n if n else 0
        avg_days = sum(r.bars_held for r in results) / n / BARS_PER_DAY if n else 0
        exits = {}
        for r in results:
            exits[r.exit_reason] = exits.get(r.exit_reason, 0) + 1
        print(f"  {vname:<28} {wr:>5.1f}% {ev:>+6.2f}% {avg_days:>7.1f}d "
              f"{exits.get('zscore_tp', 0):>4} {exits.get('zscore_sl', 0):>4} "
              f"{exits.get('timeout', 0):>4} {exits.get('hard_stop', 0):>4}")
    print()

# ─── zscore_sl PnL distribution ──────────────────────────────────────────
print(f"{'─'*95}")
print("zscore_sl exit PnL distribution (current +1.0):\n")
results_a = run_zscore_variant(zscore_entries, sl_increase=1.0)
sl_results = [(r, e) for r, e in zip(results_a, zscore_entries) if r.exit_reason == "zscore_sl"]
if sl_results:
    pnls = sorted([r.pnl_pct for r, _ in sl_results])
    print(f"  N={len(pnls)}, min={pnls[0]:+.2f}%, max={pnls[-1]:+.2f}%, "
          f"mean={sum(pnls)/len(pnls):+.2f}%, median={pnls[len(pnls)//2]:+.2f}%")
    print(f"  Winners: {sum(1 for p in pnls if p > 0)}, Losers: {sum(1 for p in pnls if p <= 0)}")
    print(f"\n  Details:")
    for r, e in sl_results:
        sig = e['sig']
        pk = SIGNAL_PRIMARY_Z.get(sig.signal_type, "oi_z")
        ez = sig.zscores.get(pk, 0.0)
        print(f"    {e['sym']:>10} {sig.signal_type:<20} entry_z={ez:+.2f}  "
              f"pnl={r.pnl_pct:+.2f}%  held={r.bars_held/BARS_PER_DAY:.1f}d")
