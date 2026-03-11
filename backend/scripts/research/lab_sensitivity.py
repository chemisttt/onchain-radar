#!/usr/bin/env python3
"""Threshold sensitivity analysis — test ±20% on each key threshold.

Stable = ±10% gives similar EV. Fragile = EV changes sign.

Usage:
  cd backend && python3 /tmp/lab_sensitivity.py
  cd backend && python3 /tmp/lab_sensitivity.py --4h
"""

import asyncio
import os
import sys
import time
import pickle

sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')

# Must set --4h before importing setup_backtest
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
    SignalInput, compute_confluence, detect_signals,
    CONFLUENCE_SIGNAL, ALT_MIN_CONFLUENCE,
)
import services.signal_conditions as sc

# Key thresholds to test
THRESHOLDS = {
    "OI_Z_OVERHEAT":        {"attr": "OI_Z_OVERHEAT",         "default": 1.5,    "range": [1.2, 1.35, 1.5, 1.65, 1.8]},
    "FUND_Z_OVERHEAT":      {"attr": "FUND_Z_OVERHEAT",       "default": 0.8,    "range": [0.6, 0.7, 0.8, 0.9, 1.0]},
    "LIQ_SHORT_Z_SQUEEZE":  {"attr": "LIQ_SHORT_Z_SQUEEZE",   "default": 3.0,    "range": [2.4, 2.7, 3.0, 3.3, 3.6]},
    "PRICE_VS_SMA_OVEREXT":  {"attr": "PRICE_VS_SMA_OVEREXT_LO", "default": 8,   "range": [6, 7, 8, 9, 10]},
    "CONFLUENCE_SIGNAL":     {"attr": "CONFLUENCE_SIGNAL",     "default": 4,      "range": [3, 4, 5]},
}

# TOP_OI for alt filter
TOP_OI_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "DOGEUSDT", "TRXUSDT", "UNIUSDT", "SUIUSDT", "ADAUSDT",
}

COOLDOWN_DAYS = 1
CLUSTER_GAP_DAYS = 2


def _detect_and_filter(bars, symbol, days=1100, confluence_min=CONFLUENCE_SIGNAL, alt_min=ALT_MIN_CONFLUENCE):
    """Detect signals with momentum filter + clustering (same as detect_all_signals)."""
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
            trend = bars[i].trend
            if direction == "long" and trend == "down":
                continue
            if direction == "short" and trend == "up":
                continue

            confluence, factors = compute_confluence(inp, direction)
            if confluence < confluence_min:
                continue
            if symbol not in TOP_OI_SYMBOLS and confluence < alt_min:
                continue

            cd_key = f"{sig_type}:{symbol}"
            if cd_key in cooldowns and (i - cooldowns[cd_key]) < COOLDOWN_DAYS:
                continue
            cooldowns[cd_key] = i

            raw.append(Signal(
                bar_idx=i, signal_type=sig_type, direction=direction,
                entry_price=bars[i].close, confluence=confluence,
                factors=factors[:5],
                zscores={"oi_z": bars[i].oi_z, "fund_z": bars[i].fund_z,
                         "liq_z": bars[i].liq_z, "vol_z": bars[i].vol_z},
            ))

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


async def run_sensitivity():
    await init_db()
    print("  Threshold Sensitivity Analysis")
    print(f"  Mode: {'4h exit' if USE_4H_EXIT else 'daily'}")
    print()

    # Preload data
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
    print(f"  Data loaded in {time.time() - t0:.1f}s")
    print()

    for thresh_name, cfg in THRESHOLDS.items():
        attr = cfg["attr"]
        default_val = cfg["default"]
        test_values = cfg["range"]

        print(f"  === {thresh_name} (default={default_val}) ===")
        print(f"  {'Value':>8} {'N':>6} {'WR':>6} {'GrossEV':>9} {'NetEV':>9} {'NetPF':>7}")
        print("  " + "-" * 50)

        for val in test_values:
            # Set the threshold
            original = getattr(sc, attr)
            setattr(sc, attr, val)

            # Special case: CONFLUENCE_SIGNAL is used in filtering
            conf_min = val if attr == "CONFLUENCE_SIGNAL" else CONFLUENCE_SIGNAL

            # Run backtest with this threshold
            results = []
            for sym, daily in all_daily.items():
                signals = _detect_and_filter(daily, sym, days=1100, confluence_min=conf_min)
                if not signals:
                    continue

                if USE_4H_EXIT and sym in all_4h:
                    bars_4h, df, dl = all_4h[sym]
                    sigs_by_date = {}
                    for i in range(len(daily)):
                        t = detect_signals_at_bar(daily, i)
                        if t:
                            sigs_by_date[daily[i].date] = t
                    cache_4h = {}
                    for i, b4 in enumerate(bars_4h):
                        s = sigs_by_date.get(b4.date)
                        if s:
                            cache_4h[i] = s

                    for sig in signals:
                        sig_date = daily[sig.bar_idx].date
                        idx_4h = dl.get(sig_date, -1)
                        if idx_4h < 0:
                            continue
                        sig.bar_idx_4h = idx_4h
                        sig.entry_price = bars_4h[idx_4h].close
                        orig = sig.bar_idx
                        sig.bar_idx = idx_4h
                        r = strategy_adaptive(bars_4h, sig, cache_4h)
                        if r:
                            _apply_costs(r, bars_4h, idx_4h, sig.direction)
                            results.append(r)
                        sig.bar_idx = orig
                else:
                    cache = {}
                    for i in range(len(daily)):
                        t = detect_signals_at_bar(daily, i)
                        if t:
                            cache[i] = t
                    for sig in signals:
                        r = strategy_adaptive(daily, sig, cache)
                        if r:
                            _apply_costs(r, daily, sig.bar_idx, sig.direction)
                            results.append(r)

            stats = _compute_stats(results) if results else {}
            n = stats.get("trades", 0)
            wr = stats.get("wr", 0)
            ev = stats.get("ev", 0)
            net_ev = stats.get("net_ev", 0)
            net_pf = stats.get("net_pf", 0)

            marker = " <-- default" if val == default_val else ""
            print(f"  {val:>8} {n:>6} {wr:>5.1f}% {ev:>+8.2f}% {net_ev:>+8.2f}% {net_pf:>6.2f}x{marker}")

            # Restore
            setattr(sc, attr, original)

        print()

    print("  Interpretation:")
    print("  - Stable: ±10% threshold change → EV stays same sign")
    print("  - Fragile: EV changes sign on small variation → likely overfit")
    print()


if __name__ == "__main__":
    asyncio.run(run_sensitivity())
