#!/usr/bin/env python3
"""
Hurst Exponent Research — regime detection filter for signal system.

Theory:
  H > 0.55 → trending (persistent)   — trend-following signals better
  H < 0.45 → mean-reverting          — counter-trend signals better
  0.45-0.55 → random walk            — no edge, skip?

Method: R/S (Rescaled Range) analysis on log returns.

Usage:
    python3 scripts/research/cache_signals.py   # run once
    python3 scripts/research/hurst_research.py   # this script
"""
import sys, os, pickle, math
import numpy as np
from collections import defaultdict

sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')
sys.argv = ['test', '--4h']

from scripts.setup_backtest import (
    strategy_trailing, strategy_counter, strategy_fixed, strategy_zscore, strategy_hybrid,
    strategy_adaptive, _apply_costs, BARS_PER_DAY, HARD_STOP_PCT
)

CACHE_PATH = '/tmp/signal_cache.pkl'

with open(CACHE_PATH, 'rb') as f:
    cache = pickle.load(f)

RAW = cache['all_signals']
SYMBOLS_DATA = cache['symbols']
print(f"Loaded {len(RAW)} signals, {len(SYMBOLS_DATA)} symbols\n")

MH = 30 * BARS_PER_DAY


# ─── Hurst Exponent (R/S method) ────────────────────────────────────
def hurst_rs(series: np.ndarray, min_window: int = 20, max_window: int = None) -> float:
    """Compute Hurst exponent via R/S analysis on a price series.

    Uses sub-series division at multiple scales, fits log(R/S) vs log(n).
    Returns H ∈ (0, 1). Returns 0.5 if computation fails.
    """
    n = len(series)
    if n < min_window * 2:
        return 0.5

    # Log returns
    returns = np.diff(np.log(series))
    returns = returns[np.isfinite(returns)]
    if len(returns) < min_window:
        return 0.5

    if max_window is None:
        max_window = len(returns) // 2

    # Generate window sizes (powers of 2 + some intermediates)
    sizes = []
    s = min_window
    while s <= max_window:
        sizes.append(s)
        s = int(s * 1.5)
    if len(sizes) < 3:
        return 0.5

    log_sizes = []
    log_rs = []

    for size in sizes:
        n_chunks = len(returns) // size
        if n_chunks < 1:
            continue

        rs_values = []
        for i in range(n_chunks):
            chunk = returns[i * size:(i + 1) * size]
            mean = np.mean(chunk)
            deviations = np.cumsum(chunk - mean)
            r = np.max(deviations) - np.min(deviations)
            s = np.std(chunk, ddof=1)
            if s > 0:
                rs_values.append(r / s)

        if rs_values:
            log_sizes.append(math.log(size))
            log_rs.append(math.log(np.mean(rs_values)))

    if len(log_sizes) < 3:
        return 0.5

    # Linear regression: log(R/S) = H * log(n) + c
    log_sizes = np.array(log_sizes)
    log_rs = np.array(log_rs)

    # Least squares
    n_pts = len(log_sizes)
    sum_x = np.sum(log_sizes)
    sum_y = np.sum(log_rs)
    sum_xy = np.sum(log_sizes * log_rs)
    sum_x2 = np.sum(log_sizes ** 2)

    denom = n_pts * sum_x2 - sum_x ** 2
    if abs(denom) < 1e-12:
        return 0.5

    H = (n_pts * sum_xy - sum_x * sum_y) / denom
    return max(0.0, min(1.0, H))


# ─── Precompute rolling Hurst for all symbols ───────────────────────
def compute_rolling_hurst(closes: list, window: int = 200) -> np.ndarray:
    """Compute rolling Hurst exponent for each bar using trailing window."""
    n = len(closes)
    hurst_arr = np.full(n, 0.5)
    prices = np.array([c for c in closes], dtype=float)

    for i in range(window, n):
        segment = prices[i - window:i]
        if np.any(segment <= 0):
            continue
        hurst_arr[i] = hurst_rs(segment)

    return hurst_arr


print("Computing rolling Hurst for all symbols...")
WINDOWS = [100, 200, 400]  # 4h bars: ~17d, ~33d, ~67d
HURST_CACHE = {}  # sym → {window → np.array}

for sym, data in SYMBOLS_DATA.items():
    bars_4h = data['bars_4h']
    if not bars_4h:
        continue
    closes = [b.close for b in bars_4h]
    HURST_CACHE[sym] = {}
    for w in WINDOWS:
        HURST_CACHE[sym][w] = compute_rolling_hurst(closes, w)
    print(f"  {sym}: {len(closes)} bars, H(200) last={HURST_CACHE[sym][200][-1]:.3f}")

print()


# ─── Strategy dispatch (matches ADAPTIVE_EXIT from setup_backtest.py) ─
def mk_trail(am=1.5, bp=2.0):
    return lambda bars, sig, cache: strategy_trailing(bars, sig, max_hold=MH, atr_mult=am, be_pct=bp)

def mk_counter(hs=12.0):
    return lambda bars, sig, cache: strategy_counter(bars, sig, cache, max_hold=MH, hard_stop=hs)

def mk_fixed(tp=5, sl=3):
    return lambda bars, sig, cache: strategy_fixed(bars, sig, tp_pct=tp, sl_pct=sl, timeout=7*BARS_PER_DAY)

def mk_zscore():
    return lambda bars, sig, cache: strategy_zscore(bars, sig, max_hold=MH)

def mk_hybrid():
    return lambda bars, sig, cache: strategy_hybrid(bars, sig, cache, max_hold=MH)

# Matches ADAPTIVE_EXIT exactly (setup_backtest.py line 865)
CURRENT = {
    "liq_short_squeeze":   mk_counter(),     # counter_sig, hard_stop=12%
    "div_squeeze_3d":      mk_counter(),     # counter_sig
    "div_top_1d":          mk_counter(),     # counter_sig
    "distribution":        mk_fixed(),       # fixed 5/3/7d
    "oi_buildup_stall":    mk_fixed(),       # fixed (NOT counter!)
    "vol_divergence":      mk_counter(),     # counter_sig (NOT trail!)
    "overheat":            mk_fixed(),       # fixed (NOT trail!)
    "overextension":       mk_trail(),       # trail_atr
    "fund_reversal":       mk_zscore(),      # zscore_mr
    "capitulation":        mk_zscore(),      # zscore_mr
    "fund_spike":          mk_trail(),       # trail_atr
    "div_squeeze_1d":      mk_hybrid(),      # hybrid
    "momentum_divergence": mk_counter(),     # counter_sig
    "liq_ratio_extreme":   mk_counter(),     # counter_sig
}


# ─── Classify signal direction type ─────────────────────────────────
# Counter-trend: signals that bet AGAINST current trend
# Trend-following: signals that bet WITH current trend
COUNTER_TREND_SIGNALS = {
    "liq_short_squeeze",   # short squeeze → long (against short trend)
    "capitulation",        # panic selling → long (against selling)
    "div_squeeze_3d",      # divergence → reversal bet
    "div_squeeze_1d",
    "div_top_1d",          # divergence top → reversal
    "vol_divergence",      # volume divergence → reversal
    "momentum_divergence", # momentum divergence → reversal
    "liq_ratio_extreme",   # extreme liq ratio → reversal
    "fund_reversal",       # funding reversal → reversal
}

TREND_FOLLOWING_SIGNALS = {
    "distribution",        # distribution → continuation of selling
    "oi_buildup_stall",    # OI build → continuation
    "overextension",       # extended move → continuation or pullback
    "overheat",            # overheated → continuation warning
    "fund_spike",          # funding spike → continuation
}


# ─── Run engine with Hurst filter ────────────────────────────────────
def run(routing=None, exclude=None, hurst_filter=None, hurst_window=200,
        filters=None, daily_cap=5):
    """
    routing: None = use strategy_adaptive (production match), or dict of overrides
    hurst_filter: dict signal_type → fn(H) → bool, OR callable(st, dir, H) → bool
    """
    exclude = exclude or set()
    filters = filters or {}
    trades = []
    filtered_count = 0

    # Pre-sort by confluence for daily cap (matches setup_backtest)
    sorted_raw = sorted(RAW, key=lambda x: (-x['sig'].confluence, x['sig_date']))

    for item in sorted_raw:
        sig = item['sig']
        st = sig.signal_type
        if st in exclude:
            continue

        if routing:
            fn = routing.get(st)
            if fn is None:
                continue
        else:
            fn = lambda bars, sig, cache: strategy_adaptive(bars, sig, cache)

        b = item['daily_bar']
        if st in filters and not filters[st](b):
            continue

        # Hurst filter
        if hurst_filter:
            sym = item['sym']
            bar_idx_4h = sig.bar_idx_4h
            h_arr = HURST_CACHE.get(sym, {}).get(hurst_window)
            if h_arr is not None and bar_idx_4h < len(h_arr):
                H = h_arr[bar_idx_4h]
                if isinstance(hurst_filter, dict):
                    if st in hurst_filter and not hurst_filter[st](H):
                        filtered_count += 1
                        continue
                elif callable(hurst_filter):
                    if not hurst_filter(st, sig.direction, H):
                        filtered_count += 1
                        continue

        orig = sig.bar_idx
        sig.bar_idx = sig.bar_idx_4h
        result = fn(item['bars_4h'], sig, item['cache_4h'])
        sig.bar_idx = orig

        if result:
            # Apply costs (fees + funding)
            _apply_costs(result, item['bars_4h'], sig.bar_idx_4h, sig.direction)

            sym = item['sym']
            h_val = 0.5
            h_arr = HURST_CACHE.get(sym, {}).get(hurst_window)
            if h_arr is not None and sig.bar_idx_4h < len(h_arr):
                h_val = h_arr[sig.bar_idx_4h]

            trades.append({
                'date': item['sig_date'], 'sym': sym,
                'sig': st, 'dir': sig.direction,
                'pnl': result.net_pnl_pct, 'mfe': result.max_favorable_pct,
                'mae': result.max_drawdown_pct, 'exit': result.exit_reason,
                'hold': result.bars_held / BARS_PER_DAY,
                'trend': b['trend'], 'pvs': b['pvs'],
                'oi_z': b['oi_z'], 'fund_z': b['fund_z'],
                'hurst': h_val,
                'confluence': sig.confluence,
            })

    # Global daily cap — top by confluence (matches setup_backtest)
    by_day = defaultdict(list)
    for t in trades:
        by_day[t['date']].append(t)
    capped = []
    for day in sorted(by_day):
        day_trades = sorted(by_day[day], key=lambda x: -x['confluence'])
        capped.extend(day_trades[:daily_cap])
    return capped, filtered_count


def report(trades, label="", show_signals=False, show_hurst=False, filtered=0):
    n = len(trades)
    if n == 0:
        print(f"  {label:>45} — no trades")
        return None
    w = sum(1 for t in trades if t['pnl'] > 0)
    ev = sum(t['pnl'] for t in trades) / n
    hold = sum(t['hold'] for t in trades) / n
    pf_up = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    pf_dn = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0)) or 1
    filt_str = f" filt={filtered}" if filtered else ""
    print(f"  {label:>45} N={n:>3} WR={w/n*100:>5.1f}% EV={ev:>+6.2f}% PF={pf_up/pf_dn:>4.1f}x Hold={hold:>4.1f}d{filt_str}")

    if show_signals:
        by_sig = defaultdict(list)
        for t in trades:
            by_sig[t['sig']].append(t)
        for sig, st in sorted(by_sig.items(), key=lambda x: -len(x[1])):
            sw = sum(1 for x in st if x['pnl'] > 0)
            sev = sum(x['pnl'] for x in st) / len(st)
            h_avg = sum(x.get('hurst', 0.5) for x in st) / len(st)
            print(f"    {sig:>22}: N={len(st):>3} WR={sw/len(st)*100:>5.0f}% EV={sev:>+6.2f}% H_avg={h_avg:.3f}")

    if show_hurst:
        # Split by Hurst regime
        trending = [t for t in trades if t.get('hurst', 0.5) > 0.55]
        mr = [t for t in trades if t.get('hurst', 0.5) < 0.45]
        rw = [t for t in trades if 0.45 <= t.get('hurst', 0.5) <= 0.55]
        for regime, rt in [("H>0.55 trending", trending), ("H<0.45 MR", mr), ("0.45-0.55 RW", rw)]:
            if rt:
                rw_ = sum(1 for t in rt if t['pnl'] > 0)
                rev = sum(t['pnl'] for t in rt) / len(rt)
                print(f"      {regime:>20}: N={len(rt):>3} WR={rw_/len(rt)*100:>5.0f}% EV={rev:>+6.2f}%")


# =====================================================================
#  EXPERIMENT 1: HURST DISTRIBUTION ANALYSIS
# =====================================================================
print("=" * 100)
print("  EXPERIMENT 1: HURST DISTRIBUTION ACROSS SYMBOLS AND WINDOWS")
print("=" * 100)

for w in WINDOWS:
    all_h = []
    for sym in SYMBOLS_DATA:
        h_arr = HURST_CACHE.get(sym, {}).get(w)
        if h_arr is not None:
            # Skip warmup period
            valid = h_arr[w:]
            all_h.extend(valid[valid != 0.5])

    all_h = np.array(all_h)
    if len(all_h) > 0:
        pct_trend = np.sum(all_h > 0.55) / len(all_h) * 100
        pct_mr = np.sum(all_h < 0.45) / len(all_h) * 100
        pct_rw = np.sum((all_h >= 0.45) & (all_h <= 0.55)) / len(all_h) * 100
        print(f"  Window={w:>3} (≈{w*4//24:>2}d): mean={np.mean(all_h):.3f} std={np.std(all_h):.3f} "
              f"trending={pct_trend:.0f}% MR={pct_mr:.0f}% RW={pct_rw:.0f}%")

print()


# =====================================================================
#  EXPERIMENT 2: BASELINE WITH HURST DATA
# =====================================================================
print("=" * 100)
print("  EXPERIMENT 2: BASELINE — ALL SIGNALS WITH HURST REGIME BREAKDOWN")
print("=" * 100)

for w in WINDOWS:
    print(f"\n  --- Window={w} (≈{w*4//24}d) ---")
    t, _ = run(exclude={'fund_spike'}, hurst_window=w)
    report(t, f"baseline w={w}", show_signals=True, show_hurst=True)

print()


# =====================================================================
#  EXPERIMENT 3: HURST REGIME FILTER — COUNTER-TREND SIGNALS
# =====================================================================
print("=" * 100)
print("  EXPERIMENT 3: SKIP COUNTER-TREND SIGNALS IN STRONG TRENDS (H > threshold)")
print("  Theory: counter-trend signals fail when market is strongly trending")
print("=" * 100)

for w in WINDOWS:
    print(f"\n  --- Window={w} (≈{w*4//24}d) ---")
    for h_thresh in [0.50, 0.55, 0.60, 0.65, 0.70]:
        hf = {sig: (lambda H, ht=h_thresh: H <= ht) for sig in COUNTER_TREND_SIGNALS}
        t, filt = run(exclude={'fund_spike'}, hurst_filter=hf, hurst_window=w)
        report(t, f"skip counter when H>{h_thresh:.2f}", filtered=filt)

print()


# =====================================================================
#  EXPERIMENT 4: HURST REGIME FILTER — SKIP ALL IN RANDOM WALK
# =====================================================================
print("=" * 100)
print("  EXPERIMENT 4: SKIP ALL SIGNALS IN RANDOM WALK (H near 0.5)")
print("  Theory: no edge when market is pure random walk")
print("=" * 100)

for w in WINDOWS:
    print(f"\n  --- Window={w} (≈{w*4//24}d) ---")
    for rw_band in [0.03, 0.05, 0.07, 0.10]:
        def rw_filter(st, direction, H, band=rw_band):
            return abs(H - 0.5) > band  # skip if H too close to 0.5
        t, filt = run(exclude={'fund_spike'}, hurst_filter=rw_filter, hurst_window=w)
        report(t, f"skip when |H-0.5|<{rw_band:.2f}", filtered=filt)

print()


# =====================================================================
#  EXPERIMENT 5: HURST REGIME FILTER — DIRECTION-AWARE
# =====================================================================
print("=" * 100)
print("  EXPERIMENT 5: DIRECTION-AWARE HURST FILTER")
print("  Skip counter-trend longs in strong uptrend (H>0.55)")
print("  Skip counter-trend shorts in strong downtrend (H>0.55)")
print("  Keep trend-following in trending, skip in MR")
print("=" * 100)

for w in [200]:  # use best window from above
    print(f"\n  --- Window={w} ---")

    # Smart filter: leverage regime for signal selection
    def smart_filter_v1(st, direction, H):
        """Skip counter-trend in strong trends, keep in MR.
        Skip trend-following in MR, keep in trends."""
        if st in COUNTER_TREND_SIGNALS:
            return H <= 0.60  # don't fight strong trends
        if st in TREND_FOLLOWING_SIGNALS:
            return H >= 0.45  # need at least weak trend
        return True  # unclassified → always trade

    t, filt = run(exclude={'fund_spike'}, hurst_filter=smart_filter_v1, hurst_window=w)
    report(t, "smart v1: counter≤0.60, trend≥0.45", show_signals=True, filtered=filt)

    def smart_filter_v2(st, direction, H):
        if st in COUNTER_TREND_SIGNALS:
            return H <= 0.55
        if st in TREND_FOLLOWING_SIGNALS:
            return H >= 0.50
        return True

    t, filt = run(exclude={'fund_spike'}, hurst_filter=smart_filter_v2, hurst_window=w)
    report(t, "smart v2: counter≤0.55, trend≥0.50", show_signals=True, filtered=filt)

    def smart_filter_v3(st, direction, H):
        if st in COUNTER_TREND_SIGNALS:
            return H <= 0.50  # aggressive: only in MR or weak
        if st in TREND_FOLLOWING_SIGNALS:
            return H >= 0.55  # only in real trends
        return True

    t, filt = run(exclude={'fund_spike'}, hurst_filter=smart_filter_v3, hurst_window=w)
    report(t, "smart v3: counter≤0.50, trend≥0.55", show_signals=True, filtered=filt)

print()


# =====================================================================
#  EXPERIMENT 6: PER-SIGNAL HURST ANALYSIS
# =====================================================================
print("=" * 100)
print("  EXPERIMENT 6: PER-SIGNAL TYPE — EV BY HURST REGIME")
print("  Which signals improve with Hurst filter?")
print("=" * 100)

w = 200  # use default window
t_all, _ = run(exclude={'fund_spike'}, hurst_window=w)

by_sig = defaultdict(list)
for t in t_all:
    by_sig[t['sig']].append(t)

print(f"\n  {'Signal':>22} | {'ALL':^22} | {'H<0.45 MR':^22} | {'0.45-0.55':^22} | {'H>0.55 trend':^22}")
print("  " + "-" * 120)

for sig in sorted(by_sig, key=lambda s: -len(by_sig[s])):
    st = by_sig[sig]

    groups = {
        'all': st,
        'mr': [t for t in st if t['hurst'] < 0.45],
        'rw': [t for t in st if 0.45 <= t['hurst'] <= 0.55],
        'tr': [t for t in st if t['hurst'] > 0.55],
    }

    parts = []
    for key in ['all', 'mr', 'rw', 'tr']:
        g = groups[key]
        if g:
            gw = sum(1 for x in g if x['pnl'] > 0)
            gev = sum(x['pnl'] for x in g) / len(g)
            parts.append(f"N={len(g):>3} EV={gev:>+5.1f}%")
        else:
            parts.append(f"{'—':^18}")

    ct = "CT" if sig in COUNTER_TREND_SIGNALS else "TF" if sig in TREND_FOLLOWING_SIGNALS else "??"
    print(f"  {sig:>22} [{ct}] | {parts[0]:^20} | {parts[1]:^20} | {parts[2]:^20} | {parts[3]:^20}")

print()


# =====================================================================
#  EXPERIMENT 7: OPTIMAL HURST THRESHOLD PER SIGNAL
# =====================================================================
print("=" * 100)
print("  EXPERIMENT 7: GRID SEARCH — BEST HURST THRESHOLD PER SIGNAL")
print("=" * 100)

w = 200
t_all, _ = run(exclude={'fund_spike'}, hurst_window=w)

by_sig = defaultdict(list)
for t in t_all:
    by_sig[t['sig']].append(t)

for sig in sorted(by_sig, key=lambda s: -len(by_sig[s])):
    st = by_sig[sig]
    n_all = len(st)
    ev_all = sum(t['pnl'] for t in st) / n_all

    best_ev = ev_all
    best_op = "none"
    best_thresh = 0
    best_n = n_all

    for thresh in np.arange(0.35, 0.75, 0.05):
        # Filter: only trade when H < thresh
        below = [t for t in st if t['hurst'] < thresh]
        if len(below) >= 5:
            ev_b = sum(t['pnl'] for t in below) / len(below)
            if ev_b > best_ev + 0.1:  # meaningful improvement
                best_ev = ev_b
                best_op = f"H<{thresh:.2f}"
                best_thresh = thresh
                best_n = len(below)

        # Filter: only trade when H > thresh
        above = [t for t in st if t['hurst'] > thresh]
        if len(above) >= 5:
            ev_a = sum(t['pnl'] for t in above) / len(above)
            if ev_a > best_ev + 0.1:
                best_ev = ev_a
                best_op = f"H>{thresh:.2f}"
                best_thresh = thresh
                best_n = len(above)

    ct = "CT" if sig in COUNTER_TREND_SIGNALS else "TF" if sig in TREND_FOLLOWING_SIGNALS else "??"
    if best_op != "none":
        delta = best_ev - ev_all
        print(f"  {sig:>22} [{ct}] base EV={ev_all:>+5.1f}% N={n_all:>3} → {best_op} EV={best_ev:>+5.1f}% N={best_n:>3} (Δ={delta:>+5.1f}%)")
    else:
        print(f"  {sig:>22} [{ct}] base EV={ev_all:>+5.1f}% N={n_all:>3} → no improvement found")

print()


# =====================================================================
#  EXPERIMENT 8: COMBINED OPTIMAL FILTER
# =====================================================================
print("=" * 100)
print("  EXPERIMENT 8: COMBINE BEST PER-SIGNAL HURST FILTERS")
print("=" * 100)

# Manually collect the best filters from exp 7 output (we'll print both ways)
# First, automate: build optimal filter from exp 7 results

w = 200
t_all, _ = run(exclude={'fund_spike'}, hurst_window=w)
by_sig = defaultdict(list)
for t in t_all:
    by_sig[t['sig']].append(t)

optimal_hurst = {}  # sig → (operator, threshold)

for sig in by_sig:
    st = by_sig[sig]
    n_all = len(st)
    ev_all = sum(t['pnl'] for t in st) / n_all

    best_improvement = 0
    best_config = None

    for thresh in np.arange(0.35, 0.75, 0.05):
        below = [t for t in st if t['hurst'] < thresh]
        if len(below) >= max(5, n_all * 0.25):  # keep at least 25% of trades
            ev_b = sum(t['pnl'] for t in below) / len(below)
            improvement = ev_b - ev_all
            if improvement > best_improvement and improvement > 0.3:
                best_improvement = improvement
                best_config = ('<', thresh, len(below), ev_b)

        above = [t for t in st if t['hurst'] > thresh]
        if len(above) >= max(5, n_all * 0.25):
            ev_a = sum(t['pnl'] for t in above) / len(above)
            improvement = ev_a - ev_all
            if improvement > best_improvement and improvement > 0.3:
                best_improvement = improvement
                best_config = ('>', thresh, len(above), ev_a)

    if best_config:
        op, thresh, n_filt, ev_filt = best_config
        optimal_hurst[sig] = (op, thresh)

print(f"  Signals with beneficial Hurst filter: {len(optimal_hurst)}/{len(by_sig)}")
for sig, (op, th) in sorted(optimal_hurst.items()):
    print(f"    {sig:>22}: H {op} {th:.2f}")

# Build the combined filter
def combined_optimal(st, direction, H):
    if st in optimal_hurst:
        op, thresh = optimal_hurst[st]
        if op == '<':
            return H < thresh
        else:
            return H > thresh
    return True  # no filter for this signal

# Compare
print(f"\n  Results with combined optimal Hurst filter:")
t_base, _ = run(exclude={'fund_spike'}, hurst_window=w)
report(t_base, "baseline (no Hurst filter)")

t_opt, filt_opt = run(exclude={'fund_spike'}, hurst_filter=combined_optimal, hurst_window=w)
report(t_opt, "combined optimal Hurst filter", show_signals=True, filtered=filt_opt)

# Year-by-year stability check
print(f"\n  Year-by-year stability:")
for label, trades in [("baseline", t_base), ("Hurst filtered", t_opt)]:
    print(f"    {label}:")
    for year in ['2023', '2024', '2025', '2026']:
        yt = [t for t in trades if t['date'].startswith(year)]
        if yt:
            yw = sum(1 for t in yt if t['pnl'] > 0)
            yev = sum(t['pnl'] for t in yt) / len(yt)
            print(f"      {year}: N={len(yt):>3} WR={yw/len(yt)*100:>5.0f}% EV={yev:>+6.2f}%")

print()


# =====================================================================
#  EXPERIMENT 9: HURST + EXISTING FILTERS (INTERACTION)
# =====================================================================
print("=" * 100)
print("  EXPERIMENT 9: HURST + FUND_Z FILTER INTERACTION")
print("  Does Hurst add value on top of existing z-score filters?")
print("=" * 100)

w = 200

# Baseline with fund_z < 1 filter (known strong filter from roadmap)
fund_z_filter = {sig: (lambda b: abs(b['fund_z']) < 1.0) for sig in CURRENT if sig != 'fund_spike'}

t_fz, _ = run(exclude={'fund_spike'}, filters=fund_z_filter, hurst_window=w)
report(t_fz, "fund_z<1 only", show_hurst=True)

t_fz_h, filt_h = run(exclude={'fund_spike'}, filters=fund_z_filter,
                       hurst_filter=combined_optimal, hurst_window=w)
report(t_fz_h, "fund_z<1 + Hurst", filtered=filt_h)

print()


# =====================================================================
#  EXPERIMENT 10: ACF HALF-LIFE ADAPTIVE WINDOW
# =====================================================================
print("=" * 100)
print("  EXPERIMENT 10: HURST SENSITIVITY TO WINDOW SIZE")
print("  Check if results are robust across windows or window-dependent")
print("=" * 100)

for w in WINDOWS:
    t, filt = run(exclude={'fund_spike'}, hurst_filter=combined_optimal, hurst_window=w)
    report(t, f"combined optimal @ w={w}", filtered=filt)

print()
print("=" * 100)
print("  DONE — check per-signal Hurst regime analysis (Exp 6) for key insights")
print("=" * 100)
