#!/usr/bin/env python3
"""Equity simulation for Hybrid C (860 trades) + 3 risk profiles + stress tests.

Runs the full Hybrid C trade collection, then simulates equity curves
across a grid of (alloc_pct, leverage, max_concurrent) scenarios.
Selects Aggressive / Stable / Conservative profiles.
Runs 4 stress tests: correlated drawdown, slippage, capacity cap, Monte Carlo.

Usage:
  cd backend && python3 scripts/equity_sim_hybrid.py
"""

import asyncio
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force 4h exit mode
if "--4h" not in sys.argv:
    sys.argv.append("--4h")

from db import init_db, get_db
from scripts.setup_backtest import (
    load_symbol_data, load_4h_bars, detect_all_signals, detect_signals_at_bar,
    strategy_adaptive, _apply_costs, BARS_PER_DAY,
)
from scripts.validate_strategy import (
    HL_SYMBOLS, HYBRID_C_4H_TYPES, GLOBAL_DAILY_CAP,
    detect_4h_signals, merge_hybrid_c,
)

START_EQUITY = 1000.0

# ─── Grid Parameters ──────────────────────────────────────────────────

ALLOC_PCTS = [10, 15, 20, 25, 33, 50, 75, 100]
LEVERAGES = [1, 2, 3, 5]
MAX_CONCURRENTS = [1, 2, 3, 5, 10]

# ─── Trade Collection ─────────────────────────────────────────────────


async def collect_trades() -> list[dict]:
    """Collect Hybrid C trades with entry/exit timestamps + MAE."""
    await init_db()
    all_trades = []
    daily_cache: dict[str, list] = {}

    for sym in HL_SYMBOLS:
        daily = await load_symbol_data(sym)
        if not daily:
            continue
        daily_cache[sym] = daily

        # Daily detection (all types)
        daily_sigs_raw = detect_all_signals(daily, sym, days=9999)
        daily_sigs_dated = []
        for sig in daily_sigs_raw:
            sig._daily_date = daily[sig.bar_idx].date
            daily_sigs_dated.append((sig, sym))

        # 4h detection for Hybrid C types
        h4_sigs = await detect_4h_signals(sym, days=1095, types_filter=HYBRID_C_4H_TYPES)
        h4_dated = [(sig, sym) for sig in h4_sigs]

        # Merge Hybrid C
        final_sigs = merge_hybrid_c(daily_sigs_dated, h4_dated)

        # Load 4h OHLCV for exit simulation
        bars_4h, date_first, date_last = await load_4h_bars(sym, daily)
        if not bars_4h:
            continue

        # Build counter-signal cache
        sigs_by_date: dict[str, list] = {}
        for i in range(len(daily)):
            t = detect_signals_at_bar(daily, i)
            if t:
                sigs_by_date[daily[i].date] = t
        cache_4h: dict[int, list] = {}
        for i, b4 in enumerate(bars_4h):
            s = sigs_by_date.get(b4.date)
            if s:
                cache_4h[i] = s

        for sig, _ in final_sigs:
            sig_date = getattr(sig, '_date', None) or getattr(sig, '_daily_date', None)
            if not sig_date:
                continue
            idx_4h = date_last.get(sig_date, -1)
            if idx_4h < 0:
                continue

            sig.bar_idx_4h = idx_4h
            sig.entry_price = bars_4h[idx_4h].close

            orig = sig.bar_idx
            sig.bar_idx = sig.bar_idx_4h
            result = strategy_adaptive(bars_4h, sig, cache_4h)
            sig.bar_idx = orig

            if result:
                _apply_costs(result, bars_4h, idx_4h, sig.direction)
                exit_idx = min(result.exit_bar, len(bars_4h) - 1)
                all_trades.append({
                    "entry_date": sig_date,
                    "entry_ts": bars_4h[idx_4h].ts,
                    "exit_ts": bars_4h[exit_idx].ts,
                    "net_pnl_pct": result.net_pnl_pct,
                    "mae_pct": result.max_drawdown_pct,
                    "bars_held": result.bars_held,
                    "signal": sig.signal_type,
                    "symbol": sym,
                    "direction": sig.direction,
                    "exit_reason": result.exit_reason,
                    "confluence": sig.confluence,
                })

    # Global daily cap (same as backtest)
    by_day: dict[str, list] = defaultdict(list)
    for t in all_trades:
        by_day[t["entry_date"]].append(t)
    capped = []
    for day in sorted(by_day):
        trades = sorted(by_day[day], key=lambda x: -x["confluence"])[:GLOBAL_DAILY_CAP]
        capped.extend(trades)
    all_trades = sorted(capped, key=lambda x: x["entry_ts"])

    return all_trades


# ─── Simulation Engine ────────────────────────────────────────────────


def simulate(trades: list[dict], alloc_pct: float, leverage: float,
             max_concurrent: int) -> dict:
    """Simulate equity curve with compounding, concurrent limits, liquidation."""
    equity = START_EQUITY
    peak = START_EQUITY
    max_dd = 0.0
    liquidations = 0
    skipped = 0
    total_taken = 0
    max_consec_loss = 0
    cur_consec_loss = 0

    monthly_pnl: dict[str, float] = defaultdict(float)
    alloc_frac = alloc_pct / 100.0

    # Build event timeline
    events = []
    for i, t in enumerate(trades):
        events.append(("entry", t["entry_ts"], i))
        events.append(("exit", t["exit_ts"], i))
    events.sort(key=lambda x: (x[1], 0 if x[0] == "exit" else 1))

    open_pos: dict[int, float] = {}  # trade_idx → allocated amount
    dd_start_equity = peak
    longest_dd_days = 0
    dd_start_ts = None
    cur_dd_start_ts = None

    equity_curve = []  # (ts, equity) for drawdown duration tracking

    for ev_type, ts, idx in events:
        t = trades[idx]
        if ev_type == "entry":
            if len(open_pos) >= max_concurrent:
                skipped += 1
                continue
            amt = equity * alloc_frac
            if amt < 1.0:  # equity depleted
                skipped += 1
                continue
            open_pos[idx] = amt
            total_taken += 1
        else:  # exit
            if idx not in open_pos:
                continue
            amt = open_pos.pop(idx)

            # Liquidation check: if intra-trade MAE × leverage >= 90%
            if leverage > 1 and t["mae_pct"] * leverage >= 90.0:
                pnl = -amt  # lose entire allocation
                liquidations += 1
            else:
                pnl = amt * (t["net_pnl_pct"] / 100.0) * leverage
                # Can't lose more than allocated
                pnl = max(pnl, -amt)

            equity += pnl
            month = t["entry_date"][:7]
            monthly_pnl[month] += pnl

            # Track consecutive losses
            if pnl <= 0:
                cur_consec_loss += 1
                max_consec_loss = max(max_consec_loss, cur_consec_loss)
            else:
                cur_consec_loss = 0

            # Drawdown tracking
            if equity > peak:
                peak = equity
                cur_dd_start_ts = None
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
            if dd > 0 and cur_dd_start_ts is None:
                cur_dd_start_ts = ts

            equity_curve.append((ts, equity))

            if equity <= 0:
                equity = 0
                break

    # Compute longest drawdown period (ts is in milliseconds)
    ms_per_day = 86400 * 1000
    in_dd_since = None
    peak_track = START_EQUITY
    for ts, eq in equity_curve:
        if eq > peak_track:
            if in_dd_since is not None:
                dd_days = (ts - in_dd_since) / ms_per_day
                longest_dd_days = max(longest_dd_days, dd_days)
            peak_track = eq
            in_dd_since = None
        elif in_dd_since is None and eq < peak_track:
            in_dd_since = ts
    # Handle still-in-drawdown at end
    if in_dd_since is not None and equity_curve:
        dd_days = (equity_curve[-1][0] - in_dd_since) / ms_per_day
        longest_dd_days = max(longest_dd_days, dd_days)

    roi = (equity - START_EQUITY) / START_EQUITY * 100

    return {
        "alloc_pct": alloc_pct,
        "leverage": leverage,
        "max_concurrent": max_concurrent,
        "final_equity": equity,
        "roi": roi,
        "max_dd": max_dd,
        "liquidations": liquidations,
        "skipped": skipped,
        "taken": total_taken,
        "max_consec_loss": max_consec_loss,
        "longest_dd_days": longest_dd_days,
        "monthly_pnl": dict(monthly_pnl),
        "roi_dd_ratio": roi / max_dd if max_dd > 0 else float("inf"),
    }


# ─── Profile Selection ────────────────────────────────────────────────


def select_profiles(results: list[dict]) -> dict[str, dict]:
    """Select 3 risk profiles from grid results."""
    profiles = {}

    # Aggressive: max ROI, MaxDD ≤ 60%, liquidations acceptable
    aggressive = [r for r in results if r["max_dd"] <= 60 and r["final_equity"] > 0]
    if aggressive:
        profiles["Aggressive"] = max(aggressive, key=lambda r: r["roi"])

    # Stable: best ROI/MaxDD ratio, MaxDD ≤ 20%, no liquidations
    stable = [r for r in results if r["max_dd"] <= 20 and r["liquidations"] == 0]
    if stable:
        profiles["Stable"] = max(stable, key=lambda r: r["roi_dd_ratio"])

    # Conservative: best ROI/MaxDD ratio, MaxDD ≤ 12%, no liquidations
    conservative = [r for r in results if r["max_dd"] <= 12 and r["liquidations"] == 0]
    if conservative:
        profiles["Conservative"] = max(conservative, key=lambda r: r["roi_dd_ratio"])

    return profiles


# ─── Stress Test 1: Correlated Drawdown ───────────────────────────


def stress_correlated_drawdown(trades: list[dict], alloc_pct: float,
                                leverage: float, max_concurrent: int) -> dict:
    """Simulate worst-case: all open positions hit their MAE simultaneously.

    At each event, compute: if all open positions experienced their max
    adverse excursion RIGHT NOW, what would equity be?
    This models a flash crash where everything dumps together.
    """
    equity = START_EQUITY
    alloc_frac = alloc_pct / 100.0

    events = []
    for i, t in enumerate(trades):
        events.append(("entry", t["entry_ts"], i))
        events.append(("exit", t["exit_ts"], i))
    events.sort(key=lambda x: (x[1], 0 if x[0] == "exit" else 1))

    open_pos: dict[int, float] = {}  # idx → allocated amount
    worst_stress_dd = 0.0
    worst_stress_equity = START_EQUITY
    worst_n_open = 0
    ruin_count = 0  # how many times stress equity <= 0

    for ev_type, ts, idx in events:
        t = trades[idx]
        if ev_type == "entry":
            if len(open_pos) >= max_concurrent:
                continue
            amt = equity * alloc_frac
            if amt < 1.0:
                continue
            open_pos[idx] = amt
        else:
            if idx not in open_pos:
                continue
            amt = open_pos.pop(idx)
            pnl = amt * (t["net_pnl_pct"] / 100.0) * leverage
            pnl = max(pnl, -amt)
            equity += pnl
            if equity <= 0:
                equity = 0
                break

        # Stress check: what if all open positions hit MAE right now?
        if len(open_pos) >= 2:
            total_stress_loss = 0.0
            for oidx, oamt in open_pos.items():
                mae = trades[oidx]["mae_pct"]
                loss = oamt * (mae / 100.0) * leverage
                loss = min(loss, oamt)  # can't lose more than allocated
                total_stress_loss += loss

            stress_eq = equity - total_stress_loss
            if stress_eq <= 0:
                ruin_count += 1
            stress_dd = total_stress_loss / equity * 100 if equity > 0 else 100
            if stress_dd > worst_stress_dd:
                worst_stress_dd = stress_dd
                worst_stress_equity = stress_eq
                worst_n_open = len(open_pos)

    return {
        "worst_stress_dd": worst_stress_dd,
        "worst_stress_equity": worst_stress_equity,
        "worst_n_open": worst_n_open,
        "ruin_moments": ruin_count,
    }


# ─── Stress Test 2: Slippage Sensitivity ─────────────────────────


def test_slippage(trades: list[dict], alloc_pct: float, leverage: float,
                  max_concurrent: int, slippages: list[float]) -> list[dict]:
    """Re-run simulation with additional round-trip slippage costs."""
    results = []
    for slip in slippages:
        # Apply slippage to each trade's PnL
        adjusted = []
        for t in trades:
            t2 = dict(t)
            t2["net_pnl_pct"] = t["net_pnl_pct"] - slip
            adjusted.append(t2)
        r = simulate(adjusted, alloc_pct, leverage, max_concurrent)
        r["slippage"] = slip
        # Recompute EV
        taken_trades = [t for t in adjusted]  # all trades (simulate handles skipping)
        r["adj_ev"] = sum(t["net_pnl_pct"] for t in adjusted) / len(adjusted)
        results.append(r)
    return results


# ─── Stress Test 3: Capacity Cap ─────────────────────────────────


def simulate_with_cap(trades: list[dict], alloc_pct: float, leverage: float,
                      max_concurrent: int, cap_usd: float) -> dict:
    """Simulate with max position size cap (in USD notional)."""
    equity = START_EQUITY
    peak = START_EQUITY
    max_dd = 0.0
    alloc_frac = alloc_pct / 100.0
    capped_count = 0
    total_taken = 0
    skipped = 0

    events = []
    for i, t in enumerate(trades):
        events.append(("entry", t["entry_ts"], i))
        events.append(("exit", t["exit_ts"], i))
    events.sort(key=lambda x: (x[1], 0 if x[0] == "exit" else 1))

    open_pos: dict[int, float] = {}
    monthly_pnl: dict[str, float] = defaultdict(float)

    for ev_type, ts, idx in events:
        t = trades[idx]
        if ev_type == "entry":
            if len(open_pos) >= max_concurrent:
                skipped += 1
                continue
            amt = equity * alloc_frac
            # Cap: notional = amt * leverage, if > cap → reduce amt
            notional = amt * leverage
            if notional > cap_usd:
                amt = cap_usd / leverage
                capped_count += 1
            if amt < 1.0:
                skipped += 1
                continue
            open_pos[idx] = amt
            total_taken += 1
        else:
            if idx not in open_pos:
                continue
            amt = open_pos.pop(idx)
            pnl = amt * (t["net_pnl_pct"] / 100.0) * leverage
            pnl = max(pnl, -amt)
            equity += pnl
            month = t["entry_date"][:7]
            monthly_pnl[month] += pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
            if equity <= 0:
                equity = 0
                break

    roi = (equity - START_EQUITY) / START_EQUITY * 100
    return {
        "cap_usd": cap_usd,
        "final_equity": equity,
        "roi": roi,
        "max_dd": max_dd,
        "taken": total_taken,
        "capped_trades": capped_count,
        "skipped": skipped,
    }


# ─── Stress Test 4: Monte Carlo (Bootstrap) ──────────────────────


def monte_carlo(trades: list[dict], alloc_pct: float, leverage: float,
                max_concurrent: int, n_runs: int = 1000) -> dict:
    """Bootstrap resampling: sample N trades WITH replacement, simulate.

    Tests: "what if we got a different set of trades?"
    Some trades appear multiple times, others not at all.
    This breaks compounding commutativity and gives real variance.
    Also tracks max consecutive losses distribution.
    """
    alloc_frac = alloc_pct / 100.0
    n_trades = len(trades)
    rois = []
    max_dds = []
    max_consec_losses = []
    ruins = 0

    for _ in range(n_runs):
        # Bootstrap: sample with replacement
        sample = [trades[random.randint(0, n_trades - 1)] for _ in range(n_trades)]

        equity = START_EQUITY
        peak = START_EQUITY
        max_dd = 0.0
        consec = 0
        max_consec = 0

        for t in sample:
            amt = equity * alloc_frac
            if amt < 1.0:
                break
            pnl = amt * (t["net_pnl_pct"] / 100.0) * leverage
            pnl = max(pnl, -amt)
            equity += pnl

            if pnl <= 0:
                consec += 1
                max_consec = max(max_consec, consec)
            else:
                consec = 0

            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
            if equity <= 0:
                equity = 0
                ruins += 1
                break

        roi = (equity - START_EQUITY) / START_EQUITY * 100
        rois.append(roi)
        max_dds.append(max_dd)
        max_consec_losses.append(max_consec)

    rois.sort()
    max_dds.sort()
    max_consec_losses.sort()
    n = len(rois)

    return {
        "n_runs": n_runs,
        "median_roi": rois[n // 2],
        "p5_roi": rois[int(n * 0.05)],
        "p25_roi": rois[int(n * 0.25)],
        "p75_roi": rois[int(n * 0.75)],
        "p95_roi": rois[int(n * 0.95)],
        "mean_roi": sum(rois) / n,
        "median_dd": max_dds[n // 2],
        "p95_dd": max_dds[int(n * 0.95)],
        "max_dd": max(max_dds),
        "median_consec": max_consec_losses[n // 2],
        "p95_consec": max_consec_losses[int(n * 0.95)],
        "ruins": ruins,
        "ruin_pct": ruins / n * 100,
    }


# ─── Output ───────────────────────────────────────────────────────────


def print_grid(results: list[dict]):
    """Print full grid sorted by ROI."""
    print(f"\n  {'Alloc%':>6} {'Lev':>4} {'MaxPos':>6} {'Taken':>6} {'Skip':>5} "
          f"{'Final$':>12} {'ROI':>10} {'MaxDD':>8} {'Liqs':>5} {'ROI/DD':>7}")
    print("  " + "-" * 82)

    sorted_r = sorted(results, key=lambda r: -r["roi"])
    for r in sorted_r:
        roi_dd = f"{r['roi_dd_ratio']:.1f}" if r['roi_dd_ratio'] < 1e6 else "inf"
        print(f"  {r['alloc_pct']:>5}% {r['leverage']:>3}x {r['max_concurrent']:>6} "
              f"{r['taken']:>6} {r['skipped']:>5} "
              f"${r['final_equity']:>11,.0f} {r['roi']:>+9.1f}% "
              f"{r['max_dd']:>7.1f}% {r['liquidations']:>5} {roi_dd:>7}")


def print_profile(name: str, r: dict):
    """Print detailed profile info."""
    print(f"\n  {'─' * 60}")
    print(f"  {name.upper()}")
    print(f"  {'─' * 60}")
    print(f"  Config:     alloc={r['alloc_pct']}%, leverage={r['leverage']}x, "
          f"max_concurrent={r['max_concurrent']}")
    print(f"  Final:      ${r['final_equity']:,.0f}  (ROI: {r['roi']:+,.1f}%)")
    print(f"  MaxDD:      {r['max_dd']:.1f}%")
    print(f"  ROI/MaxDD:  {r['roi_dd_ratio']:.1f}x")
    print(f"  Liqs:       {r['liquidations']}")
    print(f"  Trades:     {r['taken']} taken, {r['skipped']} skipped")
    print(f"  MaxConsecL: {r['max_consec_loss']}")
    print(f"  LongestDD:  {r['longest_dd_days']:.0f} days")

    # Monthly PnL
    monthly = r["monthly_pnl"]
    if monthly:
        months = sorted(monthly.keys())
        print(f"\n  Monthly PnL ($):")
        print(f"  {'Month':>10} {'PnL':>12} {'Cum':>12}")
        cum = 0.0
        for m in months:
            cum += monthly[m]
            marker = " **" if monthly[m] < 0 else ""
            print(f"  {m:>10} {monthly[m]:>+11,.0f} {cum:>+11,.0f}{marker}")

        # Yearly summary
        yearly: dict[str, float] = defaultdict(float)
        for m, pnl in monthly.items():
            yearly[m[:4]] += pnl
        print(f"\n  Yearly PnL ($):")
        for y in sorted(yearly):
            print(f"    {y}: {yearly[y]:>+11,.0f}")


# ─── Main ─────────────────────────────────────────────────────────────


async def main():
    t0 = time.time()

    print()
    print("=" * 70)
    print("  EQUITY SIMULATION — Hybrid C (HL 30 symbols)")
    print("=" * 70)

    # Step 1: Collect trades
    print("\n  Collecting Hybrid C trades...")
    trades = await collect_trades()
    print(f"  Trades: {len(trades)}")

    if not trades:
        print("  No trades found!")
        return

    wins = sum(1 for t in trades if t["net_pnl_pct"] > 0)
    avg_ev = sum(t["net_pnl_pct"] for t in trades) / len(trades)
    total_pnl = sum(t["net_pnl_pct"] for t in trades)
    print(f"  WR: {wins/len(trades)*100:.1f}%  EV: {avg_ev:+.2f}%  "
          f"Total PnL: {total_pnl:+.1f}%")

    date_range = f"{trades[0]['entry_date']} → {trades[-1]['entry_date']}"
    print(f"  Period: {date_range}")

    # Concurrent analysis
    events_c = []
    for t in trades:
        events_c.append((t["entry_ts"], +1))
        events_c.append((t["exit_ts"], -1))
    events_c.sort()
    concurrent = 0
    max_c = 0
    for _, delta in events_c:
        concurrent += delta
        max_c = max(max_c, concurrent)
    print(f"  Max concurrent positions: {max_c}")

    # Step 2: Grid simulation
    print(f"\n  Running grid: {len(ALLOC_PCTS)}×{len(LEVERAGES)}×{len(MAX_CONCURRENTS)} "
          f"= {len(ALLOC_PCTS) * len(LEVERAGES) * len(MAX_CONCURRENTS)} scenarios...")

    results = []
    for alloc in ALLOC_PCTS:
        for lev in LEVERAGES:
            for mc in MAX_CONCURRENTS:
                r = simulate(trades, alloc, lev, mc)
                results.append(r)

    print(f"  Done. ({len(results)} scenarios)")

    # Step 3: Full grid
    print("\n" + "=" * 70)
    print("  FULL GRID (sorted by ROI)")
    print("=" * 70)
    print_grid(results)

    # Step 4: Profile selection
    print("\n" + "=" * 70)
    print("  RECOMMENDED PROFILES")
    print("=" * 70)

    profiles = select_profiles(results)
    for name in ["Aggressive", "Stable", "Conservative"]:
        if name in profiles:
            print_profile(name, profiles[name])
        else:
            print(f"\n  {name}: No scenario meets criteria")

    # Step 5: Summary table
    print("\n" + "=" * 70)
    print("  PROFILE COMPARISON")
    print("=" * 70)
    print(f"\n  {'Profile':<15} {'Config':<22} {'ROI':>10} {'MaxDD':>8} "
          f"{'ROI/DD':>7} {'Liqs':>5} {'ConsecL':>8}")
    print("  " + "-" * 80)
    for name in ["Aggressive", "Stable", "Conservative"]:
        p = profiles.get(name)
        if p:
            cfg = f"{p['alloc_pct']}%×{p['leverage']}x / {p['max_concurrent']}pos"
            print(f"  {name:<15} {cfg:<22} {p['roi']:>+9.1f}% {p['max_dd']:>7.1f}% "
                  f"{p['roi_dd_ratio']:>6.1f}x {p['liquidations']:>5} {p['max_consec_loss']:>8}")

    # ═══════════════════════════════════════════════════════════════
    #  STRESS TESTS (on 3 profiles)
    # ═══════════════════════════════════════════════════════════════

    print("\n" + "=" * 70)
    print("  STRESS TEST 1: CORRELATED DRAWDOWN (flash crash)")
    print("  All open positions hit their MAE simultaneously")
    print("=" * 70)

    print(f"\n  {'Profile':<15} {'WorstDD':>8} {'StressEq':>12} {'#Open':>6} {'Ruin moments':>13}")
    print("  " + "-" * 60)
    for name in ["Aggressive", "Stable", "Conservative"]:
        p = profiles.get(name)
        if not p:
            continue
        stress = stress_correlated_drawdown(
            trades, p["alloc_pct"], p["leverage"], p["max_concurrent"])
        print(f"  {name:<15} {stress['worst_stress_dd']:>7.1f}% "
              f"${stress['worst_stress_equity']:>10,.0f} {stress['worst_n_open']:>6} "
              f"{stress['ruin_moments']:>13}")

    # ─── Stress Test 2: Slippage ─────────────────────────────────
    print("\n" + "=" * 70)
    print("  STRESS TEST 2: SLIPPAGE SENSITIVITY")
    print("  Round-trip slippage added to each trade")
    print("=" * 70)

    slippages = [0.0, 0.1, 0.2, 0.3, 0.5, 1.0]
    for name in ["Aggressive", "Stable", "Conservative"]:
        p = profiles.get(name)
        if not p:
            continue
        cfg = f"{p['alloc_pct']}%×{p['leverage']}x/{p['max_concurrent']}pos"
        print(f"\n  {name} ({cfg}):")
        print(f"  {'Slip%':>6} {'EV':>8} {'ROI':>12} {'MaxDD':>8} {'Δ ROI':>12}")
        print("  " + "-" * 52)
        base_roi = None
        slip_results = test_slippage(
            trades, p["alloc_pct"], p["leverage"], p["max_concurrent"], slippages)
        for sr in slip_results:
            if base_roi is None:
                base_roi = sr["roi"]
            delta = sr["roi"] - base_roi
            print(f"  {sr['slippage']:>5.1f}% {sr['adj_ev']:>+7.2f}% "
                  f"${sr['final_equity']:>10,.0f} {sr['max_dd']:>7.1f}% "
                  f"{delta:>+11.1f}%")

    # ─── Stress Test 3: Capacity Cap ─────────────────────────────
    print("\n" + "=" * 70)
    print("  STRESS TEST 3: CAPACITY CAP (max position notional)")
    print("  Limits position size to realistic exchange liquidity")
    print("=" * 70)

    caps = [25_000, 50_000, 100_000, 200_000, 500_000, float("inf")]
    for name in ["Aggressive", "Stable"]:
        p = profiles.get(name)
        if not p:
            continue
        cfg = f"{p['alloc_pct']}%×{p['leverage']}x/{p['max_concurrent']}pos"
        print(f"\n  {name} ({cfg}):")
        print(f"  {'Cap':>10} {'Final$':>12} {'ROI':>12} {'MaxDD':>8} {'Capped':>7}")
        print("  " + "-" * 55)
        for cap in caps:
            cr = simulate_with_cap(
                trades, p["alloc_pct"], p["leverage"], p["max_concurrent"], cap)
            cap_label = f"${cap:,.0f}" if cap < float("inf") else "none"
            print(f"  {cap_label:>10} ${cr['final_equity']:>10,.0f} "
                  f"{cr['roi']:>+11.1f}% {cr['max_dd']:>7.1f}% {cr['capped_trades']:>7}")

    # ─── Stress Test 4: Monte Carlo (Bootstrap) ────────────────
    print("\n" + "=" * 70)
    print("  STRESS TEST 4: MONTE CARLO — Bootstrap (1000 resamples)")
    print("  Sample 810 trades WITH replacement — tests edge robustness")
    print("=" * 70)

    random.seed(42)  # reproducible
    for name in ["Aggressive", "Stable", "Conservative"]:
        p = profiles.get(name)
        if not p:
            continue
        mc = monte_carlo(trades, p["alloc_pct"], p["leverage"], p["max_concurrent"])
        cfg = f"{p['alloc_pct']}%×{p['leverage']}x/{p['max_concurrent']}pos"
        print(f"\n  {name} ({cfg}):")
        print(f"    ROI distribution:")
        print(f"      P5:     {mc['p5_roi']:>+12,.1f}%")
        print(f"      P25:    {mc['p25_roi']:>+12,.1f}%")
        print(f"      Median: {mc['median_roi']:>+12,.1f}%")
        print(f"      P75:    {mc['p75_roi']:>+12,.1f}%")
        print(f"      P95:    {mc['p95_roi']:>+12,.1f}%")
        print(f"    MaxDD:   median={mc['median_dd']:.1f}%, P95={mc['p95_dd']:.1f}%, "
              f"worst={mc['max_dd']:.1f}%")
        print(f"    ConsecL: median={mc['median_consec']}, P95={mc['p95_consec']}")
        print(f"    Ruin:    {mc['ruins']}/{mc['n_runs']} ({mc['ruin_pct']:.1f}%)")

    elapsed = time.time() - t0
    print(f"\n  Total time: {elapsed:.0f}s")
    print()


if __name__ == "__main__":
    asyncio.run(main())
