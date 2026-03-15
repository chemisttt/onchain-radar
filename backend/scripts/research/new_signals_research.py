#!/usr/bin/env python3
"""Research new signal types — detect on historical daily bars, simulate exits on 4h bars.

Tests 6 candidate signal types:
1. OI Flush + Price Hold — sharp OI drop but price stable → reversal
2. Volume Climax Reversal — extreme volume + price reversal candle
3. Funding Mean Reversion — sustained extreme funding (3+ bars) → reversion trade
4. Cross-Asset Divergence — alt lagging/leading BTC → catch-up
5. Volatility Squeeze — ATR at N-bar low → breakout
6. OI Acceleration Divergence — OI accelerating opposite to price

For each: runs all 6 exit strategies, computes WR/EV/PnL, compares to baseline.

Usage:
    cd backend && python3 scripts/research/new_signals_research.py
"""

import asyncio, sys, os, time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')
sys.argv = ['test', '--4h']

from db import init_db
from scripts.setup_backtest import (
    load_symbol_data, load_4h_bars, detect_all_signals, detect_signals_at_bar,
    Bar, Bar4h, Signal, ExitResult,
    strategy_fixed, strategy_zscore, strategy_counter, strategy_trailing,
    strategy_hybrid, strategy_trail_counter,
    _apply_costs, _walk_pnl,
    SYMBOLS, BARS_PER_DAY, TOP_OI_SYMBOLS, ALT_MIN_CONFLUENCE,
    COUNTER_SIGNALS, ADAPTIVE_EXIT, SIGNAL_PRIMARY_Z, ZSCORE_TP_THRESH,
    HARD_STOP_PCT, COOLDOWN_DAYS, CLUSTER_GAP_DAYS,
)
from scripts.validate_strategy import HL_SYMBOLS

# ─── Config ────────────────────────────────────────────────────────────────
_SYMBOLS = HL_SYMBOLS
TRAIN_TEST_SPLIT = "2025-01-01"


# ─── New Signal Detection ──────────────────────────────────────────────────

def detect_new_signals_at_bar(daily: list[Bar], i: int, sym: str,
                               btc_daily: list[Bar] = None, btc_idx_map: dict = None):
    """Detect new candidate signal types at bar i. Returns list of (type, direction)."""
    if i < 30:  # need lookback
        return []

    b = daily[i]
    results = []

    # ── 1. OI Flush + Price Hold ──
    # Sharp OI drop (>5%) but price holds (abs < 2%) → longs survived liquidation cascade
    if b.oi_chg < -5.0 and abs(b.price_chg) < 2.0:
        # Direction based on recent trend
        if b.trend != "down" and b.fund_z < 1.0:
            results.append(("oi_flush_hold", "long"))
    if b.oi_chg < -5.0 and abs(b.price_chg) < 2.0:
        if b.trend != "up" and b.fund_z > -1.0:
            results.append(("oi_flush_hold", "short"))

    # ── 2. Volume Climax Reversal ──
    # Extreme volume (vol_z > 2) + price reversal (wick > body)
    if b.vol_z > 2.0 and b.atr > 0:
        body = abs(b.close - daily[i-1].close)
        wick_up = b.high - max(b.close, daily[i-1].close)
        wick_dn = min(b.close, daily[i-1].close) - b.low
        range_full = b.high - b.low
        if range_full > 0:
            # Bearish reversal: big upper wick + price was up
            if wick_up > body and b.price_chg > 1.0 and b.trend != "up":
                results.append(("vol_climax", "short"))
            # Bullish reversal: big lower wick + price was down
            if wick_dn > body and b.price_chg < -1.0 and b.trend != "down":
                results.append(("vol_climax", "long"))

    # ── 3. Funding Mean Reversion ──
    # Sustained extreme funding for 3+ consecutive bars
    if i >= 3:
        fund_zs = [daily[i-j].fund_z for j in range(3)]
        all_high = all(z > 1.0 for z in fund_zs)
        all_low = all(z < -1.0 for z in fund_zs)
        if all_high and b.fund_z > 1.5 and b.trend != "up":
            results.append(("fund_mean_revert", "short"))
        if all_low and b.fund_z < -1.5 and b.trend != "down":
            results.append(("fund_mean_revert", "long"))

    # ── 4. Cross-Asset Divergence (vs BTC) ──
    if btc_daily and btc_idx_map and sym != "BTCUSDT" and i >= 5:
        btc_i = btc_idx_map.get(b.date)
        if btc_i is not None and btc_i >= 5:
            btc_chg_5d = btc_daily[btc_i].price_chg_5d if hasattr(btc_daily[btc_i], 'price_chg_5d') else 0
            alt_chg_5d = b.price_chg_5d if hasattr(b, 'price_chg_5d') else 0
            spread = alt_chg_5d - btc_chg_5d
            # Alt lagged BTC significantly → catch-up long
            if spread < -8.0 and b.trend != "down":
                results.append(("cross_diverge", "long"))
            # Alt outpaced BTC significantly → mean reversion short
            if spread > 8.0 and b.trend != "up":
                results.append(("cross_diverge", "short"))

    # ── 5. Volatility Squeeze ──
    # ATR at 20-bar low → breakout imminent
    if i >= 20 and b.atr > 0:
        recent_atrs = [daily[i-j].atr for j in range(20) if daily[i-j].atr > 0]
        if len(recent_atrs) >= 15:
            atr_pctile = sum(1 for a in recent_atrs if a <= b.atr) / len(recent_atrs)
            if atr_pctile <= 0.1:  # ATR in bottom 10% of last 20 bars
                # Direction from trend
                if b.trend == "up" or b.price_vs_sma > 2:
                    results.append(("vol_squeeze", "long"))
                elif b.trend == "down" or b.price_vs_sma < -2:
                    results.append(("vol_squeeze", "short"))

    # ── 6. OI Acceleration Divergence ──
    # OI accelerating (z_accel > 1) while price dropping → shorts building, squeeze coming
    if hasattr(b, 'z_accel'):
        if b.z_accel > 1.0 and b.price_chg_3d < -3.0 and b.trend != "down":
            results.append(("oi_accel_div", "long"))
        if b.z_accel < -1.0 and b.price_chg_3d > 3.0 and b.trend != "up":
            results.append(("oi_accel_div", "short"))

    return results


def compute_new_confluence(b: Bar, direction: str) -> tuple[int, list[str]]:
    """Simplified confluence for new signals — same logic as existing."""
    score = 3  # base (all new signals have inherent conditions met)
    factors = []

    if abs(b.oi_z) > 3.0:
        score += 2; factors.append("OI_z extreme")
    elif abs(b.oi_z) > 2.0:
        score += 1; factors.append("OI_z elevated")
    if abs(b.fund_z) > 3.0:
        score += 2; factors.append("Fund_z extreme")
    elif abs(b.fund_z) > 2.0:
        score += 1; factors.append("Fund_z elevated")
    if abs(b.liq_z) > 2.0:
        score += 1; factors.append("Liq_z elevated")
    if abs(b.vol_z) > 2.0:
        score += 1; factors.append("Vol_z elevated")
    if abs(b.price_chg_5d) > 5:
        score += 1; factors.append("Momentum")
    if hasattr(b, 'z_accel') and abs(b.z_accel) > 1.0:
        score += 1; factors.append("Z accel")

    # Trend alignment
    if (direction == "long" and b.trend == "up") or (direction == "short" and b.trend == "down"):
        score += 1; factors.append("Trend aligned")
    elif (direction == "long" and b.trend == "down") or (direction == "short" and b.trend == "up"):
        score -= 1; factors.append("Trend counter")

    # Funding confirms
    if direction == "short" and b.funding_rate > 0:
        score += 1; factors.append("Fund confirms short")
    elif direction == "long" and b.funding_rate < 0:
        score += 1; factors.append("Fund confirms long")

    return score, factors


# ─── Simulation Engine ──────────────────────────────────────────────────

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


@dataclass
class TradeResult:
    sym: str
    sig_type: str
    direction: str
    date: str
    confluence: int
    exit_strategy: str
    pnl_net: float
    bars_held: int
    exit_reason: str
    mfe: float
    mae: float


async def main():
    t0 = time.time()
    await init_db()

    # ── Load all symbol data ──
    print("Loading data...")
    all_data = {}
    btc_daily = None
    btc_idx_map = {}

    for sym in _SYMBOLS:
        daily = await load_symbol_data(sym)
        if not daily:
            continue
        bars_4h, df, dl = await load_4h_bars(sym, daily)
        if not bars_4h:
            continue

        # Build counter-signal cache from EXISTING signals (for counter exits)
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

        if sym == "BTCUSDT":
            btc_daily = daily
            btc_idx_map = {b.date: i for i, b in enumerate(daily)}

    print(f"  Loaded {len(all_data)} symbols in {time.time()-t0:.1f}s")

    # ── Detect new signals ──
    print("\nDetecting new signals...")
    all_signals_by_type = defaultdict(list)  # type → list of (sym, signal, bars_4h, cache_4h)
    total = 0

    for sym, data in all_data.items():
        daily = data['daily']
        bars_4h = data['bars_4h']
        dl = data['date_last']
        c4h = data['cache_4h']

        cooldowns = {}
        for i in range(30, len(daily)):
            new_sigs = detect_new_signals_at_bar(daily, i, sym, btc_daily, btc_idx_map)
            if not new_sigs:
                continue

            for sig_type, direction in new_sigs:
                # Cooldown
                cd_key = f"{sig_type}:{sym}"
                if cd_key in cooldowns and (i - cooldowns[cd_key]) < COOLDOWN_DAYS:
                    continue

                # Confluence
                confluence, factors = compute_new_confluence(daily[i], direction)
                if confluence < 4:  # same SIGNAL tier threshold
                    continue
                if sym not in TOP_OI_SYMBOLS and confluence < ALT_MIN_CONFLUENCE:
                    continue

                cooldowns[cd_key] = i

                # Map to 4h bar
                idx_4h = dl.get(daily[i].date, -1)
                if idx_4h < 0:
                    continue

                sig = Signal(
                    bar_idx=i,
                    signal_type=sig_type,
                    direction=direction,
                    entry_price=daily[i].close,
                    confluence=confluence,
                    factors=factors[:5],
                    zscores={"oi_z": daily[i].oi_z, "fund_z": daily[i].fund_z,
                             "liq_z": daily[i].liq_z, "vol_z": daily[i].vol_z},
                    bar_idx_4h=idx_4h,
                )
                sig.entry_price = bars_4h[idx_4h].close  # 4h close at end of signal day

                all_signals_by_type[sig_type].append((sym, sig, bars_4h, c4h, daily[i].date))
                total += 1

    print(f"  Found {total} new signals across {len(all_signals_by_type)} types")
    for st, sigs in sorted(all_signals_by_type.items(), key=lambda x: -len(x[1])):
        n_long = sum(1 for _, s, _, _, _ in sigs if s.direction == "long")
        n_short = len(sigs) - n_long
        print(f"    {st}: {len(sigs)} ({n_long}L / {n_short}S)")

    # ── Simulate each signal type with all exit strategies ──
    print("\n" + "="*80)
    print("RESULTS PER SIGNAL TYPE × EXIT STRATEGY")
    print("="*80)

    summary = {}  # (sig_type, exit_strat) → list of net_pnl

    for sig_type, sig_list in sorted(all_signals_by_type.items(), key=lambda x: -len(x[1])):
        print(f"\n{'─'*60}")
        print(f"  {sig_type.upper()} (N={len(sig_list)})")
        print(f"{'─'*60}")

        # Direction breakdown
        n_long = sum(1 for _, s, _, _, _ in sig_list if s.direction == "long")
        n_short = len(sig_list) - n_long
        dates = [d for _, _, _, _, d in sig_list]
        print(f"  {n_long}L / {n_short}S  |  {min(dates)} → {max(dates)}")

        # Symbol distribution
        sym_counts = defaultdict(int)
        for sym, _, _, _, _ in sig_list:
            sym_counts[sym] += 1
        top_syms = sorted(sym_counts.items(), key=lambda x: -x[1])[:5]
        print(f"  Top symbols: {', '.join(f'{s}({n})' for s,n in top_syms)}")

        best_ev = -999
        best_strat = ""

        for strat_name, strat_fn in EXIT_STRATEGIES.items():
            pnls = []
            wins = 0
            reasons = defaultdict(int)

            for sym, sig, bars_4h, c4h, date in sig_list:
                # Set bar_idx to 4h index for exit simulation
                sig_copy = Signal(
                    bar_idx=sig.bar_idx_4h,
                    signal_type=sig.signal_type,
                    direction=sig.direction,
                    entry_price=sig.entry_price,
                    confluence=sig.confluence,
                    factors=sig.factors,
                    zscores=sig.zscores,
                )

                try:
                    result = strat_fn(bars_4h, sig_copy, c4h)
                    _apply_costs(result, bars_4h, sig_copy.bar_idx, sig_copy.direction)
                    pnls.append(result.net_pnl_pct)
                    if result.net_pnl_pct > 0:
                        wins += 1
                    reasons[result.exit_reason] += 1
                except Exception:
                    continue

            if not pnls:
                continue

            n = len(pnls)
            wr = wins / n * 100
            ev = np.mean(pnls)
            pnl_total = sum(pnls)
            median = np.median(pnls)

            summary_key = (sig_type, strat_name)
            summary[summary_key] = pnls

            marker = ""
            if ev > best_ev:
                best_ev = ev
                best_strat = strat_name

            reasons_str = ", ".join(f"{r}:{c}" for r,c in sorted(reasons.items(), key=lambda x:-x[1]))
            print(f"  {strat_name:15s} | N={n:3d} WR={wr:5.1f}% EV={ev:+6.2f}% PnL={pnl_total:+7.1f}% med={median:+5.2f}% | {reasons_str}")

        if best_strat:
            print(f"  >>> BEST: {best_strat} (EV={best_ev:+.2f}%)")

    # ── Comparison with existing signals ──
    print("\n" + "="*80)
    print("COMPARISON: NEW vs EXISTING SIGNALS")
    print("="*80)

    # Run existing signals through adaptive exit for baseline
    existing_pnls = []
    for sym, data in all_data.items():
        daily = data['daily']
        bars_4h = data['bars_4h']
        dl = data['date_last']
        c4h = data['cache_4h']

        signals = detect_all_signals(daily, sym, days=9999)
        for sig in signals:
            idx_4h = dl.get(daily[sig.bar_idx].date, -1)
            if idx_4h < 0:
                continue
            sig.bar_idx = idx_4h
            sig.entry_price = bars_4h[idx_4h].close

            strat = ADAPTIVE_EXIT.get(sig.signal_type, "trail_atr")
            strat_fn = EXIT_STRATEGIES.get(strat, EXIT_STRATEGIES["trail_atr"])
            try:
                result = strat_fn(bars_4h, sig, c4h)
                _apply_costs(result, bars_4h, sig.bar_idx, sig.direction)
                existing_pnls.append(result.net_pnl_pct)
            except Exception:
                continue

    existing_n = len(existing_pnls)
    existing_wr = sum(1 for p in existing_pnls if p > 0) / existing_n * 100 if existing_n else 0
    existing_ev = np.mean(existing_pnls) if existing_pnls else 0
    existing_pnl = sum(existing_pnls)

    print(f"\n  EXISTING (adaptive): N={existing_n}, WR={existing_wr:.1f}%, EV={existing_ev:+.2f}%, PnL={existing_pnl:+.1f}%")

    # Show best new signals
    print(f"\n  NEW SIGNALS (best exit per type):")
    for sig_type in sorted(all_signals_by_type.keys()):
        best_ev = -999
        best_key = None
        for (st, strat), pnls in summary.items():
            if st == sig_type:
                ev = np.mean(pnls)
                if ev > best_ev:
                    best_ev = ev
                    best_key = (st, strat)
        if best_key:
            pnls = summary[best_key]
            n = len(pnls)
            wr = sum(1 for p in pnls if p > 0) / n * 100
            pnl_t = sum(pnls)
            print(f"    {best_key[0]:20s} ({best_key[1]:15s}): N={n:3d} WR={wr:5.1f}% EV={best_ev:+6.2f}% PnL={pnl_t:+7.1f}%")

    # ── What happens if we ADD new signals to existing ──
    print(f"\n  COMBINED SYSTEM (existing + best new):")
    for sig_type in sorted(all_signals_by_type.keys()):
        best_ev = -999
        best_key = None
        for (st, strat), pnls in summary.items():
            if st == sig_type:
                ev = np.mean(pnls)
                if ev > best_ev:
                    best_ev = ev
                    best_key = (st, strat)
        if best_key and best_ev > 0:
            new_pnls = summary[best_key]
            combined = existing_pnls + new_pnls
            comb_n = len(combined)
            comb_wr = sum(1 for p in combined if p > 0) / comb_n * 100
            comb_ev = np.mean(combined)
            comb_pnl = sum(combined)
            delta_ev = comb_ev - existing_ev
            delta_pnl = comb_pnl - existing_pnl
            print(f"    + {best_key[0]:20s}: N={comb_n:4d} WR={comb_wr:5.1f}% EV={comb_ev:+.2f}% (ΔEV={delta_ev:+.2f}%) PnL={comb_pnl:+.0f}% (Δ{delta_pnl:+.0f}%)")

    # ── Train/Test split analysis for promising signals ──
    print(f"\n{'='*80}")
    print("TRAIN/TEST SPLIT ANALYSIS (split: {})".format(TRAIN_TEST_SPLIT))
    print("="*80)

    for sig_type, sig_list in sorted(all_signals_by_type.items(), key=lambda x: -len(x[1])):
        # Find best strategy for this type
        best_ev = -999
        best_strat = None
        for (st, strat), pnls in summary.items():
            if st == sig_type and np.mean(pnls) > best_ev:
                best_ev = np.mean(pnls)
                best_strat = strat
        if best_strat is None or best_ev <= 0:
            continue

        strat_fn = EXIT_STRATEGIES[best_strat]
        train_pnls, test_pnls = [], []

        for sym, sig, bars_4h, c4h, date in sig_list:
            sig_copy = Signal(
                bar_idx=sig.bar_idx_4h,
                signal_type=sig.signal_type,
                direction=sig.direction,
                entry_price=sig.entry_price,
                confluence=sig.confluence,
                factors=sig.factors,
                zscores=sig.zscores,
            )
            try:
                result = strat_fn(bars_4h, sig_copy, c4h)
                _apply_costs(result, bars_4h, sig_copy.bar_idx, sig_copy.direction)
                if date < TRAIN_TEST_SPLIT:
                    train_pnls.append(result.net_pnl_pct)
                else:
                    test_pnls.append(result.net_pnl_pct)
            except Exception:
                continue

        if train_pnls and test_pnls:
            tr_n = len(train_pnls)
            tr_wr = sum(1 for p in train_pnls if p > 0) / tr_n * 100
            tr_ev = np.mean(train_pnls)
            te_n = len(test_pnls)
            te_wr = sum(1 for p in test_pnls if p > 0) / te_n * 100
            te_ev = np.mean(test_pnls)
            verdict = "✓ HOLDS" if te_ev > 0 else "✗ FAILS"
            print(f"  {sig_type:20s} ({best_strat})")
            print(f"    TRAIN: N={tr_n:3d} WR={tr_wr:5.1f}% EV={tr_ev:+6.2f}%")
            print(f"    TEST:  N={te_n:3d} WR={te_wr:5.1f}% EV={te_ev:+6.2f}%  {verdict}")

    # ── Per-direction analysis ──
    print(f"\n{'='*80}")
    print("PER-DIRECTION ANALYSIS")
    print("="*80)

    for sig_type, sig_list in sorted(all_signals_by_type.items(), key=lambda x: -len(x[1])):
        best_ev = -999
        best_strat = None
        for (st, strat), pnls in summary.items():
            if st == sig_type and np.mean(pnls) > best_ev:
                best_ev = np.mean(pnls)
                best_strat = strat
        if best_strat is None or best_ev <= 0:
            continue

        strat_fn = EXIT_STRATEGIES[best_strat]
        long_pnls, short_pnls = [], []

        for sym, sig, bars_4h, c4h, date in sig_list:
            sig_copy = Signal(
                bar_idx=sig.bar_idx_4h,
                signal_type=sig.signal_type,
                direction=sig.direction,
                entry_price=sig.entry_price,
                confluence=sig.confluence,
                factors=sig.factors,
                zscores=sig.zscores,
            )
            try:
                result = strat_fn(bars_4h, sig_copy, c4h)
                _apply_costs(result, bars_4h, sig_copy.bar_idx, sig_copy.direction)
                if sig.direction == "long":
                    long_pnls.append(result.net_pnl_pct)
                else:
                    short_pnls.append(result.net_pnl_pct)
            except Exception:
                continue

        print(f"  {sig_type:20s} ({best_strat})")
        if long_pnls:
            n = len(long_pnls); wr = sum(1 for p in long_pnls if p > 0)/n*100; ev = np.mean(long_pnls)
            print(f"    LONG:  N={n:3d} WR={wr:5.1f}% EV={ev:+6.2f}%")
        if short_pnls:
            n = len(short_pnls); wr = sum(1 for p in short_pnls if p > 0)/n*100; ev = np.mean(short_pnls)
            print(f"    SHORT: N={n:3d} WR={wr:5.1f}% EV={ev:+6.2f}%")

    # ── Threshold sensitivity for promising signals ──
    print(f"\n{'='*80}")
    print("THRESHOLD SENSITIVITY SWEEP")
    print("="*80)

    # OI Flush: sweep oi_chg threshold
    if "oi_flush_hold" in all_signals_by_type:
        print(f"\n  oi_flush_hold — OI change threshold sweep:")
        for oi_thresh in [-3, -4, -5, -6, -7, -8, -10]:
            count = 0
            pnls = []
            for sym, data in all_data.items():
                daily = data['daily']
                bars_4h = data['bars_4h']
                dl = data['date_last']
                c4h = data['cache_4h']
                cooldowns = {}
                for i in range(30, len(daily)):
                    b = daily[i]
                    if b.oi_chg < oi_thresh and abs(b.price_chg) < 2.0:
                        direction = "long" if b.trend != "down" and b.fund_z < 1.0 else \
                                   ("short" if b.trend != "up" and b.fund_z > -1.0 else None)
                        if not direction:
                            continue
                        cd_key = f"oi_flush:{sym}"
                        if cd_key in cooldowns and (i - cooldowns[cd_key]) < 1:
                            continue
                        conf, _ = compute_new_confluence(b, direction)
                        if conf < 4:
                            continue
                        if sym not in TOP_OI_SYMBOLS and conf < ALT_MIN_CONFLUENCE:
                            continue
                        cooldowns[cd_key] = i
                        idx_4h = dl.get(b.date, -1)
                        if idx_4h < 0:
                            continue
                        sig = Signal(bar_idx=idx_4h, signal_type="oi_flush_hold",
                                     direction=direction, entry_price=bars_4h[idx_4h].close,
                                     confluence=conf, factors=[], zscores={
                                         "oi_z": b.oi_z, "fund_z": b.fund_z,
                                         "liq_z": b.liq_z, "vol_z": b.vol_z})
                        best_strat_fn = EXIT_STRATEGIES.get(
                            [s for (st,s),_ in summary.items() if st == "oi_flush_hold"][0]
                            if any(st == "oi_flush_hold" for st,_ in summary.keys())
                            else "counter_sig",
                            EXIT_STRATEGIES["counter_sig"]
                        )
                        try:
                            result = best_strat_fn(bars_4h, sig, c4h)
                            _apply_costs(result, bars_4h, sig.bar_idx, direction)
                            pnls.append(result.net_pnl_pct)
                        except Exception:
                            pass
            if pnls:
                n = len(pnls)
                wr = sum(1 for p in pnls if p > 0)/n*100
                ev = np.mean(pnls)
                print(f"    oi_chg < {oi_thresh:3d}%: N={n:3d} WR={wr:5.1f}% EV={ev:+6.2f}% PnL={sum(pnls):+.1f}%")

    # Cross-diverge: sweep spread threshold
    if "cross_diverge" in all_signals_by_type:
        print(f"\n  cross_diverge — spread threshold sweep:")
        for spread_thresh in [5, 6, 7, 8, 10, 12, 15]:
            pnls = []
            for sym, data in all_data.items():
                if sym == "BTCUSDT":
                    continue
                daily = data['daily']
                bars_4h = data['bars_4h']
                dl = data['date_last']
                c4h = data['cache_4h']
                cooldowns = {}
                for i in range(30, len(daily)):
                    b = daily[i]
                    if not btc_daily or i >= len(daily):
                        continue
                    btc_i = btc_idx_map.get(b.date)
                    if btc_i is None or btc_i < 5:
                        continue
                    btc_chg = btc_daily[btc_i].price_chg_5d if hasattr(btc_daily[btc_i], 'price_chg_5d') else 0
                    alt_chg = b.price_chg_5d if hasattr(b, 'price_chg_5d') else 0
                    spread = alt_chg - btc_chg
                    direction = None
                    if spread < -spread_thresh and b.trend != "down":
                        direction = "long"
                    elif spread > spread_thresh and b.trend != "up":
                        direction = "short"
                    if not direction:
                        continue
                    cd_key = f"xdiv:{sym}"
                    if cd_key in cooldowns and (i - cooldowns[cd_key]) < 1:
                        continue
                    conf, _ = compute_new_confluence(b, direction)
                    if conf < 4:
                        continue
                    if sym not in TOP_OI_SYMBOLS and conf < ALT_MIN_CONFLUENCE:
                        continue
                    cooldowns[cd_key] = i
                    idx_4h = dl.get(b.date, -1)
                    if idx_4h < 0:
                        continue
                    sig = Signal(bar_idx=idx_4h, signal_type="cross_diverge",
                                 direction=direction, entry_price=bars_4h[idx_4h].close,
                                 confluence=conf, factors=[], zscores={
                                     "oi_z": b.oi_z, "fund_z": b.fund_z,
                                     "liq_z": b.liq_z, "vol_z": b.vol_z})
                    try:
                        result = EXIT_STRATEGIES["counter_sig"](bars_4h, sig, c4h)
                        _apply_costs(result, bars_4h, sig.bar_idx, direction)
                        pnls.append(result.net_pnl_pct)
                    except Exception:
                        pass
            if pnls:
                n = len(pnls)
                wr = sum(1 for p in pnls if p > 0)/n*100
                ev = np.mean(pnls)
                print(f"    spread > ±{spread_thresh:2d}%: N={n:3d} WR={wr:5.1f}% EV={ev:+6.2f}% PnL={sum(pnls):+.1f}%")

    # Funding mean revert: sweep fund_z threshold
    if "fund_mean_revert" in all_signals_by_type:
        print(f"\n  fund_mean_revert — fund_z threshold sweep:")
        for fz_thresh in [1.0, 1.2, 1.5, 1.8, 2.0, 2.5]:
            pnls = []
            for sym, data in all_data.items():
                daily = data['daily']
                bars_4h = data['bars_4h']
                dl = data['date_last']
                c4h = data['cache_4h']
                cooldowns = {}
                for i in range(30, len(daily)):
                    b = daily[i]
                    if i < 3:
                        continue
                    fund_zs = [daily[i-j].fund_z for j in range(3)]
                    direction = None
                    if all(z > fz_thresh * 0.67 for z in fund_zs) and b.fund_z > fz_thresh and b.trend != "up":
                        direction = "short"
                    elif all(z < -fz_thresh * 0.67 for z in fund_zs) and b.fund_z < -fz_thresh and b.trend != "down":
                        direction = "long"
                    if not direction:
                        continue
                    cd_key = f"fmr:{sym}"
                    if cd_key in cooldowns and (i - cooldowns[cd_key]) < 1:
                        continue
                    conf, _ = compute_new_confluence(b, direction)
                    if conf < 4:
                        continue
                    if sym not in TOP_OI_SYMBOLS and conf < ALT_MIN_CONFLUENCE:
                        continue
                    cooldowns[cd_key] = i
                    idx_4h = dl.get(b.date, -1)
                    if idx_4h < 0:
                        continue
                    sig = Signal(bar_idx=idx_4h, signal_type="fund_mean_revert",
                                 direction=direction, entry_price=bars_4h[idx_4h].close,
                                 confluence=conf, factors=[], zscores={
                                     "oi_z": b.oi_z, "fund_z": b.fund_z,
                                     "liq_z": b.liq_z, "vol_z": b.vol_z})
                    try:
                        result = EXIT_STRATEGIES["zscore_mr"](bars_4h, sig, c4h)
                        _apply_costs(result, bars_4h, sig.bar_idx, direction)
                        pnls.append(result.net_pnl_pct)
                    except Exception:
                        pass
            if pnls:
                n = len(pnls)
                wr = sum(1 for p in pnls if p > 0)/n*100
                ev = np.mean(pnls)
                print(f"    fund_z > ±{fz_thresh:.1f}: N={n:3d} WR={wr:5.1f}% EV={ev:+6.2f}% PnL={sum(pnls):+.1f}%")

    print(f"\n{'='*80}")
    print(f"Total time: {time.time()-t0:.1f}s")
    print("="*80)


asyncio.run(main())
