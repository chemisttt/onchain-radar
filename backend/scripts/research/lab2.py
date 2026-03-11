#!/usr/bin/env python3
"""
Lab 2: Counter strategy improvements — trailing breakeven + hard stop tuning.
Focus: 61 wasted trades (MFE>5%, PnL=-8%) → salvage with trailing protection.
"""
import sys, os, pickle
sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')
sys.argv = ['test', '--4h']

from scripts.setup_backtest import (
    strategy_trailing, strategy_fixed, strategy_zscore, strategy_hybrid,
    BARS_PER_DAY, COUNTER_SIGNALS, ExitResult, _fav_adv, _walk_pnl
)
from collections import defaultdict

CACHE_PATH = '/tmp/signal_cache.pkl'
with open(CACHE_PATH, 'rb') as f:
    RAW = pickle.load(f)
print(f"Loaded {len(RAW)} signals\n")

MH = 30 * BARS_PER_DAY


# ─── Modified counter strategies ─────────────────────────────────────
def strategy_counter_be(bars, signal, cache=None, max_hold=30,
                        hard_stop=8.0, be_trigger=4.0, be_lock=0.5):
    """Counter with trailing breakeven.
    Once MFE >= be_trigger%, move stop to entry + be_lock%.
    """
    ei = signal.bar_idx
    ep = signal.entry_price
    d = signal.direction
    cs = COUNTER_SIGNALS.get(d, set())
    if cache is None:
        cache = {}
    mf, ma = 0.0, 0.0
    be_active = False

    for j in range(ei + 1, min(ei + max_hold + 1, len(bars))):
        fav, adv = _fav_adv(bars, ei, d, j)
        mf, ma = max(mf, fav), max(ma, adv)

        # Activate breakeven once MFE threshold hit
        if not be_active and mf >= be_trigger:
            be_active = True

        # Hard stop (or breakeven stop)
        if be_active:
            # Stop at entry + be_lock% profit
            if adv >= 0 and fav < be_lock:
                # Price went below our be_lock threshold
                pnl = _walk_pnl(bars, ei, d, j)
                if pnl < be_lock:
                    return ExitResult(j, bars[j].close, "be_stop", be_lock, j - ei, ma, mf)

        if adv >= hard_stop:
            return ExitResult(j, bars[j].close, "hard_stop", -hard_stop, j - ei, ma, mf)

        # Counter signal exit
        triggered = cache.get(j, ())
        for st, sd in triggered:
            if st in cs:
                pnl = _walk_pnl(bars, ei, d, j)
                return ExitResult(j, bars[j].close, "counter", pnl, j - ei, ma, mf)

    last = min(ei + max_hold, len(bars) - 1)
    pnl = _walk_pnl(bars, ei, d, last)
    return ExitResult(last, bars[last].close, "timeout", pnl, last - ei, ma, mf)


def strategy_counter_trail(bars, signal, cache=None, max_hold=30,
                           hard_stop=8.0, trail_trigger=3.0, trail_pct=3.0):
    """Counter with trailing stop after trigger.
    Once MFE >= trail_trigger%, activate trailing stop at trail_pct% from peak.
    Still exit on counter signal if it comes first.
    """
    ei = signal.bar_idx
    ep = signal.entry_price
    d = signal.direction
    cs = COUNTER_SIGNALS.get(d, set())
    if cache is None:
        cache = {}
    mf, ma = 0.0, 0.0
    trail_active = False
    peak = 0.0

    for j in range(ei + 1, min(ei + max_hold + 1, len(bars))):
        fav, adv = _fav_adv(bars, ei, d, j)
        mf, ma = max(mf, fav), max(ma, adv)

        # Activate trail once trigger hit
        if not trail_active and mf >= trail_trigger:
            trail_active = True
            peak = mf

        if trail_active:
            peak = max(peak, fav)
            drawdown_from_peak = peak - fav
            if drawdown_from_peak >= trail_pct:
                pnl = peak - trail_pct  # approximate
                pnl = max(pnl, _walk_pnl(bars, ei, d, j))  # use actual if better
                return ExitResult(j, bars[j].close, "trail_be", pnl, j - ei, ma, mf)

        if adv >= hard_stop:
            return ExitResult(j, bars[j].close, "hard_stop", -hard_stop, j - ei, ma, mf)

        triggered = cache.get(j, ())
        for st, sd in triggered:
            if st in cs:
                pnl = _walk_pnl(bars, ei, d, j)
                return ExitResult(j, bars[j].close, "counter", pnl, j - ei, ma, mf)

    last = min(ei + max_hold, len(bars) - 1)
    pnl = _walk_pnl(bars, ei, d, last)
    return ExitResult(last, bars[last].close, "timeout", pnl, last - ei, ma, mf)


def strategy_counter_v2(bars, signal, cache=None, max_hold=30, hard_stop=8.0):
    """Original counter but with configurable hard stop."""
    ei = signal.bar_idx
    ep = signal.entry_price
    d = signal.direction
    cs = COUNTER_SIGNALS.get(d, set())
    if cache is None:
        cache = {}
    mf, ma = 0.0, 0.0
    for j in range(ei + 1, min(ei + max_hold + 1, len(bars))):
        fav, adv = _fav_adv(bars, ei, d, j)
        mf, ma = max(mf, fav), max(ma, adv)
        if adv >= hard_stop:
            return ExitResult(j, bars[j].close, "hard_stop", -hard_stop, j - ei, ma, mf)
        triggered = cache.get(j, ())
        for st, sd in triggered:
            if st in cs:
                pnl = _walk_pnl(bars, ei, d, j)
                return ExitResult(j, bars[j].close, "counter", pnl, j - ei, ma, mf)
    last = min(ei + max_hold, len(bars) - 1)
    pnl = _walk_pnl(bars, ei, d, last)
    return ExitResult(last, bars[last].close, "timeout", pnl, last - ei, ma, mf)


# ─── Strategy factories ─────────────────────────────────────────────
def mk_counter():
    return lambda bars, sig, cache: strategy_counter_v2(bars, sig, cache, max_hold=MH)

def mk_counter_hs(hs):
    return lambda bars, sig, cache: strategy_counter_v2(bars, sig, cache, max_hold=MH, hard_stop=hs)

def mk_counter_be(trig, lock):
    return lambda bars, sig, cache: strategy_counter_be(bars, sig, cache, max_hold=MH, be_trigger=trig, be_lock=lock)

def mk_counter_trail(trig, trail):
    return lambda bars, sig, cache: strategy_counter_trail(bars, sig, cache, max_hold=MH, trail_trigger=trig, trail_pct=trail)

def mk_trail(am=1.5, bp=2.0):
    return lambda bars, sig, cache: strategy_trailing(bars, sig, max_hold=MH, atr_mult=am, be_pct=bp)

def mk_fixed(tp=5, sl=3):
    return lambda bars, sig, cache: strategy_fixed(bars, sig, tp_pct=tp, sl_pct=sl, timeout=7*BARS_PER_DAY)

def mk_zscore():
    return lambda bars, sig, cache: strategy_zscore(bars, sig, max_hold=MH)

def mk_hybrid():
    return lambda bars, sig, cache: strategy_hybrid(bars, sig, cache, max_hold=MH)


# ─── Engine ──────────────────────────────────────────────────────────
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

COUNTER_SIGS = {"liq_short_squeeze", "div_squeeze_3d", "div_top_1d", "oi_buildup_stall"}


def run(routing, exclude=None, filters=None, daily_cap=5):
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
                'trend': b['trend'], 'pvs': b['pvs'],
            })
    by_day = defaultdict(list)
    for t in trades:
        by_day[t['date']].append(t)
    capped = []
    for day in sorted(by_day):
        top = sorted(by_day[day], key=lambda x: -abs(x['pnl']))[:daily_cap]
        capped.extend(top)
    return capped


def report(trades, label="", show_exit=False, show_sig=False):
    n = len(trades)
    if n == 0:
        print(f"  {label:>45} — no trades")
        return None
    w = sum(1 for t in trades if t['pnl'] > 0)
    ev = sum(t['pnl'] for t in trades) / n
    hold = sum(t['hold'] for t in trades) / n
    pf_up = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    pf_dn = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0)) or 1
    print(f"  {label:>45} N={n:>3} WR={w/n*100:>5.1f}% EV={ev:>+6.2f}% PF={pf_up/pf_dn:>4.1f}x Hold={hold:>4.1f}d")
    if show_exit:
        by_ex = defaultdict(list)
        for t in trades:
            by_ex[t['exit']].append(t)
        for ex, st in sorted(by_ex.items(), key=lambda x: -len(x[1])):
            sw = sum(1 for t in st if t['pnl'] > 0)
            sev = sum(t['pnl'] for t in st) / len(st)
            print(f"      exit {ex:>12}: N={len(st):>3} WR={sw/len(st)*100:>5.0f}% EV={sev:>+6.2f}%")
    if show_sig:
        by_sig = defaultdict(list)
        for t in trades:
            by_sig[t['sig']].append(t)
        for sig, st in sorted(by_sig.items(), key=lambda x: -len(x[1])):
            sw = sum(1 for t in st if t['pnl'] > 0)
            sev = sum(t['pnl'] for t in st) / len(st)
            print(f"      {sig:>22}: N={len(st):>3} WR={sw/len(st)*100:>5.0f}% EV={sev:>+6.2f}%")
    return {'n': n, 'wr': w/n*100, 'ev': ev, 'pf': pf_up/pf_dn, 'hold': hold}


# =====================================================================
print("=" * 100)
print("  EXP 1: HARD STOP LEVEL FOR COUNTER SIGNALS")
print("  Baseline: -8% hard stop. Test wider stops.")
print("=" * 100)

for hs in [6, 8, 10, 12, 15, 20]:
    route = dict(CURRENT)
    for s in COUNTER_SIGS:
        route[s] = mk_counter_hs(hs)
    trades = run(route, exclude={'fund_spike'})
    counter_trades = [t for t in trades if t['sig'] in COUNTER_SIGS]
    report(counter_trades, f"counter sigs, hard_stop={hs}%", show_exit=True)
    print()

# Full strategy impact
print("\n  --- Full strategy impact ---")
for hs in [8, 10, 12, 15]:
    route = dict(CURRENT)
    for s in COUNTER_SIGS:
        route[s] = mk_counter_hs(hs)
    trades = run(route, exclude={'fund_spike'})
    report(trades, f"FULL adaptive, counter HS={hs}%")


print()
print("=" * 100)
print("  EXP 2: COUNTER WITH BREAKEVEN PROTECTION")
print("  Once MFE hits trigger%, lock in lock% profit.")
print("=" * 100)

for trig in [3, 4, 5, 7]:
    for lock in [0.0, 0.5, 1.0]:
        route = dict(CURRENT)
        for s in COUNTER_SIGS:
            route[s] = mk_counter_be(trig, lock)
        trades = run(route, exclude={'fund_spike'})
        counter_trades = [t for t in trades if t['sig'] in COUNTER_SIGS]
        report(counter_trades, f"counter BE trig={trig}% lock={lock}%")

# Best BE vs baseline
print("\n  --- Best BE vs baseline (full strategy) ---")
route_base = dict(CURRENT)
trades_base = run(route_base, exclude={'fund_spike'})
report(trades_base, "BASELINE (no BE)")

for trig, lock in [(3, 0.5), (4, 0.5), (5, 0.5), (5, 1.0), (7, 0.5)]:
    route = dict(CURRENT)
    for s in COUNTER_SIGS:
        route[s] = mk_counter_be(trig, lock)
    trades = run(route, exclude={'fund_spike'})
    report(trades, f"BE trig={trig}% lock={lock}% (full)")


print()
print("=" * 100)
print("  EXP 3: COUNTER WITH TRAILING STOP")
print("  Once MFE hits trigger%, trail stop at trail% from peak.")
print("=" * 100)

for trig in [3, 5, 7]:
    for trail in [2, 3, 4, 5]:
        route = dict(CURRENT)
        for s in COUNTER_SIGS:
            route[s] = mk_counter_trail(trig, trail)
        trades = run(route, exclude={'fund_spike'})
        counter_trades = [t for t in trades if t['sig'] in COUNTER_SIGS]
        report(counter_trades, f"counter trail trig={trig}% dd={trail}%")

# Best trail vs baseline
print("\n  --- Best trail combos (full strategy) ---")
for trig, trail in [(3, 3), (3, 4), (5, 3), (5, 4), (5, 5), (7, 4), (7, 5)]:
    route = dict(CURRENT)
    for s in COUNTER_SIGS:
        route[s] = mk_counter_trail(trig, trail)
    trades = run(route, exclude={'fund_spike'})
    report(trades, f"trail trig={trig}% dd={trail}% (full)")


print()
print("=" * 100)
print("  EXP 4: COMBO — WIDER HARD STOP + TRAILING PROTECTION")
print("  Idea: widen HS to 12-15%, add trail once profitable")
print("=" * 100)

def mk_counter_combo(hs, trig, trail):
    """Counter with wider hard stop + trailing after trigger."""
    def fn(bars, sig, cache):
        ei = sig.bar_idx
        ep = sig.entry_price
        d = sig.direction
        cs = COUNTER_SIGNALS.get(d, set())
        if cache is None:
            cache = {}
        mf, ma = 0.0, 0.0
        trail_active = False
        peak = 0.0

        for j in range(ei + 1, min(ei + MH + 1, len(bars))):
            fav, adv = _fav_adv(bars, ei, d, j)
            mf, ma = max(mf, fav), max(ma, adv)

            if not trail_active and mf >= trig:
                trail_active = True
                peak = mf

            if trail_active:
                peak = max(peak, fav)
                dd = peak - fav
                if dd >= trail:
                    pnl = _walk_pnl(bars, ei, d, j)
                    return ExitResult(j, bars[j].close, "trail_be", pnl, j - ei, ma, mf)

            if adv >= hs:
                return ExitResult(j, bars[j].close, "hard_stop", -hs, j - ei, ma, mf)

            triggered = cache.get(j, ())
            for st, sd in triggered:
                if st in cs:
                    pnl = _walk_pnl(bars, ei, d, j)
                    return ExitResult(j, bars[j].close, "counter", pnl, j - ei, ma, mf)

        last = min(ei + MH, len(bars) - 1)
        pnl = _walk_pnl(bars, ei, d, last)
        return ExitResult(last, bars[last].close, "timeout", pnl, last - ei, ma, mf)
    return fn

for hs in [10, 12, 15]:
    for trig, trail in [(3, 3), (5, 4), (5, 3), (7, 5)]:
        route = dict(CURRENT)
        for s in COUNTER_SIGS:
            route[s] = mk_counter_combo(hs, trig, trail)
        trades = run(route, exclude={'fund_spike'})
        report(trades, f"combo HS={hs}% trig={trig}% trail={trail}%")

# Best combos with exit breakdown
print("\n  --- Top combos with exit breakdown ---")
for hs, trig, trail in [(12, 5, 4), (12, 5, 3), (15, 5, 4), (10, 3, 3)]:
    route = dict(CURRENT)
    for s in COUNTER_SIGS:
        route[s] = mk_counter_combo(hs, trig, trail)
    trades = run(route, exclude={'fund_spike'})
    report(trades, f"combo HS={hs} t={trig} tr={trail} (full)", show_exit=True, show_sig=True)
    print()


print()
print("=" * 100)
print("  EXP 5: OVEREXTENSION — BEST EXIT STRATEGY")
print("  Currently trail EV=-0.34%. Can we save it?")
print("=" * 100)

OE_EXITS = [
    ("trail 1.5/2.0", mk_trail(1.5, 2.0)),
    ("trail 2.0/3.0", mk_trail(2.0, 3.0)),
    ("fixed 3/2", mk_fixed(3, 2)),
    ("fixed 5/3", mk_fixed(5, 3)),
    ("fixed 2/1", mk_fixed(2, 1)),
    ("counter", mk_counter()),
    ("counter_be 3/0.5", mk_counter_be(3, 0.5)),
    ("zscore", mk_zscore()),
]

for label, fn in OE_EXITS:
    route = dict(CURRENT)
    route["overextension"] = fn
    trades = run(route, exclude={'fund_spike'})
    oe = [t for t in trades if t['sig'] == 'overextension']
    if oe:
        w = sum(1 for t in oe if t['pnl'] > 0)
        ev = sum(t['pnl'] for t in oe) / len(oe)
        hold = sum(t['hold'] for t in oe) / len(oe)
        print(f"  OE → {label:>20}: N={len(oe):>3} WR={w/len(oe)*100:>5.0f}% EV={ev:>+6.2f}% Hold={hold:>4.1f}d")

# OE with pvs filter combos
print("\n  --- OE with pvs filters ---")
for pvs_max in [12, 15, 20, 99]:
    for strat_name, strat_fn in [("trail", mk_trail()), ("fixed 3/2", mk_fixed(3, 2)), ("fixed 2/1", mk_fixed(2, 1))]:
        route = dict(CURRENT)
        route["overextension"] = strat_fn
        filters = {"overextension": lambda b, mx=pvs_max: b['pvs'] < mx and b['pvs'] > 8}
        trades = run(route, exclude={'fund_spike'}, filters=filters)
        oe = [t for t in trades if t['sig'] == 'overextension']
        if oe:
            w = sum(1 for t in oe if t['pnl'] > 0)
            ev = sum(t['pnl'] for t in oe) / len(oe)
            label = f"pvs 8-{pvs_max} {strat_name}"
            print(f"  OE {label:>25}: N={len(oe):>3} WR={w/len(oe)*100:>5.0f}% EV={ev:>+6.2f}%")


print()
print("=" * 100)
print("  EXP 6: OVERHEAT — BEST EXIT + FILTERS")
print("=" * 100)

OH_EXITS = [
    ("trail 1.5/2.0", mk_trail(1.5, 2.0)),
    ("counter", mk_counter()),
    ("counter_be 3/0.5", mk_counter_be(3, 0.5)),
    ("fixed 3/2", mk_fixed(3, 2)),
    ("fixed 5/3", mk_fixed(5, 3)),
    ("zscore", mk_zscore()),
    ("hybrid", mk_hybrid()),
]

for label, fn in OH_EXITS:
    route = dict(CURRENT)
    route["overheat"] = fn
    trades = run(route, exclude={'fund_spike'})
    oh = [t for t in trades if t['sig'] == 'overheat']
    if oh:
        w = sum(1 for t in oh if t['pnl'] > 0)
        ev = sum(t['pnl'] for t in oh) / len(oh)
        print(f"  OH → {label:>20}: N={len(oh):>3} WR={w/len(oh)*100:>5.0f}% EV={ev:>+6.2f}%")

# OH with filters
print("\n  --- OH with quality filters ---")
for oi_min in [1.5, 2.0, 2.5]:
    route = dict(CURRENT)
    filters = {"overheat": lambda b, oim=oi_min: b['oi_z'] > oim}
    trades = run(route, exclude={'fund_spike'}, filters=filters)
    oh = [t for t in trades if t['sig'] == 'overheat']
    if oh:
        w = sum(1 for t in oh if t['pnl'] > 0)
        ev = sum(t['pnl'] for t in oh) / len(oh)
        print(f"  OH oi_z>{oi_min}: N={len(oh):>3} WR={w/len(oh)*100:>5.0f}% EV={ev:>+6.2f}%")


print()
print("=" * 100)
print("  EXP 7: FINAL COMPARISON — ALL IMPROVEMENTS COMBINED")
print("=" * 100)

# A: Baseline (current adaptive, no fs)
trades_a = run(CURRENT, exclude={'fund_spike'})
report(trades_a, "A: Baseline (current)", show_sig=True)
print()

# B: Clean (no fs+oe+oh) — previous best
trades_b = run(CURRENT, exclude={'fund_spike', 'overextension', 'overheat'})
report(trades_b, "B: Clean (no fs+oe+oh)")
print()

# C: Improved counter (best combo from exp 4) — test multiple
for hs, trig, trail in [(10, 3, 3), (12, 5, 4), (12, 5, 3)]:
    route_c = dict(CURRENT)
    for s in COUNTER_SIGS:
        route_c[s] = mk_counter_combo(hs, trig, trail)
    trades_c = run(route_c, exclude={'fund_spike'})
    report(trades_c, f"C: Improved counter HS={hs}/t{trig}/tr{trail}", show_sig=True)
    print()

# D: Improved counter + OE fixed 3/2 + OH filtered
for hs, trig, trail in [(12, 5, 4), (10, 3, 3)]:
    route_d = dict(CURRENT)
    for s in COUNTER_SIGS:
        route_d[s] = mk_counter_combo(hs, trig, trail)
    route_d["overextension"] = mk_fixed(3, 2)
    route_d["overheat"] = mk_hybrid()
    trades_d = run(route_d, exclude={'fund_spike'})
    report(trades_d, f"D: Counter+OE+OH improved {hs}/{trig}/{trail}", show_sig=True, show_exit=True)
    print()

# E: Conservative profile with improved counter
route_e = {
    "liq_short_squeeze": mk_counter_combo(12, 5, 4),
    "div_squeeze_3d":    mk_counter_combo(12, 5, 4),
    "oi_buildup_stall":  mk_counter_combo(12, 5, 4),
    "vol_divergence":    mk_trail(),
    "fund_reversal":     mk_zscore(),
    "distribution":      mk_fixed(),
}
trades_e = run(route_e)
report(trades_e, "E: Conservative + improved counter", show_sig=True)
print()


print()
print("=" * 100)
print("  SUMMARY TABLE")
print("=" * 100)
print(f"  {'Config':>50} {'N':>4} {'WR':>6} {'EV':>7} {'PF':>5} {'Hold':>5}")
print("  " + "-" * 80)

summary_configs = [
    ("Baseline (current, no fs)", CURRENT, {'fund_spike'}, None),
    ("Clean (no fs+oe+oh)", CURRENT, {'fund_spike', 'overextension', 'overheat'}, None),
]

# Add best combos dynamically
for hs, trig, trail in [(10, 3, 3), (12, 5, 4), (12, 5, 3)]:
    route = dict(CURRENT)
    for s in COUNTER_SIGS:
        route[s] = mk_counter_combo(hs, trig, trail)
    summary_configs.append((f"Counter HS={hs}/t{trig}/tr{trail}", route, {'fund_spike'}, None))

# Best full combo
for hs, trig, trail in [(12, 5, 4), (10, 3, 3)]:
    route = dict(CURRENT)
    for s in COUNTER_SIGS:
        route[s] = mk_counter_combo(hs, trig, trail)
    route["overextension"] = mk_fixed(3, 2)
    route["overheat"] = mk_hybrid()
    summary_configs.append((f"Full improved {hs}/{trig}/{trail}", route, {'fund_spike'}, None))

# Conservative improved
summary_configs.append(("Conservative + improved counter", route_e, None, None))

for label, route, excl, filt in summary_configs:
    trades = run(route, exclude=excl, filters=filt)
    n = len(trades)
    if n == 0:
        continue
    w = sum(1 for t in trades if t['pnl'] > 0)
    ev = sum(t['pnl'] for t in trades) / n
    pf_up = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    pf_dn = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0)) or 1
    hold = sum(t['hold'] for t in trades) / n
    print(f"  {label:>50} {n:>4} {w/n*100:>5.1f}% {ev:>+6.2f}% {pf_up/pf_dn:>4.1f}x {hold:>4.1f}d")
