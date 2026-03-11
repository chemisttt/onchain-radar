#!/usr/bin/env python3
"""
Signal Strategy Lab — fast backtesting from cached signals.
Usage: python3 /tmp/lab.py
Cache: python3 /tmp/cache_signals.py (run once when signals change)
"""
import sys, os, pickle
sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')
sys.argv = ['test', '--4h']

from scripts.setup_backtest import (
    strategy_trailing, strategy_counter, strategy_fixed, strategy_zscore, strategy_hybrid,
    BARS_PER_DAY
)
from collections import defaultdict

CACHE_PATH = '/tmp/signal_cache.pkl'

with open(CACHE_PATH, 'rb') as f:
    RAW = pickle.load(f)
print(f"Loaded {len(RAW)} raw signals\n")

MH = 30 * BARS_PER_DAY


# ─── Strategy dispatch ───────────────────────────────────────────────
def mk_trail(am=1.5, bp=2.0):
    return lambda bars, sig, cache: strategy_trailing(bars, sig, max_hold=MH, atr_mult=am, be_pct=bp)

def mk_counter():
    return lambda bars, sig, cache: strategy_counter(bars, sig, cache, max_hold=MH)

def mk_fixed(tp=5, sl=3):
    return lambda bars, sig, cache: strategy_fixed(bars, sig, tp_pct=tp, sl_pct=sl, timeout=7*BARS_PER_DAY)

def mk_zscore():
    return lambda bars, sig, cache: strategy_zscore(bars, sig, max_hold=MH)

def mk_hybrid():
    return lambda bars, sig, cache: strategy_hybrid(bars, sig, cache, max_hold=MH)


# ─── Core engine ─────────────────────────────────────────────────────
def run(routing, exclude=None, filters=None, daily_cap=5):
    """
    routing: dict signal_type → strategy_fn(bars, sig, cache)
    exclude: set of signal types to skip
    filters: dict signal_type → fn(daily_bar_dict) → bool
    """
    exclude = exclude or set()
    filters = filters or {}
    trades = []

    for item in RAW:
        sig = item['sig']
        st = sig.signal_type
        if st in exclude:
            continue
        b = item['daily_bar']
        if st in filters and not filters[st](b):
            continue

        fn = routing.get(st)
        if fn is None:
            continue  # no route = skip

        orig = sig.bar_idx
        sig.bar_idx = sig.bar_idx_4h
        result = fn(item['bars_4h'], sig, item['cache_4h'])
        sig.bar_idx = orig

        if result:
            trades.append({
                'date': item['sig_date'], 'sym': item['sym'],
                'sig': st, 'dir': sig.direction,
                'pnl': result.pnl_pct, 'mfe': result.max_favorable_pct,
                'mae': result.max_drawdown_pct, 'exit': result.exit_reason,
                'hold': result.bars_held / BARS_PER_DAY,
                'trend': b['trend'], 'pvs': b['pvs'],
                'oi_z': b['oi_z'], 'fund_z': b['fund_z'],
            })

    # daily cap
    by_day = defaultdict(list)
    for t in trades:
        by_day[t['date']].append(t)
    capped = []
    for day in sorted(by_day):
        top = sorted(by_day[day], key=lambda x: -abs(x['pnl']))[:daily_cap]
        capped.extend(top)
    return capped


def report(trades, label="", show_signals=False, show_exit=False):
    n = len(trades)
    if n == 0:
        print(f"  {label:>40} — no trades")
        return None
    w = sum(1 for t in trades if t['pnl'] > 0)
    ev = sum(t['pnl'] for t in trades) / n
    hold = sum(t['hold'] for t in trades) / n
    pf_up = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    pf_dn = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0)) or 1
    avg_win = sum(t['pnl'] for t in trades if t['pnl'] > 0) / max(w, 1)
    avg_loss = sum(t['pnl'] for t in trades if t['pnl'] <= 0) / max(n - w, 1)
    print(f"  {label:>40} N={n:>3} WR={w/n*100:>5.1f}% EV={ev:>+6.2f}% PF={pf_up/pf_dn:>4.1f}x Hold={hold:>4.1f}d W={avg_win:>+5.1f}% L={avg_loss:>+5.1f}%")

    if show_signals:
        by_sig = defaultdict(list)
        for t in trades:
            by_sig[t['sig']].append(t)
        for sig, st in sorted(by_sig.items(), key=lambda x: -len(x[1])):
            sw = sum(1 for t in st if t['pnl'] > 0)
            sev = sum(t['pnl'] for t in st) / len(st)
            print(f"    {sig:>22}: N={len(st):>3} WR={sw/len(st)*100:>5.0f}% EV={sev:>+6.2f}%")

    if show_exit:
        by_ex = defaultdict(list)
        for t in trades:
            by_ex[t['exit']].append(t)
        for ex, st in sorted(by_ex.items(), key=lambda x: -len(x[1])):
            sw = sum(1 for t in st if t['pnl'] > 0)
            sev = sum(t['pnl'] for t in st) / len(st)
            print(f"    exit {ex:>12}: N={len(st):>3} WR={sw/len(st)*100:>5.0f}% EV={sev:>+6.2f}%")

    return {'n': n, 'wr': w/n*100, 'ev': ev, 'pf': pf_up/pf_dn, 'hold': hold, 'label': label}


# ─── Routing presets ─────────────────────────────────────────────────
# Current Adaptive (from setup_backtest.py)
CURRENT = {
    "liq_short_squeeze": mk_counter(),
    "div_squeeze_3d":    mk_counter(),
    "div_top_1d":        mk_counter(),
    "distribution":      mk_fixed(),
    "oi_buildup_stall":  mk_counter(),
    "overheat":          mk_trail(),
    "overextension":     mk_trail(),
    "vol_divergence":    mk_trail(),
    "fund_reversal":     mk_zscore(),
    "capitulation":      mk_zscore(),
    "fund_spike":        mk_trail(),
    "div_squeeze_1d":    mk_hybrid(),
}

# =====================================================================
#  EXPERIMENTS
# =====================================================================

print("=" * 100)
print("  EXPERIMENT 1: BASELINE COMPARISON")
print("=" * 100)

t1 = run(CURRENT)
report(t1, "all signals", show_signals=True)

t2 = run(CURRENT, exclude={'fund_spike'})
report(t2, "no fund_spike", show_signals=True)

t3 = run(CURRENT, exclude={'fund_spike', 'overextension', 'overheat'})
report(t3, "no fs+oe+oh (clean)")

t4 = run(CURRENT, exclude={'fund_spike', 'overextension', 'overheat'}, daily_cap=2)
report(t4, "clean + cap=2")

print()
print("=" * 100)
print("  EXPERIMENT 2: RE-ENABLE SIGNALS WITH BETTER EXIT STRATEGIES")
print("  Idea: fund_spike/overextension/overheat fail on trail, try other exits")
print("=" * 100)

# What if we route overheat to counter instead of trail?
for sig_name, label in [("overheat", "OH"), ("overextension", "OE"), ("fund_spike", "FS")]:
    for strat_name, strat_fn in [
        ("trail 1.5", mk_trail(1.5, 2.0)),
        ("trail 2.5", mk_trail(2.5, 3.0)),
        ("counter",   mk_counter()),
        ("fixed 5/3", mk_fixed(5, 3)),
        ("fixed 3/2", mk_fixed(3, 2)),
        ("fixed 8/3", mk_fixed(8, 3)),
        ("zscore",    mk_zscore()),
        ("hybrid",    mk_hybrid()),
    ]:
        route = dict(CURRENT)
        route[sig_name] = strat_fn
        trades = run(route, exclude={'fund_spike'} - {sig_name})
        sig_trades = [t for t in trades if t['sig'] == sig_name]
        if sig_trades:
            w = sum(1 for t in sig_trades if t['pnl'] > 0)
            ev = sum(t['pnl'] for t in sig_trades) / len(sig_trades)
            print(f"  {label} → {strat_name:>12}: N={len(sig_trades):>3} WR={w/len(sig_trades)*100:>5.0f}% EV={ev:>+6.2f}%")
    print()


print("=" * 100)
print("  EXPERIMENT 3: FIXED TP/SL GRID SEARCH FOR ALL SIGNALS")
print("  Idea: maybe fixed TP/SL outperforms adaptive for some signals")
print("=" * 100)

for tp, sl in [(3, 2), (5, 3), (8, 3), (8, 5), (5, 2), (10, 5), (3, 3), (5, 5)]:
    all_fixed = {sig: mk_fixed(tp, sl) for sig in CURRENT}
    trades = run(all_fixed, exclude={'fund_spike'})
    report(trades, f"ALL fixed {tp}/{sl}")

print()
print("=" * 100)
print("  EXPERIMENT 4: ALL-COUNTER vs ALL-TRAIL vs ALL-ZSCORE")
print("=" * 100)

for name, fn in [("ALL counter", mk_counter()), ("ALL trail", mk_trail()),
                  ("ALL zscore", mk_zscore()), ("ALL hybrid", mk_hybrid())]:
    route = {sig: fn for sig in CURRENT}
    trades = run(route, exclude={'fund_spike'})
    report(trades, name)


print()
print("=" * 100)
print("  EXPERIMENT 5: TREND-AWARE ROUTING")
print("  Idea: use different exits based on market trend at entry")
print("=" * 100)

# Custom run that picks strategy based on trend
def run_trend_aware(up_route, down_route, neutral_route, exclude=None, daily_cap=5):
    exclude = exclude or set()
    trades = []
    for item in RAW:
        sig = item['sig']
        st = sig.signal_type
        if st in exclude:
            continue
        b = item['daily_bar']
        trend = b['trend']
        if trend == 'up':
            route = up_route
        elif trend == 'down':
            route = down_route
        else:
            route = neutral_route
        fn = route.get(st)
        if fn is None:
            continue
        orig = sig.bar_idx
        sig.bar_idx = sig.bar_idx_4h
        result = fn(item['bars_4h'], sig, item['cache_4h'])
        sig.bar_idx = orig
        if result:
            trades.append({
                'date': item['sig_date'], 'sym': item['sym'],
                'sig': st, 'dir': sig.direction,
                'pnl': result.pnl_pct, 'mfe': result.max_favorable_pct,
                'mae': result.max_drawdown_pct, 'exit': result.exit_reason,
                'hold': result.bars_held / BARS_PER_DAY,
                'trend': trend, 'pvs': b['pvs'],
                'oi_z': b['oi_z'], 'fund_z': b['fund_z'],
            })
    by_day = defaultdict(list)
    for t in trades:
        by_day[t['date']].append(t)
    capped = []
    for day in sorted(by_day):
        top = sorted(by_day[day], key=lambda x: -abs(x['pnl']))[:daily_cap]
        capped.extend(top)
    return capped

# In uptrend: counter (hold for reversal), in downtrend: trail (ride momentum)
up_counter = {sig: mk_counter() for sig in CURRENT}
down_trail = {sig: mk_trail() for sig in CURRENT}
neutral_current = dict(CURRENT)

t = run_trend_aware(up_counter, down_trail, neutral_current, exclude={'fund_spike'})
report(t, "trend: up→counter, down→trail", show_signals=True)

# Opposite: up→trail, down→counter
t = run_trend_aware(down_trail, up_counter, neutral_current, exclude={'fund_spike'})
report(t, "trend: up→trail, down→counter")


print()
print("=" * 100)
print("  EXPERIMENT 6: SIGNAL FILTERS (beyond simple disable)")
print("  Idea: keep signals but add quality filters")
print("=" * 100)

# Overextension only in downtrend (shorting extended bounces)
filters_oe_down = {"overextension": lambda b: b['trend'] == 'down'}
t = run(CURRENT, exclude={'fund_spike'}, filters=filters_oe_down)
report(t, "OE only trend=down")

# Overextension with higher fund_z
filters_oe_fund = {"overextension": lambda b: b['fund_z'] > 1.0}
t = run(CURRENT, exclude={'fund_spike'}, filters=filters_oe_fund)
report(t, "OE fund_z>1.0")

filters_oe_fund2 = {"overextension": lambda b: b['fund_z'] > 1.5}
t = run(CURRENT, exclude={'fund_spike'}, filters=filters_oe_fund2)
report(t, "OE fund_z>1.5")

# Overheat with high oi_z
filters_oh_oiz = {"overheat": lambda b: b['oi_z'] > 2.0}
t = run(CURRENT, exclude={'fund_spike'}, filters=filters_oh_oiz)
report(t, "OH oi_z>2.0")

filters_oh_oiz2 = {"overheat": lambda b: b['oi_z'] > 2.5}
t = run(CURRENT, exclude={'fund_spike'}, filters=filters_oh_oiz2)
report(t, "OH oi_z>2.5")

# fund_spike re-enable with tight filters
fs_filters = {"fund_spike": lambda b: b['fund_z'] > 2.5}
route_fs = dict(CURRENT)
t = run(route_fs, filters=fs_filters)
report(t, "FS fund_z>2.5 only")

fs_filters2 = {"fund_spike": lambda b: b['fund_z'] > 2.5 and b['trend'] != 'up'}
t = run(route_fs, filters=fs_filters2)
report(t, "FS fund_z>2.5 + not up")

# liq_short_squeeze quality filters
lss_filters = {"liq_short_squeeze": lambda b: b['pvs'] < 30}
t = run(CURRENT, exclude={'fund_spike'}, filters=lss_filters)
report(t, "LSS pvs<30")

lss_filters2 = {"liq_short_squeeze": lambda b: b['pvs'] < 20}
t = run(CURRENT, exclude={'fund_spike'}, filters=lss_filters2)
report(t, "LSS pvs<20")


print()
print("=" * 100)
print("  EXPERIMENT 7: STRATEGY PROFILES")
print("=" * 100)

# AGGRESSIVE: max return, accept high DD
# All counter (longest holds, highest EV when right)
aggr = {sig: mk_counter() for sig in CURRENT}
t = run(aggr, exclude={'fund_spike', 'overextension'})
report(t, "AGGRESSIVE: all counter, no oe", show_signals=True)

# STABLE: balanced, current best
t = run(CURRENT, exclude={'fund_spike', 'overextension', 'overheat'})
report(t, "STABLE: clean adaptive")

# CONSERVATIVE: high WR, low DD
# Only signals with EV > 1% historically, fixed TP/SL
cons_route = {
    "liq_short_squeeze": mk_counter(),
    "div_squeeze_3d":    mk_counter(),
    "oi_buildup_stall":  mk_counter(),
    "vol_divergence":    mk_trail(),
    "fund_reversal":     mk_zscore(),
    "distribution":      mk_fixed(),
}
t = run(cons_route)
report(t, "CONSERVATIVE: top 6 signals only", show_signals=True)

# TURBO: aggressive routing + re-enable filtered signals
turbo = {
    "liq_short_squeeze": mk_counter(),
    "div_squeeze_3d":    mk_counter(),
    "div_top_1d":        mk_counter(),
    "distribution":      mk_fixed(),
    "oi_buildup_stall":  mk_counter(),
    "overheat":          mk_counter(),   # counter instead of trail
    "overextension":     mk_fixed(3, 2), # quick scalp
    "vol_divergence":    mk_trail(),
    "fund_reversal":     mk_zscore(),
    "capitulation":      mk_zscore(),
    "fund_spike":        mk_fixed(3, 2), # quick scalp
    "div_squeeze_1d":    mk_counter(),
}
t = run(turbo)
report(t, "TURBO: all signals, aggressive exits", show_signals=True)

# TURBO v2: same but with quality filters
turbo_filters = {
    "fund_spike": lambda b: b['fund_z'] > 2.0,
    "overextension": lambda b: b['pvs'] > 10 and b['fund_z'] > 1.0,
    "overheat": lambda b: b['oi_z'] > 2.0,
}
t = run(turbo, filters=turbo_filters)
report(t, "TURBO v2: filtered aggressive", show_signals=True)


print()
print("=" * 100)
print("  EXPERIMENT 8: FIXED TP/SL PER SIGNAL (optimal grid)")
print("  Idea: each signal gets its own best TP/SL")
print("=" * 100)

# For each signal, find best TP/SL
active_sigs = set()
for item in RAW:
    active_sigs.add(item['sig'].signal_type)

print(f"  Testing {len(active_sigs)} signal types × 8 TP/SL combos...")
best_fixed = {}
for sig_name in sorted(active_sigs):
    best_ev = -999
    best_cfg = None
    for tp, sl in [(3, 2), (5, 3), (8, 3), (8, 5), (5, 2), (10, 5), (3, 3), (5, 5)]:
        route = {sig_name: mk_fixed(tp, sl)}
        trades = run(route)
        sig_trades = [t for t in trades if t['sig'] == sig_name]
        if sig_trades:
            ev = sum(t['pnl'] for t in sig_trades) / len(sig_trades)
            wr = sum(1 for t in sig_trades if t['pnl'] > 0) / len(sig_trades) * 100
            if ev > best_ev:
                best_ev = ev
                best_cfg = (tp, sl, len(sig_trades), wr, ev)
    if best_cfg:
        tp, sl, nn, wr, ev = best_cfg
        best_fixed[sig_name] = (tp, sl)
        print(f"  {sig_name:>22}: best TP={tp}/SL={sl} → N={nn:>3} WR={wr:>5.1f}% EV={ev:>+6.2f}%")

# Build optimal fixed-only routing
optimal_fixed = {sig: mk_fixed(tp, sl) for sig, (tp, sl) in best_fixed.items()}
t = run(optimal_fixed, exclude={'fund_spike'})
report(t, "OPTIMAL per-signal fixed", show_signals=True)


print()
print("=" * 100)
print("  EXPERIMENT 9: COUNTER WITH DIFFERENT HARD STOP LEVELS")
print("  Idea: -8% hard stop might be too tight for counter strategy")
print("=" * 100)

# This requires modifying counter strategy... but we can approximate
# by seeing how many counter trades hit exactly -8% (hard_stop)
t_counter_all = run({sig: mk_counter() for sig in CURRENT}, exclude={'fund_spike'})
hardstops = [t for t in t_counter_all if t['exit'] == 'hard_stop']
print(f"  Counter: {len(hardstops)}/{len(t_counter_all)} trades hit -8% hard stop")
if hardstops:
    # How many had MFE > 5% before dying?
    mfe_before = [t for t in hardstops if t['mfe'] > 5]
    print(f"  Of those, {len(mfe_before)} had MFE>5% before stop (wasted)")
    for t in sorted(mfe_before, key=lambda x: -x['mfe'])[:10]:
        print(f"    {t['date']} {t['sym']:>12} {t['sig']:>20} MFE={t['mfe']:>+5.1f}% → {t['pnl']:>+5.1f}%")


print()
print("=" * 100)
print("  EXPERIMENT 10: LONG vs SHORT SPLIT")
print("=" * 100)

t_all = run(CURRENT, exclude={'fund_spike'})
longs = [t for t in t_all if t['dir'] == 'long']
shorts = [t for t in t_all if t['dir'] == 'short']
print(f"  Longs:  N={len(longs):>3} WR={sum(1 for t in longs if t['pnl']>0)/len(longs)*100:>5.1f}% EV={sum(t['pnl'] for t in longs)/len(longs):>+6.2f}%")
print(f"  Shorts: N={len(shorts):>3} WR={sum(1 for t in shorts if t['pnl']>0)/len(shorts)*100:>5.1f}% EV={sum(t['pnl'] for t in shorts)/len(shorts):>+6.2f}%")

# Long-only signal breakdown
print("  Long signals:")
by_sig_l = defaultdict(list)
for t in longs:
    by_sig_l[t['sig']].append(t)
for sig, st in sorted(by_sig_l.items(), key=lambda x: -len(x[1])):
    sw = sum(1 for t in st if t['pnl'] > 0)
    sev = sum(t['pnl'] for t in st) / len(st)
    print(f"    {sig:>22}: N={len(st):>3} WR={sw/len(st)*100:>5.0f}% EV={sev:>+6.2f}%")

print("  Short signals:")
by_sig_s = defaultdict(list)
for t in shorts:
    by_sig_s[t['sig']].append(t)
for sig, st in sorted(by_sig_s.items(), key=lambda x: -len(x[1])):
    sw = sum(1 for t in st if t['pnl'] > 0)
    sev = sum(t['pnl'] for t in st) / len(st)
    print(f"    {sig:>22}: N={len(st):>3} WR={sw/len(st)*100:>5.0f}% EV={sev:>+6.2f}%")


print()
print("=" * 100)
print("  EXPERIMENT 11: YEAR-BY-YEAR STABILITY")
print("=" * 100)

for label, route, excl in [
    ("current adaptive", CURRENT, {'fund_spike'}),
    ("clean", CURRENT, {'fund_spike', 'overextension', 'overheat'}),
    ("turbo v2", turbo, None),
]:
    trades = run(route, exclude=excl, filters=turbo_filters if label == "turbo v2" else None)
    print(f"\n  --- {label} ---")
    for year in ['2023', '2024', '2025', '2026']:
        yt = [t for t in trades if t['date'].startswith(year)]
        if yt:
            w = sum(1 for t in yt if t['pnl'] > 0)
            ev = sum(t['pnl'] for t in yt) / len(yt)
            print(f"    {year}: N={len(yt):>3} WR={w/len(yt)*100:>5.0f}% EV={ev:>+6.2f}%")


print()
print("=" * 100)
print("  SUMMARY: TOP CONFIGS")
print("=" * 100)

configs = [
    ("Current (no fs)", CURRENT, {'fund_spike'}, None, 5),
    ("Clean (no fs+oe+oh)", CURRENT, {'fund_spike', 'overextension', 'overheat'}, None, 5),
    ("Clean cap=2", CURRENT, {'fund_spike', 'overextension', 'overheat'}, None, 2),
    ("Conservative", cons_route, None, None, 5),
    ("Turbo v2", turbo, None, turbo_filters, 5),
    ("All counter (no fs+oe)", {s: mk_counter() for s in CURRENT}, {'fund_spike', 'overextension'}, None, 5),
]

print(f"  {'Config':>35} {'N':>4} {'WR':>6} {'EV':>7} {'PF':>5} {'Hold':>5}")
print("  " + "-" * 70)
for label, route, excl, filt, cap in configs:
    trades = run(route, exclude=excl, filters=filt, daily_cap=cap)
    n = len(trades)
    if n == 0:
        continue
    w = sum(1 for t in trades if t['pnl'] > 0)
    ev = sum(t['pnl'] for t in trades) / n
    pf_up = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    pf_dn = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0)) or 1
    hold = sum(t['hold'] for t in trades) / n
    print(f"  {label:>35} {n:>4} {w/n*100:>5.1f}% {ev:>+6.2f}% {pf_up/pf_dn:>4.1f}x {hold:>4.1f}d")
