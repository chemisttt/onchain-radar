#!/usr/bin/env python3
"""Final strategy validation — closes 3 gaps in Phase B.

Gap 1: Hybrid C + adaptive exits (was only fixed TP/SL)
Gap 2: Walk-forward on HL symbol set (was only old symbols)
Gap 3: All combined: HL + Hybrid C + adaptive + walk-forward

Usage:
  cd backend && python3 scripts/validate_strategy.py
  cd backend && python3 scripts/validate_strategy.py --quick   # skip walk-forward
"""

import asyncio
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force 4h exit mode
if "--4h" not in sys.argv:
    sys.argv.append("--4h")

from db import init_db, get_db
from services.signal_conditions import SignalInput, detect_signals, compute_confluence
from scripts.setup_backtest import (
    load_symbol_data, load_4h_bars, detect_all_signals, detect_signals_at_bar,
    Bar, Bar4h, Signal, ExitResult,
    strategy_adaptive, _apply_costs, _compute_stats,
    BARS_PER_DAY, MIN_POINTS, Z_WINDOW, SMA_PERIOD,
    TOP_OI_SYMBOLS, ALT_MIN_CONFLUENCE, CONFLUENCE_SIGNAL,
    COOLDOWN_DAYS, CLUSTER_GAP_DAYS,
    _rolling_zscore_np, _rolling_sma_np, _rolling_atr_np, _shift_pct,
)

SKIP_WF = "--quick" in sys.argv

# ─── Symbol Sets ──────────────────────────────────────────────────────

OLD_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT", "AAVEUSDT", "DOTUSDT",
    "FILUSDT", "ATOMUSDT", "TRXUSDT", "JUPUSDT", "SEIUSDT", "TIAUSDT",
    "INJUSDT", "TRUMPUSDT", "WIFUSDT", "TONUSDT", "RENDERUSDT", "ENAUSDT",
]

HL_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT", "AAVEUSDT", "DOTUSDT",
    "TRXUSDT", "TONUSDT", "ENAUSDT", "TRUMPUSDT", "WIFUSDT",
    "JUPUSDT", "INJUSDT", "SEIUSDT",
    "HYPEUSDT", "ZECUSDT", "TAOUSDT", "WLDUSDT",
]

HYBRID_C_4H_TYPES = {"liq_short_squeeze", "momentum_divergence", "div_top_1d"}
GLOBAL_DAILY_CAP = 5

# ─── 4h Detection ─────────────────────────────────────────────────────

Z_WINDOW_4H = 2190
MIN_POINTS_4H = 120
SMA_PERIOD_4H = 120
COOLDOWN_CANDLES_4H = 6


def _zscore_simple(values: list[float]) -> float:
    n = len(values)
    if n < MIN_POINTS_4H:
        return 0.0
    mean = sum(values) / n
    std = (sum((x - mean) ** 2 for x in values) / n) ** 0.5
    return (values[-1] - mean) / std if std > 1e-10 else 0.0


def _sma_simple(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    s = values[-period:] if len(values) >= period else values
    return sum(s) / len(s)


async def detect_4h_signals(symbol: str, days: int = 1095,
                            types_filter: set | None = None) -> list[Signal]:
    """Detect signals on 4h bars, return Signal objects for exit simulation.

    Uses derivatives_4h for z-scores (Z_WINDOW_4H=2190).
    Optionally filters to specific signal types (for Hybrid C).
    """
    db = get_db()
    rows = await db.execute_fetchall(
        """SELECT ts, close_price, open_interest_usd, oi_binance_usd, funding_rate,
                  liquidations_long, liquidations_short, liquidations_delta, volume_usd
           FROM derivatives_4h WHERE symbol = ? ORDER BY ts ASC""",
        (symbol,),
    )
    if not rows or len(rows) < MIN_POINTS_4H + 10:
        return []

    timestamps = [r["ts"] // 1000 for r in rows]
    prices = [r["close_price"] or 0 for r in rows]
    ois = [(r["oi_binance_usd"] or 0) or (r["open_interest_usd"] or 0) for r in rows]
    fundings = [r["funding_rate"] or 0 for r in rows]
    liq_deltas = [r["liquidations_delta"] or 0 for r in rows]
    liq_longs = [r["liquidations_long"] or 0 for r in rows]
    liq_shorts = [r["liquidations_short"] or 0 for r in rows]
    volumes = [r["volume_usd"] or 0 for r in rows]

    # Momentum data
    mom_rows = await db.execute_fetchall(
        "SELECT date, momentum_value, relative_volume FROM daily_momentum WHERE symbol = ? ORDER BY date ASC",
        (symbol,),
    )
    mom_by_date: dict[str, tuple[float, float]] = {}
    for r in mom_rows:
        mom_by_date[r["date"]] = (r["momentum_value"] or 0.0, r["relative_volume"] or 0.0)

    total = len(rows)
    lookback_candles = days * 6
    warmup_end = max(MIN_POINTS_4H, total - lookback_candles)

    # Pre-compute OI z-scores for acceleration
    oi_zscores: list[float] = []
    for i in range(total):
        start = max(0, i - Z_WINDOW_4H + 1)
        oi_zscores.append(_zscore_simple(ois[start:i + 1]))

    signals: list[Signal] = []
    cooldowns: dict[str, int] = {}

    for i in range(warmup_end, total):
        price = prices[i]
        if price <= 0:
            continue

        ts_sec = timestamps[i]
        date_str = datetime.utcfromtimestamp(ts_sec).strftime("%Y-%m-%d")

        sma = _sma_simple(prices[:i + 1], SMA_PERIOD_4H)
        price_vs_sma = ((price - sma) / sma * 100) if sma > 0 else 0
        trend = "up" if price_vs_sma > 2 else ("down" if price_vs_sma < -2 else "neutral")

        start = max(0, i - Z_WINDOW_4H + 1)
        oi_z = oi_zscores[i]
        fund_z = _zscore_simple(fundings[start:i + 1])
        liq_z = _zscore_simple(liq_deltas[start:i + 1])
        vol_z = _zscore_simple(volumes[start:i + 1])
        liq_long_z = _zscore_simple(liq_longs[start:i + 1])
        liq_short_z = _zscore_simple(liq_shorts[start:i + 1])

        # 6-candle (~1d) changes
        idx_6 = max(0, i - 6)
        price_chg_6 = ((price - prices[idx_6]) / prices[idx_6] * 100) if prices[idx_6] > 0 else 0
        oi_chg_6 = ((ois[i] - ois[idx_6]) / ois[idx_6] * 100) if ois[idx_6] > 0 else 0

        # 18-candle (~3d) changes
        idx_18 = max(0, i - 18)
        price_chg_18 = ((price - prices[idx_18]) / prices[idx_18] * 100) if prices[idx_18] > 0 else 0
        oi_chg_18 = ((ois[i] - ois[idx_18]) / ois[idx_18] * 100) if ois[idx_18] > 0 else 0

        # 30-candle (~5d) momentum
        idx_30 = max(0, i - 30)
        price_momentum = ((price - prices[idx_30]) / prices[idx_30] * 100) if prices[idx_30] > 0 else 0

        z_accel = oi_z - oi_zscores[max(0, i - 18)] if i >= 18 else 0.0
        vol_declining = (volumes[i] < volumes[i - 6] < volumes[i - 12]) if i >= 18 else False

        has_fd = i >= 18
        fund_delta = (fundings[i] - fundings[i - 18]) if has_fd else 0.0

        mom = mom_by_date.get(date_str, (0.0, 0.0))

        inp = SignalInput(
            oi_z=oi_z, fund_z=fund_z, liq_z=liq_z, vol_z=vol_z,
            price_chg=price_chg_6, oi_chg=oi_chg_6,
            price_chg_3d=price_chg_18, oi_chg_3d=oi_chg_18,
            price_vs_sma=price_vs_sma, trend=trend,
            funding_rate=fundings[i],
            liq_long_z=liq_long_z, liq_short_z=liq_short_z,
            price_momentum=price_momentum, z_accel=z_accel,
            vol_declining_3d=vol_declining,
            fund_delta_3d=fund_delta, has_fund_delta=has_fd,
            momentum_value=mom[0], relative_volume=mom[1],
            price_chg_5d=price_momentum,
        )

        triggered = detect_signals(inp)

        for sig_type, direction in triggered:
            if types_filter and sig_type not in types_filter:
                continue

            # Momentum filter
            if sig_type not in ("distribution", "momentum_divergence", "fund_spike"):
                if direction == "long" and trend == "down":
                    continue
                if direction == "short" and trend == "up":
                    continue

            confluence, factors = compute_confluence(inp, direction)
            if confluence < CONFLUENCE_SIGNAL:
                continue
            if symbol not in TOP_OI_SYMBOLS and confluence < ALT_MIN_CONFLUENCE:
                continue

            cd_key = f"{sig_type}:{symbol}"
            if cd_key in cooldowns and (i - cooldowns[cd_key]) < COOLDOWN_CANDLES_4H:
                continue
            cooldowns[cd_key] = i

            signals.append(Signal(
                bar_idx=-1,  # will be remapped to 4h OHLCV index
                signal_type=sig_type,
                direction=direction,
                entry_price=price,
                confluence=confluence,
                factors=factors[:5],
                zscores={"oi_z": oi_z, "fund_z": fund_z, "liq_z": liq_z, "vol_z": vol_z},
                bar_idx_4h=-1,
            ))
            # Store ts for date mapping
            signals[-1]._ts = ts_sec
            signals[-1]._date = date_str

    # Cluster nearby same-direction signals (6 candle gap ~ 1 day)
    if len(signals) > 1:
        clustered: list[Signal] = []
        for sig in signals:
            merged = False
            for existing in clustered:
                if existing.direction != sig.direction:
                    continue
                # Use timestamp-based gap
                if hasattr(sig, '_ts') and hasattr(existing, '_ts'):
                    gap_hours = abs(sig._ts - existing._ts) / 3600
                    if gap_hours <= 24:  # ~6 candles
                        if sig.confluence > existing.confluence:
                            clustered[clustered.index(existing)] = sig
                        merged = True
                        break
            if not merged:
                clustered.append(sig)
        signals = clustered

    return signals


# ─── Hybrid C Merge ───────────────────────────────────────────────────

def merge_hybrid_c(daily_signals: list[tuple[Signal, str]],
                   h4_signals: list[tuple[Signal, str]]) -> list[tuple[Signal, str]]:
    """Take HYBRID_C types from 4h, rest from daily. Dedup by date+direction."""
    merged = []
    for sig, sym in h4_signals:
        if sig.signal_type in HYBRID_C_4H_TYPES:
            merged.append((sig, sym))
    for sig, sym in daily_signals:
        if sig.signal_type not in HYBRID_C_4H_TYPES:
            merged.append((sig, sym))

    # Dedup: same symbol + same day + same direction → keep higher confluence
    seen: dict[str, tuple[Signal, str]] = {}
    for sig, sym in merged:
        date = getattr(sig, '_date', '?')
        if date == '?':
            # daily signal — get date from bar_idx (stored separately)
            date = getattr(sig, '_daily_date', '?')
        key = f"{sym}:{date}:{sig.direction}"
        if key not in seen or sig.confluence > seen[key][0].confluence:
            seen[key] = (sig, sym)
    return list(seen.values())


# ─── Run Config ───────────────────────────────────────────────────────

async def run_config(symbols: list[str], label: str, hybrid_c: bool = False,
                     date_from: str | None = None, date_to: str | None = None) -> dict:
    """Run one configuration: detect signals + adaptive exit on 4h bars.

    Returns stats dict with 'results' list for further analysis.
    """
    all_signals: list[tuple[Signal, list, str]] = []  # (signal, exit_bars_4h, symbol)
    daily_bars_cache: dict[str, list[Bar]] = {}

    for sym in symbols:
        daily_bars = await load_symbol_data(sym)
        if not daily_bars:
            continue
        daily_bars_cache[sym] = daily_bars

        # Daily detection (all types, or non-Hybrid-C types)
        daily_sigs_raw = detect_all_signals(daily_bars, sym, days=9999)
        daily_sigs_dated: list[tuple[Signal, str]] = []
        for sig in daily_sigs_raw:
            sig._daily_date = daily_bars[sig.bar_idx].date
            daily_sigs_dated.append((sig, sym))

        if hybrid_c:
            # 4h detection for Hybrid C types
            h4_sigs = await detect_4h_signals(sym, days=1095, types_filter=HYBRID_C_4H_TYPES)
            h4_dated: list[tuple[Signal, str]] = []
            for sig in h4_sigs:
                h4_dated.append((sig, sym))

            # Merge
            final_sigs = merge_hybrid_c(daily_sigs_dated, h4_dated)
        else:
            final_sigs = daily_sigs_dated

        # Load 4h OHLCV for exit simulation
        bars_4h, date_first, date_last = await load_4h_bars(sym, daily_bars)
        if not bars_4h:
            continue

        for sig, _ in final_sigs:
            # Get date for this signal
            sig_date = getattr(sig, '_date', None) or getattr(sig, '_daily_date', None)
            if not sig_date:
                continue

            # Date range filter
            if date_from and sig_date < date_from:
                continue
            if date_to and sig_date >= date_to:
                continue

            # Map to 4h OHLCV index
            idx_4h = date_last.get(sig_date, -1)
            if idx_4h < 0:
                continue

            sig.bar_idx_4h = idx_4h
            sig.entry_price = bars_4h[idx_4h].close
            all_signals.append((sig, bars_4h, sym))

    # Global daily cap
    by_day: dict[str, list[tuple[Signal, list, str]]] = defaultdict(list)
    for sig, bars_4h, sym in all_signals:
        day = bars_4h[sig.bar_idx_4h].date
        by_day[day].append((sig, bars_4h, sym))

    capped: list[tuple[Signal, list, str]] = []
    for day, ds in by_day.items():
        ds.sort(key=lambda x: -x[0].confluence)
        capped.extend(ds[:GLOBAL_DAILY_CAP])
    all_signals = capped

    if not all_signals:
        return {"label": label, "trades": 0, "net_ev": 0, "net_wr": 0, "net_pf": 0,
                "net_total_pnl": 0, "results": []}

    # Build counter-signal cache per exit_bars set
    cache_by_bars: dict[int, dict] = {}
    for sig, bars_4h, sym in all_signals:
        bid = id(bars_4h)
        if bid in cache_by_bars:
            continue
        daily_bars = daily_bars_cache.get(sym, [])
        if not daily_bars:
            cache_by_bars[bid] = {}
            continue
        daily_sigs_by_date: dict[str, list[tuple[str, str]]] = {}
        for i in range(len(daily_bars)):
            t = detect_signals_at_bar(daily_bars, i)
            if t:
                daily_sigs_by_date[daily_bars[i].date] = t
        cache_4h: dict[int, list[tuple[str, str]]] = {}
        for i, b4 in enumerate(bars_4h):
            sigs = daily_sigs_by_date.get(b4.date)
            if sigs:
                cache_4h[i] = sigs
        cache_by_bars[bid] = cache_4h

    # Run adaptive exit
    results: list[ExitResult] = []
    sig_results: dict[str, list[ExitResult]] = defaultdict(list)

    for sig, bars_4h, sym in all_signals:
        cache = cache_by_bars.get(id(bars_4h), {})
        orig_idx = sig.bar_idx
        sig.bar_idx = sig.bar_idx_4h
        r = strategy_adaptive(bars_4h, sig, cache)
        if r:
            _apply_costs(r, bars_4h, sig.bar_idx, sig.direction)
            results.append(r)
            sig_results[sig.signal_type].append(r)
        sig.bar_idx = orig_idx

    stats = _compute_stats(results)
    if stats:
        stats["label"] = label
        stats["results"] = results
        stats["sig_results"] = dict(sig_results)
    else:
        stats = {"label": label, "trades": 0, "net_ev": 0, "net_wr": 0, "net_pf": 0,
                 "net_total_pnl": 0, "results": [], "sig_results": {}}
    return stats


def _print_stats(s: dict, indent: int = 2):
    if s.get("trades", 0) == 0:
        print(f"{' ' * indent}{s['label']:<50} N=   0")
        return
    print(f"{' ' * indent}{s['label']:<50} N={s['trades']:>4}  "
          f"WR={s['net_wr']:>5.1f}%  EV={s['net_ev']:>+6.2f}%  "
          f"PF={s['net_pf']:>5.2f}x  PnL={s['net_total_pnl']:>+7.1f}%")


def _print_per_signal(sig_results: dict[str, list[ExitResult]]):
    if not sig_results:
        return
    print(f"    {'Signal':<24} {'N':>4} {'WR':>6} {'EV':>8} {'PnL':>8}")
    print("    " + "-" * 55)
    items = []
    for st, rs in sig_results.items():
        s = _compute_stats(rs)
        if s and s["trades"] > 0:
            items.append((st, s))
    items.sort(key=lambda x: -x[1]["net_ev"])
    for st, s in items:
        print(f"    {st:<24} {s['trades']:>4} {s['net_wr']:>5.1f}% {s['net_ev']:>+7.2f}% "
              f"{s['net_total_pnl']:>+7.1f}%")


# ─── Walk-Forward ─────────────────────────────────────────────────────

async def walk_forward(symbols: list[str], label: str, hybrid_c: bool = False):
    """6-window expanding walk-forward."""
    # Get date range
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT MIN(date) as mn, MAX(date) as mx FROM daily_derivatives"
    )
    min_date = rows[0]["mn"]
    max_date = rows[0]["mx"]

    train_months = 12
    test_months = 6

    start = datetime.strptime(min_date, "%Y-%m-%d")
    end = datetime.strptime(max_date, "%Y-%m-%d")

    windows = []
    cursor = start
    while True:
        train_end = cursor + timedelta(days=train_months * 30)
        test_start = train_end
        test_end = test_start + timedelta(days=test_months * 30)
        if test_end > end + timedelta(days=30):
            break
        windows.append({
            "train_start": cursor.strftime("%Y-%m-%d"),
            "train_end": train_end.strftime("%Y-%m-%d"),
            "test_start": test_start.strftime("%Y-%m-%d"),
            "test_end": test_end.strftime("%Y-%m-%d"),
        })
        cursor += timedelta(days=test_months * 30)

    print(f"\n  Walk-Forward: {label} ({len(windows)} windows)")
    print(f"  {'Window':<6} {'Train':>24} {'Test':>24} {'Tr.N':>5} {'Te.N':>5} "
          f"{'Tr.EV':>8} {'Te.EV':>8} {'Te.WR':>7}")
    print("  " + "-" * 90)

    test_evs = []

    for wi, w in enumerate(windows):
        # Train: detect in train period only
        train_s = await run_config(symbols, f"W{wi+1} train", hybrid_c=hybrid_c,
                                   date_from=w["train_start"], date_to=w["train_end"])
        test_s = await run_config(symbols, f"W{wi+1} test", hybrid_c=hybrid_c,
                                  date_from=w["test_start"], date_to=w["test_end"])

        tr_ev = train_s.get("net_ev", 0)
        te_ev = test_s.get("net_ev", 0)
        te_wr = test_s.get("net_wr", 0)
        tr_n = train_s.get("trades", 0)
        te_n = test_s.get("trades", 0)
        test_evs.append(te_ev)

        train_range = f"{w['train_start']}..{w['train_end']}"
        test_range = f"{w['test_start']}..{w['test_end']}"
        print(f"  W{wi+1:<4} {train_range:>24} {test_range:>24} {tr_n:>5} {te_n:>5} "
              f"{tr_ev:>+7.2f}% {te_ev:>+7.2f}% {te_wr:>6.1f}%")

    # Summary
    import statistics
    if test_evs:
        pos = sum(1 for ev in test_evs if ev > 0)
        avg_ev = statistics.mean(test_evs)
        worst = min(test_evs)
        print(f"\n  Positive: {pos}/{len(test_evs)}, Avg test EV: {avg_ev:+.2f}%, "
              f"Worst: {worst:+.2f}%")
        return {"positive": pos, "total": len(test_evs), "avg_ev": avg_ev, "worst": worst}
    return {}


# ─── Main ─────────────────────────────────────────────────────────────

async def main():
    await init_db()
    t0 = time.time()

    print()
    print("=" * 100)
    print("  FINAL STRATEGY VALIDATION")
    print("=" * 100)

    # ═══════════════════════════════════════════════════════════════
    #  GAP 1: Hybrid C + Adaptive Exits
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 100)
    print("  GAP 1: HYBRID C + ADAPTIVE EXITS (old symbols)")
    print("=" * 100)
    print()

    s_old_daily = await run_config(OLD_SYMBOLS, "Old 30 + daily (Phase B baseline)")
    _print_stats(s_old_daily)
    _print_per_signal(s_old_daily.get("sig_results", {}))

    print()
    s_old_hybrid = await run_config(OLD_SYMBOLS, "Old 30 + Hybrid C + adaptive", hybrid_c=True)
    _print_stats(s_old_hybrid)
    _print_per_signal(s_old_hybrid.get("sig_results", {}))

    # ═══════════════════════════════════════════════════════════════
    #  GAP 2+3: HL Symbols + Hybrid C + Adaptive
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 100)
    print("  GAP 2+3: HL SYMBOLS + HYBRID C + ADAPTIVE EXITS")
    print("=" * 100)
    print()

    s_hl_daily = await run_config(HL_SYMBOLS, "HL 30 + daily + adaptive")
    _print_stats(s_hl_daily)
    _print_per_signal(s_hl_daily.get("sig_results", {}))

    print()
    s_hl_hybrid = await run_config(HL_SYMBOLS, "HL 30 + Hybrid C + adaptive", hybrid_c=True)
    _print_stats(s_hl_hybrid)
    _print_per_signal(s_hl_hybrid.get("sig_results", {}))

    # ═══════════════════════════════════════════════════════════════
    #  Summary Comparison
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 100)
    print("  COMPARISON TABLE")
    print("=" * 100)
    print(f"  {'Config':<52} {'N':>4} {'WR':>6} {'EV':>8} {'PF':>6} {'PnL':>8}")
    print("  " + "-" * 88)

    for s in [s_old_daily, s_old_hybrid, s_hl_daily, s_hl_hybrid]:
        if s.get("trades", 0) == 0:
            print(f"  {s['label']:<52} {'—':>4}")
            continue
        print(f"  {s['label']:<52} {s['trades']:>4} {s['net_wr']:>5.1f}% "
              f"{s['net_ev']:>+7.2f}% {s['net_pf']:>5.2f}x {s['net_total_pnl']:>+7.1f}%")

    # Deltas
    print()
    base = s_old_daily
    for s in [s_old_hybrid, s_hl_daily, s_hl_hybrid]:
        if base.get("trades", 0) > 0 and s.get("trades", 0) > 0:
            dn = s["trades"] - base["trades"]
            dev = s["net_ev"] - base["net_ev"]
            dpnl = s["net_total_pnl"] - base["net_total_pnl"]
            print(f"  {s['label']:<52} ΔN={dn:+d}  ΔEV={dev:+.2f}%  ΔPnL={dpnl:+.1f}%")

    # ═══════════════════════════════════════════════════════════════
    #  Walk-Forward
    # ═══════════════════════════════════════════════════════════════
    if not SKIP_WF:
        print("\n" + "=" * 100)
        print("  WALK-FORWARD VALIDATION")
        print("=" * 100)

        wf_hl_daily = await walk_forward(HL_SYMBOLS, "HL daily + adaptive")
        wf_hl_hybrid = await walk_forward(HL_SYMBOLS, "HL Hybrid C + adaptive", hybrid_c=True)

        print("\n  Walk-Forward Summary:")
        for label, wf in [("HL daily", wf_hl_daily), ("HL Hybrid C", wf_hl_hybrid)]:
            if wf:
                print(f"    {label:<20} {wf['positive']}/{wf['total']} positive, "
                      f"avg={wf['avg_ev']:+.2f}%, worst={wf['worst']:+.2f}%")
    else:
        print("\n  (Walk-forward skipped — use without --quick to include)")

    elapsed = time.time() - t0
    print(f"\n  Total time: {elapsed:.0f}s")
    print()


if __name__ == "__main__":
    asyncio.run(main())
