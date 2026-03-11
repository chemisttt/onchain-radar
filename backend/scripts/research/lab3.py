#!/usr/bin/env python3
"""
Lab 3: Advanced strategy experiments — symbols, regimes, clustering, combos.
"""
import sys, os, pickle
sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')
sys.argv = ['test', '--4h']

from scripts.setup_backtest import (
    strategy_trailing, strategy_counter, strategy_fixed, strategy_zscore, strategy_hybrid,
    BARS_PER_DAY, COUNTER_SIGNALS, ExitResult, _fav_adv, _walk_pnl
)
from collections import defaultdict
from datetime import datetime, timedelta

CACHE_PATH = '/tmp/signal_cache.pkl'
with open(CACHE_PATH, 'rb') as f:
    RAW = pickle.load(f)
print(f"Loaded {len(RAW)} signals\n")

MH = 30 * BARS_PER_DAY


# ─── Strategy factories ─────────────────────────────────────────────
def mk_counter(hs=8.0):
    def fn(bars, sig, cache):
        ei = sig.bar_idx; ep = sig.entry_price; d = sig.direction
        cs = COUNTER_SIGNALS.get(d, set())
        if cache is None: cache = {}
        mf, ma = 0.0, 0.0
        for j in range(ei + 1, min(ei + MH + 1, len(bars))):
            fav, adv = _fav_adv(bars, ei, d, j)
            mf, ma = max(mf, fav), max(ma, adv)
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

def mk_trail(am=1.5, bp=2.0):
    return lambda bars, sig, cache: strategy_trailing(bars, sig, max_hold=MH, atr_mult=am, be_pct=bp)

def mk_fixed(tp=5, sl=3):
    return lambda bars, sig, cache: strategy_fixed(bars, sig, tp_pct=tp, sl_pct=sl, timeout=7*BARS_PER_DAY)

def mk_zscore():
    return lambda bars, sig, cache: strategy_zscore(bars, sig, max_hold=MH)

def mk_hybrid():
    return lambda bars, sig, cache: strategy_hybrid(bars, sig, cache, max_hold=MH)


# ─── Routing ─────────────────────────────────────────────────────────
ADAPTIVE = {
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

# Better version with HS=12
ADAPTIVE_12 = dict(ADAPTIVE)
for s in ["liq_short_squeeze", "div_squeeze_3d", "div_top_1d", "oi_buildup_stall"]:
    ADAPTIVE_12[s] = mk_counter(hs=12.0)


# ─── Engine ──────────────────────────────────────────────────────────
def run(routing, signals=None, exclude=None, filters=None, daily_cap=5):
    signals = signals or RAW
    exclude = exclude or set()
    filters = filters or {}
    trades = []
    for item in signals:
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
                'oi_z': b['oi_z'], 'fund_z': b['fund_z'],
                'price_chg': b.get('price_chg', 0),
            })
    by_day = defaultdict(list)
    for t in trades:
        by_day[t['date']].append(t)
    capped = []
    for day in sorted(by_day):
        top = sorted(by_day[day], key=lambda x: -abs(x['pnl']))[:daily_cap]
        capped.extend(top)
    return capped


def report(trades, label=""):
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
    return {'n': n, 'wr': w/n*100, 'ev': ev, 'pf': pf_up/pf_dn}


# =====================================================================
print("=" * 100)
print("  EXP 1: PER-SYMBOL ANALYSIS")
print("  Which coins produce clean signals vs noise?")
print("=" * 100)

all_trades = run(ADAPTIVE_12, exclude={'fund_spike'})
by_sym = defaultdict(list)
for t in all_trades:
    by_sym[t['sym']].append(t)

print(f"\n  {'Symbol':>12} {'N':>4} {'WR':>6} {'EV':>8} {'PF':>5} {'AvgHold':>7}  Signals")
print("  " + "-" * 85)
sym_stats = []
for sym in sorted(by_sym, key=lambda s: -len(by_sym[s])):
    st = by_sym[sym]
    n = len(st)
    if n < 3:
        continue
    w = sum(1 for t in st if t['pnl'] > 0)
    ev = sum(t['pnl'] for t in st) / n
    hold = sum(t['hold'] for t in st) / n
    pf_up = sum(t['pnl'] for t in st if t['pnl'] > 0)
    pf_dn = abs(sum(t['pnl'] for t in st if t['pnl'] <= 0)) or 1
    sigs = defaultdict(int)
    for t in st:
        sigs[t['sig']] += 1
    sig_str = ", ".join(f"{s}:{c}" for s, c in sorted(sigs.items(), key=lambda x: -x[1])[:3])
    sym_stats.append((sym, n, w/n*100, ev, pf_up/pf_dn))
    print(f"  {sym:>12} {n:>4} {w/n*100:>5.1f}% {ev:>+7.2f}% {pf_up/pf_dn:>4.1f}x {hold:>6.1f}d  {sig_str}")

# Best and worst symbols
best_syms = sorted(sym_stats, key=lambda x: -x[3])
print(f"\n  TOP 5 by EV: {', '.join(s[0] for s in best_syms[:5])}")
print(f"  BOT 5 by EV: {', '.join(s[0] for s in best_syms[-5:])}")

# Test: exclude worst symbols
worst_set = {s[0] for s in best_syms if s[3] < -1.0}
if worst_set:
    print(f"\n  Exclude symbols with EV < -1%: {worst_set}")
    filtered = [item for item in RAW if item['sym'] not in worst_set]
    trades_clean = run(ADAPTIVE_12, signals=filtered, exclude={'fund_spike'})
    report(trades_clean, "Without worst symbols")
    report(all_trades, "All symbols (baseline)")


print()
print("=" * 100)
print("  EXP 2: BTC TREND AS GLOBAL REGIME FILTER")
print("  Idea: use BTC's trend to filter all signals")
print("=" * 100)

# Get BTC trend at each signal date
btc_trend_by_date = {}
for item in RAW:
    if item['sym'] == 'BTCUSDT':
        btc_trend_by_date[item['sig_date']] = item['daily_bar']['trend']

# Also collect from all BTC items even if not signal
# Actually we only have signal dates, not all dates. Let's use what we have.
# For non-BTC signals, find nearest BTC signal date
print(f"  BTC trend data points: {len(btc_trend_by_date)}")

# Analyze: how do trades perform when BTC is up vs down?
# We need BTC trend for each trade date, but we only have BTC at signal dates
# Use each item's own trend as proxy (it's the asset's trend, not BTC's)
# Let's just split by the trade's own trend
for trend_name in ['up', 'down', 'neutral']:
    tt = [t for t in all_trades if t['trend'] == trend_name]
    if tt:
        report(tt, f"Trend={trend_name}")

# Split by direction + trend
print("\n  --- Direction × Trend ---")
for d in ['long', 'short']:
    for trend in ['up', 'down', 'neutral']:
        tt = [t for t in all_trades if t['dir'] == d and t['trend'] == trend]
        if len(tt) >= 3:
            w = sum(1 for t in tt if t['pnl'] > 0)
            ev = sum(t['pnl'] for t in tt) / len(tt)
            print(f"  {d:>5} + {trend:>7}: N={len(tt):>3} WR={w/len(tt)*100:>5.0f}% EV={ev:>+6.2f}%")

# Test: only take longs in uptrend, shorts in downtrend (momentum)
momentum = [t for t in all_trades if
            (t['dir'] == 'long' and t['trend'] == 'up') or
            (t['dir'] == 'short' and t['trend'] == 'down') or
            t['trend'] == 'neutral']
report(momentum, "Momentum: long↑ short↓ neutral=any")

# Test: contrarian — longs in downtrend, shorts in uptrend
contrarian = [t for t in all_trades if
              (t['dir'] == 'long' and t['trend'] == 'down') or
              (t['dir'] == 'short' and t['trend'] == 'up') or
              t['trend'] == 'neutral']
report(contrarian, "Contrarian: long↓ short↑ neutral=any")

# Test: skip neutral (only strong trends)
strong = [t for t in all_trades if t['trend'] != 'neutral']
report(strong, "Strong trend only (no neutral)")


print()
print("=" * 100)
print("  EXP 3: SIGNAL CLUSTERING — BUSY DAYS")
print("  When 2+ signals fire same day, is that better or worse?")
print("=" * 100)

by_day = defaultdict(list)
for t in all_trades:
    by_day[t['date']].append(t)

for min_n, max_n, label in [(1, 1, "exactly 1"), (2, 2, "exactly 2"), (3, 99, "3+"), (1, 2, "1-2")]:
    day_trades = []
    for day, trades in by_day.items():
        if min_n <= len(trades) <= max_n:
            day_trades.extend(trades)
    if day_trades:
        report(day_trades, f"Days with {label} signals")

# Same symbol, multiple signals within 3 days
print("\n  --- Same symbol, signals within 3 days ---")
by_sym_date = defaultdict(list)
for t in all_trades:
    by_sym_date[t['sym']].append(t)

clustered = set()  # indices of clustered trades
solo = set()
for sym, trades in by_sym_date.items():
    trades_sorted = sorted(trades, key=lambda x: x['date'])
    for i, t in enumerate(trades_sorted):
        is_cluster = False
        for j, t2 in enumerate(trades_sorted):
            if i == j:
                continue
            d1 = datetime.strptime(t['date'], '%Y-%m-%d')
            d2 = datetime.strptime(t2['date'], '%Y-%m-%d')
            if abs((d1 - d2).days) <= 3:
                is_cluster = True
                break
        if is_cluster:
            clustered.add(id(t))
        else:
            solo.add(id(t))

cluster_trades = [t for t in all_trades if id(t) in clustered]
solo_trades = [t for t in all_trades if id(t) in solo]
report(cluster_trades, "Clustered (same sym ±3d)")
report(solo_trades, "Solo (isolated signal)")


print()
print("=" * 100)
print("  EXP 4: COMPOSITE QUALITY SCORE")
print("  Combine pvs, oi_z, fund_z into entry quality metric")
print("=" * 100)

# Score each trade's entry quality
for t in all_trades:
    # Higher = more extreme (potentially better signal)
    score = 0
    if abs(t['pvs']) > 10:
        score += 1
    if abs(t['oi_z']) > 1.5:
        score += 1
    if abs(t['fund_z']) > 1.0:
        score += 1
    t['quality'] = score

for q in [0, 1, 2, 3]:
    qt = [t for t in all_trades if t['quality'] == q]
    if qt:
        report(qt, f"Quality score = {q}")

# High quality only
hq = [t for t in all_trades if t['quality'] >= 2]
report(hq, "Quality >= 2 (2+ extreme metrics)")

lq = [t for t in all_trades if t['quality'] <= 1]
report(lq, "Quality <= 1 (mild entry)")


print()
print("=" * 100)
print("  EXP 5: PVS BANDS — DOES DISTANCE FROM SMA MATTER?")
print("=" * 100)

for lo, hi, label in [(-99, 0, "pvs<0 (below SMA)"), (0, 5, "pvs 0-5"),
                       (5, 10, "pvs 5-10"), (10, 15, "pvs 10-15"),
                       (15, 30, "pvs 15-30"), (30, 99, "pvs 30+")]:
    band = [t for t in all_trades if lo <= t['pvs'] < hi]
    if len(band) >= 3:
        report(band, label)


print()
print("=" * 100)
print("  EXP 6: OI_Z EXTREME FILTER")
print("  Do signals work better when OI is extreme?")
print("=" * 100)

for lo, hi, label in [(-99, -1, "oi_z < -1 (low OI)"), (-1, 0, "oi_z -1..0"),
                       (0, 1, "oi_z 0..1"), (1, 2, "oi_z 1..2"),
                       (2, 3, "oi_z 2..3"), (3, 99, "oi_z 3+")]:
    band = [t for t in all_trades if lo <= t['oi_z'] < hi]
    if len(band) >= 3:
        report(band, label)


print()
print("=" * 100)
print("  EXP 7: FUND_Z BANDS")
print("=" * 100)

for lo, hi, label in [(-99, -1, "fund_z < -1"), (-1, 0, "fund_z -1..0"),
                       (0, 1, "fund_z 0..1"), (1, 2, "fund_z 1..2"),
                       (2, 99, "fund_z 2+")]:
    band = [t for t in all_trades if lo <= t['fund_z'] < hi]
    if len(band) >= 3:
        report(band, label)


print()
print("=" * 100)
print("  EXP 8: SIGNAL PAIRS — SAME SYMBOL WITHIN 3 DAYS")
print("  Which signal combinations co-occur and what's their combined EV?")
print("=" * 100)

# Build signal pairs that fire within 3 days on same symbol
pair_results = defaultdict(list)
all_items_sorted = sorted(RAW, key=lambda x: (x['sym'], x['sig_date']))

for i, item1 in enumerate(all_items_sorted):
    for j in range(i + 1, len(all_items_sorted)):
        item2 = all_items_sorted[j]
        if item1['sym'] != item2['sym']:
            break
        d1 = datetime.strptime(item1['sig_date'], '%Y-%m-%d')
        d2 = datetime.strptime(item2['sig_date'], '%Y-%m-%d')
        delta = (d2 - d1).days
        if delta > 5:
            break
        if delta <= 3:
            s1, s2 = sorted([item1['sig'].signal_type, item2['sig'].signal_type])
            pair = f"{s1} + {s2}"
            pair_results[pair].append((item1, item2))

print(f"  Found {sum(len(v) for v in pair_results.values())} signal pairs across {len(pair_results)} pair types")
print()

# For each pair type, run the SECOND signal's trade and check EV
# (idea: first signal is "confirmation", second is actual entry)
print(f"  {'Pair':>45} {'N':>4} {'AvgDelta':>8}")
print("  " + "-" * 65)
for pair, items in sorted(pair_results.items(), key=lambda x: -len(x[1])):
    if len(items) < 3:
        continue
    avg_delta = sum(
        (datetime.strptime(i2['sig_date'], '%Y-%m-%d') - datetime.strptime(i1['sig_date'], '%Y-%m-%d')).days
        for i1, i2 in items
    ) / len(items)
    print(f"  {pair:>45} {len(items):>4} {avg_delta:>7.1f}d")

# Now test: when a pair fires, trade the second signal. Compare vs solo.
print("\n  --- Pair-confirmed trades vs solo trades ---")
# Get dates+syms where pairs occurred
pair_entries = set()  # (sym, date) of second signal in pair
for pair, items in pair_results.items():
    for i1, i2 in items:
        pair_entries.add((i2['sym'], i2['sig_date']))

paired_trades = [t for t in all_trades if (t['sym'], t['date']) in pair_entries]
unpaired_trades = [t for t in all_trades if (t['sym'], t['date']) not in pair_entries]
report(paired_trades, "Pair-confirmed (2nd signal)")
report(unpaired_trades, "Solo (no nearby pair)")


print()
print("=" * 100)
print("  EXP 9: DIRECTION AGREEMENT — SAME DIRECTION PAIRS")
print("  Signal pairs where both point same direction")
print("=" * 100)

same_dir_entries = set()
diff_dir_entries = set()
for pair, items in pair_results.items():
    for i1, i2 in items:
        if i1['sig'].direction == i2['sig'].direction:
            same_dir_entries.add((i2['sym'], i2['sig_date']))
        else:
            diff_dir_entries.add((i2['sym'], i2['sig_date']))

same_dir_trades = [t for t in all_trades if (t['sym'], t['date']) in same_dir_entries]
diff_dir_trades = [t for t in all_trades if (t['sym'], t['date']) in diff_dir_entries]
report(same_dir_trades, "Same-direction pair confirmation")
report(diff_dir_trades, "Opposite-direction pair (conflict)")


print()
print("=" * 100)
print("  EXP 10: MONTHLY / QUARTERLY PATTERNS")
print("=" * 100)

for month in range(1, 13):
    mt = [t for t in all_trades if int(t['date'].split('-')[1]) == month]
    if mt:
        w = sum(1 for t in mt if t['pnl'] > 0)
        ev = sum(t['pnl'] for t in mt) / len(mt)
        month_name = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][month-1]
        print(f"  {month_name}: N={len(mt):>3} WR={w/len(mt)*100:>5.0f}% EV={ev:>+6.2f}%")

# Quarters
print()
for q in [1, 2, 3, 4]:
    qt = [t for t in all_trades if (int(t['date'].split('-')[1]) - 1) // 3 + 1 == q]
    if qt:
        report(qt, f"Q{q}")


print()
print("=" * 100)
print("  EXP 11: PRICE MOMENTUM AT ENTRY")
print("  Does recent price change affect signal quality?")
print("=" * 100)

for lo, hi, label in [(-99, -5, "price_chg < -5%"), (-5, -2, "price_chg -5..-2%"),
                       (-2, 0, "price_chg -2..0%"), (0, 2, "price_chg 0..2%"),
                       (2, 5, "price_chg 2..5%"), (5, 99, "price_chg > 5%")]:
    band = [t for t in all_trades if lo <= t.get('price_chg', 0) < hi]
    if len(band) >= 3:
        report(band, label)


print()
print("=" * 100)
print("  EXP 12: BEST COMBINED FILTERS (grid search)")
print("  Find optimal filter combination from experiments above")
print("=" * 100)

best_ev = -999
best_label = ""
best_n = 0

# Test combinations of: trend filter, quality filter, pvs band, cluster filter
for trend_filter in [None, "momentum", "contrarian"]:
    for min_quality in [0, 1, 2]:
        for pvs_max in [15, 30, 99]:
            filtered = list(all_trades)

            if trend_filter == "momentum":
                filtered = [t for t in filtered if
                    (t['dir'] == 'long' and t['trend'] == 'up') or
                    (t['dir'] == 'short' and t['trend'] == 'down') or
                    t['trend'] == 'neutral']
            elif trend_filter == "contrarian":
                filtered = [t for t in filtered if
                    (t['dir'] == 'long' and t['trend'] == 'down') or
                    (t['dir'] == 'short' and t['trend'] == 'up') or
                    t['trend'] == 'neutral']

            filtered = [t for t in filtered if t.get('quality', 0) >= min_quality]
            filtered = [t for t in filtered if t['pvs'] < pvs_max]

            n = len(filtered)
            if n < 20:
                continue
            w = sum(1 for t in filtered if t['pnl'] > 0)
            ev = sum(t['pnl'] for t in filtered) / n
            pf_up = sum(t['pnl'] for t in filtered if t['pnl'] > 0)
            pf_dn = abs(sum(t['pnl'] for t in filtered if t['pnl'] <= 0)) or 1

            label = f"trend={trend_filter or 'any'} q>={min_quality} pvs<{pvs_max}"
            if ev > best_ev:
                best_ev = ev
                best_label = label
                best_n = n
            if ev > 1.5 and n >= 30:  # only print interesting ones
                print(f"  {label:>45} N={n:>3} WR={w/n*100:>5.1f}% EV={ev:>+6.2f}% PF={pf_up/pf_dn:>4.1f}x")

print(f"\n  BEST: {best_label} (N={best_n}, EV={best_ev:>+.2f}%)")


print()
print("=" * 100)
print("  SUMMARY: KEY INSIGHTS")
print("=" * 100)
