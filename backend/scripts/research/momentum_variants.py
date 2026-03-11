#!/usr/bin/env python3
"""Test momentum filter variants. Optimized: detect+simulate once, filter 3 ways.

Usage: cd backend && python3 -u scripts/lab/momentum_variants.py
"""
import asyncio, os, sys, time
sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')
if '--4h' not in sys.argv:
    sys.argv.append('--4h')

from db import init_db
from scripts.setup_backtest import (
    load_symbol_data, load_4h_bars, detect_signals_at_bar,
    SYMBOLS, Signal, Bar, Bar4h, ExitResult,
    _compute_stats, _apply_costs, _bar_to_signal_input,
    strategy_adaptive, BARS_PER_DAY, USE_4H_EXIT,
)
from services.signal_conditions import (
    detect_signals, compute_confluence,
    CONFLUENCE_SIGNAL, ALT_MIN_CONFLUENCE,
)

TOP_OI = {'BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','BNBUSDT','DOGEUSDT','TRXUSDT','UNIUSDT','SUIUSDT','ADAUSDT'}
COOLDOWN_DAYS = 1
CLUSTER_GAP_DAYS = 2
COUNTER_TREND_SIGNALS = {"distribution", "overextension"}
P = lambda *a, **kw: (print(*a, **kw, flush=True))


def detect_all_raw(bars, symbol, days=1100):
    """Detect signals WITHOUT momentum filter. Tag each with trend info."""
    total = len(bars)
    warmup_end = max(30, total - days)
    raw = []
    cooldowns = {}

    for i in range(warmup_end, total):
        if bars[i].close <= 0:
            continue
        inp = _bar_to_signal_input(bars, i)
        triggered = detect_signals(inp)

        for sig_type, direction in triggered:
            confluence, factors = compute_confluence(inp, direction)
            if confluence < CONFLUENCE_SIGNAL:
                continue
            if symbol not in TOP_OI and confluence < ALT_MIN_CONFLUENCE:
                continue

            cd_key = f"{sig_type}:{symbol}"
            if cd_key in cooldowns and (i - cooldowns[cd_key]) < COOLDOWN_DAYS:
                continue
            cooldowns[cd_key] = i

            sig = Signal(
                bar_idx=i, signal_type=sig_type, direction=direction,
                entry_price=bars[i].close, confluence=confluence,
                factors=factors[:5],
                zscores={"oi_z": bars[i].oi_z, "fund_z": bars[i].fund_z,
                         "liq_z": bars[i].liq_z, "vol_z": bars[i].vol_z},
            )
            # Tag with trend for filtering later
            sig._trend = bars[i].trend
            raw.append(sig)

    # Cluster
    if len(raw) <= 1:
        return raw
    raw.sort(key=lambda s: s.bar_idx)
    clustered = []
    for sig in raw:
        merged = False
        for existing in clustered:
            if existing.direction != sig.direction:
                continue
            if abs(sig.bar_idx - existing.bar_idx) <= CLUSTER_GAP_DAYS:
                if sig.confluence > existing.confluence:
                    clustered[clustered.index(existing)] = sig
                merged = True
                break
        if not merged:
            clustered.append(sig)
    return clustered


def passes_filter(sig, mode):
    """Check if signal passes momentum filter for given mode."""
    trend = sig._trend
    direction = sig.direction
    sig_type = sig.signal_type

    if mode == "current":
        if direction == "long" and trend == "down":
            return False
        if direction == "short" and trend == "up":
            return False
    elif mode == "exempt":
        if sig_type not in COUNTER_TREND_SIGNALS:
            if direction == "long" and trend == "down":
                return False
            if direction == "short" and trend == "up":
                return False
    elif mode == "no_mom":
        pass
    return True


async def main():
    await init_db()
    P("  Momentum Filter Variants (4h exit, detect once → filter 3 ways)")
    P()

    t0 = time.time()
    all_daily = {}
    all_4h = {}
    for sym in SYMBOLS:
        daily = await load_symbol_data(sym)
        if not daily:
            continue
        all_daily[sym] = daily
        if USE_4H_EXIT:
            bars_4h, df, dl = await load_4h_bars(sym, daily)
            if bars_4h:
                all_4h[sym] = (bars_4h, df, dl)
    P(f"  Data loaded in {time.time() - t0:.1f}s")

    # Build 4h signal cache once
    all_sig_cache = {}
    for sym, daily in all_daily.items():
        sigs_by_date = {}
        for i in range(len(daily)):
            t = detect_signals_at_bar(daily, i)
            if t:
                sigs_by_date[daily[i].date] = t
        if sym in all_4h:
            bars_4h, df, dl = all_4h[sym]
            cache_4h = {}
            for i, b4 in enumerate(bars_4h):
                s = sigs_by_date.get(b4.date)
                if s:
                    cache_4h[i] = s
            all_sig_cache[sym] = cache_4h

    # Detect ALL signals without momentum filter + simulate exits ONCE
    P("  Detecting signals + simulating exits...")
    t1 = time.time()
    all_results = []  # list of (ExitResult, Signal) with trend tagged

    for si, (sym, daily) in enumerate(all_daily.items()):
        signals = detect_all_raw(daily, sym, days=1100)
        if not signals:
            continue

        if USE_4H_EXIT and sym in all_4h:
            bars_4h, df, dl = all_4h[sym]
            cache_4h = all_sig_cache.get(sym, {})

            for sig in signals:
                sig_date = daily[sig.bar_idx].date
                idx_4h = dl.get(sig_date, -1)
                if idx_4h < 0:
                    continue
                sig.entry_price = bars_4h[idx_4h].close
                orig = sig.bar_idx
                sig.bar_idx = idx_4h
                r = strategy_adaptive(bars_4h, sig, cache_4h)
                if r:
                    _apply_costs(r, bars_4h, idx_4h, sig.direction)
                    all_results.append((r, sig))
                sig.bar_idx = orig

        if (si + 1) % 10 == 0:
            P(f"    [{si+1}/{len(all_daily)}]...")

    P(f"  Done: {len(all_results)} results in {time.time() - t1:.1f}s")
    P()

    # Now filter results by mode and compute stats
    modes = [
        ("A: Current (strict)", "current"),
        ("B: Exempt counter-trend", "exempt"),
        ("C: No momentum filter", "no_mom"),
    ]

    P(f"  {'Mode':<30} {'N':>5} {'WR':>6} {'GrossEV':>9} {'NetEV':>9} {'NetPF':>7}")
    P("  " + "-" * 70)

    mode_data = {}
    for label, mode in modes:
        filtered = [(r, s) for r, s in all_results if passes_filter(s, mode)]
        results = [r for r, _ in filtered]
        stats = _compute_stats(results) if results else {}

        n = stats.get("trades", 0)
        wr = stats.get("wr", 0)
        ev = stats.get("ev", 0)
        net_ev = stats.get("net_ev", 0)
        net_pf = stats.get("net_pf", 0)

        P(f"  {label:<30} {n:>5} {wr:>5.1f}% {ev:>+8.2f}% {net_ev:>+8.2f}% {net_pf:>6.2f}x")

        # Collect per-signal stats
        by_type = {}
        for r, s in filtered:
            if s.signal_type not in by_type:
                by_type[s.signal_type] = []
            by_type[s.signal_type].append(r)
        mode_data[mode] = by_type

    # Per-signal breakdown for each mode
    for label, mode in modes:
        by_type = mode_data[mode]
        P()
        P(f"  === {label} — per signal ===")
        P(f"  {'Signal':<25} {'N':>5} {'WR':>6} {'NetEV':>9}")
        P("  " + "-" * 50)
        for sig in sorted(by_type, key=lambda s: len(by_type[s]), reverse=True):
            rs = by_type[sig]
            st = _compute_stats(rs)
            P(f"  {sig:<25} {st['trades']:>5} {st['wr']:>5.1f}% {st.get('net_ev',0):>+8.2f}%")

    # Delta: what exempt adds vs current
    P()
    P("  === Delta: B (exempt) vs A (current) ===")
    current_sigs = {s.signal_type for r, s in all_results if passes_filter(s, "current")}
    for sig_type in COUNTER_TREND_SIGNALS:
        exempt_rs = mode_data["exempt"].get(sig_type, [])
        current_rs = mode_data["current"].get(sig_type, [])
        added = len(exempt_rs) - len(current_rs)
        if added > 0:
            st = _compute_stats(exempt_rs)
            P(f"  {sig_type}: +{added} new signals, total N={len(exempt_rs)}, WR {st['wr']:.1f}%, NetEV {st.get('net_ev',0):+.2f}%")
        elif len(exempt_rs) == 0:
            P(f"  {sig_type}: 0 signals (all filtered by confluence/alt)")
    P()


if __name__ == "__main__":
    asyncio.run(main())
