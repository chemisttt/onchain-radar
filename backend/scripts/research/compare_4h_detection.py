#!/usr/bin/env python3
"""Compare daily vs 4h signal detection.

Runs both simulate_alerts (daily detection) and simulate_alerts_4h_detection
(4h detection, same thresholds) side by side. Prints per-signal-type and
overall comparison.

Usage:
  cd backend && python3 scripts/compare_4h_detection.py
"""

import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import init_db
from services.backtest_service import simulate_alerts, simulate_alerts_4h_detection

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "TRXUSDT", "MATICUSDT", "UNIUSDT", "SHIBUSDT", "LTCUSDT",
    "BCHUSDT", "ATOMUSDT", "NEARUSDT", "FILUSDT", "APTUSDT",
    "ARBUSDT", "OPUSDT", "MKRUSDT", "AAVEUSDT", "LDOUSDT",
    "INJUSDT", "SUIUSDT", "TIAUSDT", "WIFUSDT", "JUPUSDT",
]

TOP_OI = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "DOGEUSDT", "TRXUSDT", "UNIUSDT", "SUIUSDT", "ADAUSDT",
}

GLOBAL_DAILY_CAP = 5


def _simulate_mfe_trade(alert: dict, tp_pct: float, sl_pct: float) -> dict | None:
    """MFE/MAE-based trade simulation."""
    mfe = alert.get("mfe_return")
    mae = alert.get("mae_return")
    if mfe is None or mae is None:
        return None

    hit_tp = mfe >= tp_pct
    hit_sl = mae >= sl_pct

    if hit_tp and not hit_sl:
        return {"result": "WIN", "pnl": tp_pct}
    if hit_sl and not hit_tp:
        return {"result": "LOSS", "pnl": -sl_pct}
    if hit_tp and hit_sl:
        if mfe > mae * 1.5:
            return {"result": "WIN", "pnl": tp_pct}
        return {"result": "LOSS", "pnl": -sl_pct}

    r = alert.get("return_7d")
    if r is None:
        return {"result": "TIMEOUT", "pnl": 0}
    adj_r = -r if alert["direction"] == "short" else r
    return {"result": "TIMEOUT", "pnl": adj_r}


def _eval_group(alerts: list[dict], tp: float = 5, sl: float = 3) -> dict:
    """Evaluate with MFE-based exits."""
    wins, losses, timeouts = 0, 0, 0
    total_pnl = 0.0

    for a in alerts:
        trade = _simulate_mfe_trade(a, tp, sl)
        if trade is None:
            continue
        if trade["result"] == "WIN":
            wins += 1
        elif trade["result"] == "LOSS":
            losses += 1
        else:
            timeouts += 1
        total_pnl += trade["pnl"]

    total = wins + losses + timeouts
    if total == 0:
        return {"total": 0, "wins": 0, "losses": 0, "timeouts": 0,
                "wr": 0, "ev": 0, "total_pnl": 0, "pf": 0}

    resolved = wins + losses
    wr = wins / resolved * 100 if resolved > 0 else 0
    ev = total_pnl / total
    gross_win = wins * tp
    gross_loss = losses * sl
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    return {
        "total": total, "wins": wins, "losses": losses, "timeouts": timeouts,
        "wr": wr, "ev": ev, "total_pnl": total_pnl, "pf": pf,
    }


def _apply_global_cap(all_alerts: list[dict]) -> list[dict]:
    """Apply global daily cap (top N by confluence per day)."""
    by_day = defaultdict(list)
    for a in all_alerts:
        day = a["fired_at"][:10]
        by_day[day].append(a)
    result = []
    for day, day_alerts in by_day.items():
        day_alerts.sort(key=lambda x: -x["confluence"])
        result.extend(day_alerts[:GLOBAL_DAILY_CAP])
    return result


async def main():
    await init_db()

    print("\n" + "=" * 100)
    print("  DAILY vs 4H SIGNAL DETECTION COMPARISON")
    print("=" * 100)

    # ── Collect daily signals ──
    print("\n  Collecting daily detection signals...")
    daily_alerts = []
    for sym in SYMBOLS:
        alerts = await simulate_alerts(sym, days=1095)
        for a in alerts:
            a["symbol"] = sym
        daily_alerts.extend(alerts)
    daily_alerts = _apply_global_cap(daily_alerts)
    print(f"  Daily: {len(daily_alerts)} signals")

    # ── Collect 4h detection signals ──
    print("  Collecting 4h detection signals...")
    alerts_4h = []
    for sym in SYMBOLS:
        alerts = await simulate_alerts_4h_detection(sym, days=1095)
        for a in alerts:
            a["symbol"] = sym
        alerts_4h.extend(alerts)
    alerts_4h = _apply_global_cap(alerts_4h)
    print(f"  4h:    {len(alerts_4h)} signals")

    # ── Check MFE coverage ──
    daily_mfe = sum(1 for a in daily_alerts if a.get("mfe_return") is not None)
    h4_mfe = sum(1 for a in alerts_4h if a.get("mfe_return") is not None)
    print(f"\n  MFE coverage: daily {daily_mfe}/{len(daily_alerts)}, "
          f"4h {h4_mfe}/{len(alerts_4h)}")

    # ═══════════════════════════════════════════════════════════════
    #  Overall comparison (TP=5% / SL=3%)
    # ═══════════════════════════════════════════════════════════════
    TP, SL = 5, 3

    print()
    print("=" * 100)
    print(f"  OVERALL (TP={TP}% / SL={SL}%)")
    print("=" * 100)
    print(f"  {'Detection':<12} {'N':>5} {'WR':>7} {'W':>5} {'L':>5} {'T/O':>5} "
          f"{'EV':>8} {'PF':>6} {'PnL':>8}")
    print("  " + "-" * 75)

    for label, group in [("Daily", daily_alerts), ("4h", alerts_4h)]:
        r = _eval_group(group, TP, SL)
        print(f"  {label:<12} {r['total']:>5} {r['wr']:>6.1f}% {r['wins']:>5} "
              f"{r['losses']:>5} {r['timeouts']:>5} {r['ev']:>+7.2f}% "
              f"{r['pf']:>5.2f}x {r['total_pnl']:>+7.1f}%")

    # ═══════════════════════════════════════════════════════════════
    #  Per-signal-type comparison
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print(f"  PER SIGNAL TYPE (TP={TP}% / SL={SL}%)")
    print("=" * 100)
    print(f"  {'Signal':<22} {'D.N':>4} {'D.WR':>6} {'D.EV':>7} {'│':>1} "
          f"{'4h.N':>5} {'4h.WR':>6} {'4h.EV':>7} {'│':>1} {'Δ EV':>7}")
    print("  " + "-" * 85)

    # Get all signal types from both
    daily_by_type = defaultdict(list)
    for a in daily_alerts:
        daily_by_type[a["type"]].append(a)
    h4_by_type = defaultdict(list)
    for a in alerts_4h:
        h4_by_type[a["type"]].append(a)

    all_types = sorted(set(list(daily_by_type.keys()) + list(h4_by_type.keys())))

    for sig_type in all_types:
        d_group = daily_by_type.get(sig_type, [])
        h_group = h4_by_type.get(sig_type, [])
        d_r = _eval_group(d_group, TP, SL)
        h_r = _eval_group(h_group, TP, SL)

        d_n = d_r["total"]
        d_wr = f"{d_r['wr']:.1f}%" if d_n > 0 else "  -  "
        d_ev = f"{d_r['ev']:+.2f}%" if d_n > 0 else "   -  "
        h_n = h_r["total"]
        h_wr = f"{h_r['wr']:.1f}%" if h_n > 0 else "  -  "
        h_ev = f"{h_r['ev']:+.2f}%" if h_n > 0 else "   -  "

        delta = ""
        if d_n > 0 and h_n > 0:
            delta = f"{h_r['ev'] - d_r['ev']:+.2f}%"

        print(f"  {sig_type:<22} {d_n:>4} {d_wr:>6} {d_ev:>7} │ "
              f"{h_n:>5} {h_wr:>6} {h_ev:>7} │ {delta:>7}")

    # ═══════════════════════════════════════════════════════════════
    #  Per-tier comparison
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print(f"  PER TIER (TP={TP}% / SL={SL}%)")
    print("=" * 100)

    for tier_name in ["SETUP", "SIGNAL"]:
        d_tier = [a for a in daily_alerts if a["tier"] == tier_name]
        h_tier = [a for a in alerts_4h if a["tier"] == tier_name]
        d_r = _eval_group(d_tier, TP, SL)
        h_r = _eval_group(h_tier, TP, SL)
        print(f"  {tier_name:<8} Daily: {d_r['total']:>4} signals, WR={d_r['wr']:.1f}%, EV={d_r['ev']:+.2f}%")
        print(f"  {'':<8} 4h:    {h_r['total']:>4} signals, WR={h_r['wr']:.1f}%, EV={h_r['ev']:+.2f}%")

    # ═══════════════════════════════════════════════════════════════
    #  Direction breakdown
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print(f"  DIRECTION BREAKDOWN (TP={TP}% / SL={SL}%)")
    print("=" * 100)

    for dir_name in ["long", "short"]:
        d_dir = [a for a in daily_alerts if a["direction"] == dir_name]
        h_dir = [a for a in alerts_4h if a["direction"] == dir_name]
        d_r = _eval_group(d_dir, TP, SL)
        h_r = _eval_group(h_dir, TP, SL)
        print(f"  {dir_name.upper():<8} Daily: {d_r['total']:>4}, WR={d_r['wr']:.1f}%, EV={d_r['ev']:+.2f}%  |  "
              f"4h: {h_r['total']:>4}, WR={h_r['wr']:.1f}%, EV={h_r['ev']:+.2f}%")

    # ═══════════════════════════════════════════════════════════════
    #  TOP 10 vs ALTS
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print(f"  TOP 10 OI vs ALTS (TP={TP}% / SL={SL}%)")
    print("=" * 100)

    for label, filt in [("TOP 10", lambda a: a["symbol"] in TOP_OI),
                         ("ALTS", lambda a: a["symbol"] not in TOP_OI)]:
        d_grp = [a for a in daily_alerts if filt(a)]
        h_grp = [a for a in alerts_4h if filt(a)]
        d_r = _eval_group(d_grp, TP, SL)
        h_r = _eval_group(h_grp, TP, SL)
        print(f"  {label:<8} Daily: {d_r['total']:>4}, WR={d_r['wr']:.1f}%, EV={d_r['ev']:+.2f}%  |  "
              f"4h: {h_r['total']:>4}, WR={h_r['wr']:.1f}%, EV={h_r['ev']:+.2f}%")

    # ═══════════════════════════════════════════════════════════════
    #  Signal timeline distribution
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print("  SIGNAL TIMELINE (per quarter)")
    print("=" * 100)

    def _quarter(fired_at: str) -> str:
        y = fired_at[:4]
        m = int(fired_at[5:7])
        q = (m - 1) // 3 + 1
        return f"{y}-Q{q}"

    d_by_q = defaultdict(int)
    h_by_q = defaultdict(int)
    for a in daily_alerts:
        d_by_q[_quarter(a["fired_at"])] += 1
    for a in alerts_4h:
        h_by_q[_quarter(a["fired_at"])] += 1

    all_quarters = sorted(set(list(d_by_q.keys()) + list(h_by_q.keys())))
    print(f"  {'Quarter':<10} {'Daily':>6} {'4h':>6} {'Ratio':>8}")
    print("  " + "-" * 35)
    for q in all_quarters:
        d_n = d_by_q.get(q, 0)
        h_n = h_by_q.get(q, 0)
        ratio = f"{h_n/d_n:.1f}x" if d_n > 0 else "  -  "
        print(f"  {q:<10} {d_n:>6} {h_n:>6} {ratio:>8}")

    # ═══════════════════════════════════════════════════════════════
    #  Verdict
    # ═══════════════════════════════════════════════════════════════
    d_overall = _eval_group(daily_alerts, TP, SL)
    h_overall = _eval_group(alerts_4h, TP, SL)

    print()
    print("=" * 100)
    print("  VERDICT")
    print("=" * 100)

    n_ratio = h_overall["total"] / d_overall["total"] if d_overall["total"] > 0 else 0
    ev_delta = h_overall["ev"] - d_overall["ev"]

    print(f"  Daily:  {d_overall['total']} signals, WR {d_overall['wr']:.1f}%, EV {d_overall['ev']:+.2f}%")
    print(f"  4h:     {h_overall['total']} signals, WR {h_overall['wr']:.1f}%, EV {h_overall['ev']:+.2f}%")
    print(f"  Ratio:  {n_ratio:.1f}x signals, EV delta {ev_delta:+.2f}%")
    print()

    if h_overall["ev"] >= d_overall["ev"] and n_ratio > 1:
        print("  → MORE signals + SAME/BETTER EV — worth tuning thresholds")
    elif n_ratio > 1 and ev_delta < -0.5:
        print("  → MORE signals but EV DROPPED — noise, not worth it")
    elif n_ratio < 1 and h_overall["ev"] > d_overall["ev"]:
        print("  → FEWER signals but HIGHER EV — interesting filter")
    else:
        print("  → Mixed results — analyze per-type breakdown for specifics")

    # ═══════════════════════════════════════════════════════════════
    #  DEEP ANALYSIS
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print("  DEEP ANALYSIS")
    print("=" * 100)

    def _stats(alerts, label):
        r = _eval_group(alerts, TP, SL)
        if r["total"] == 0:
            print(f"  {label:<45} N=   0")
            return
        print(f"  {label:<45} N={r['total']:>4}  WR={r['wr']:>5.1f}%  EV={r['ev']:>+6.2f}%")

    # 1. Tier filter
    print("\n--- 1. TIER FILTER ---")
    _stats(daily_alerts, "Daily (all tiers)")
    _stats(alerts_4h, "4h (all tiers)")
    _stats([a for a in alerts_4h if a["tier"] == "SIGNAL"], "4h SIGNAL-only")
    _stats([a for a in alerts_4h if a["tier"] == "SETUP"], "4h SETUP-only")

    # 2. Cherry-pick improved signals
    print("\n--- 2. CHERRY-PICK (improved on 4h) ---")
    improved = {"liq_short_squeeze", "momentum_divergence"}
    _stats([a for a in daily_alerts if a["type"] in improved], "Daily (lss + mom_div)")
    _stats([a for a in alerts_4h if a["type"] in improved], "4h (lss + mom_div)")

    # 3. Signal density
    print("\n--- 3. SIGNAL DENSITY ---")
    h4_by_sym_day = defaultdict(lambda: defaultdict(int))
    for a in alerts_4h:
        h4_by_sym_day[a.get("symbol", "?")][a["fired_at"][:10]] += 1
    multi = sum(1 for sym in h4_by_sym_day for d, c in h4_by_sym_day[sym].items() if c > 1)
    total_days = sum(len(d) for d in h4_by_sym_day.values())
    print(f"  4h: {multi}/{total_days} symbol-days have >1 signal ({multi/total_days*100:.1f}%)")

    d_by_sym_day = defaultdict(lambda: defaultdict(int))
    for a in daily_alerts:
        d_by_sym_day[a.get("symbol", "?")][a["fired_at"][:10]] += 1
    multi_d = sum(1 for sym in d_by_sym_day for d, c in d_by_sym_day[sym].items() if c > 1)
    total_d = sum(len(d) for d in d_by_sym_day.values())
    print(f"  Daily: {multi_d}/{total_d} symbol-days have >1 signal ({multi_d/total_d*100:.1f}%)")

    # 4. 2025-Q2/Q3 anomaly
    print("\n--- 4. 2025-Q2/Q3 ANOMALY ---")
    anomaly_set = set()
    for a in alerts_4h:
        y = a["fired_at"][:4]
        m = int(a["fired_at"][5:7])
        q = f"{y}-Q{(m-1)//3+1}"
        if q in {"2025-Q2", "2025-Q3"}:
            anomaly_set.add(id(a))
    h4_anomaly = [a for a in alerts_4h if id(a) in anomaly_set]
    h4_clean = [a for a in alerts_4h if id(a) not in anomaly_set]
    _stats(h4_anomaly, "4h anomaly quarters (Q2+Q3 2025)")
    _stats(h4_clean, "4h WITHOUT anomaly quarters")
    anomaly_types = defaultdict(int)
    for a in h4_anomaly:
        anomaly_types[a["type"]] += 1
    print(f"  Anomaly by type: {dict(sorted(anomaly_types.items(), key=lambda x:-x[1]))}")

    # 5. Higher confluence
    print("\n--- 5. HIGHER CONFLUENCE ---")
    _stats([a for a in alerts_4h if a["confluence"] >= 5], "4h confluence >= 5")
    _stats([a for a in alerts_4h if a["confluence"] >= 6], "4h confluence >= 6")

    # 6. Best combos
    print("\n--- 6. BEST COMBOS ---")
    _stats([a for a in alerts_4h if a["tier"] == "SIGNAL" and id(a) not in anomaly_set],
           "4h SIGNAL + no anomaly Q")
    good_4h = {"liq_short_squeeze", "momentum_divergence", "div_squeeze_3d",
               "div_top_1d", "fund_spike", "capitulation"}
    _stats([a for a in alerts_4h if a["type"] in good_4h], "4h only positive-EV types")
    _stats([a for a in daily_alerts if a["type"] in good_4h], "Daily same types (compare)")

    # ═══════════════════════════════════════════════════════════════
    #  HYBRID ANALYSIS
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print("  HYBRID: 4h FOR SELECT TYPES + DAILY FOR REST")
    print("=" * 100)

    # Types that showed positive EV on 4h AND improved vs daily
    h4_types_positive = {"liq_short_squeeze", "momentum_divergence", "div_squeeze_3d",
                         "div_top_1d", "fund_spike", "capitulation"}
    daily_only_types = set(t for t in all_types) - h4_types_positive

    def _build_hybrid(h4_types: set, daily_all, h4_all):
        """Take h4_types from 4h, rest from daily. Dedup by symbol+day+direction."""
        hybrid = []
        # Add 4h signals for selected types
        for a in h4_all:
            if a["type"] in h4_types:
                hybrid.append(a)
        # Add daily signals for remaining types
        for a in daily_all:
            if a["type"] not in h4_types:
                hybrid.append(a)

        # Dedup: if same symbol+day+direction exists from both sources, keep higher confluence
        seen: dict[str, dict] = {}
        for a in hybrid:
            key = f"{a.get('symbol','?')}:{a['fired_at'][:10]}:{a['direction']}"
            if key not in seen or a["confluence"] > seen[key]["confluence"]:
                seen[key] = a
        deduped = list(seen.values())

        # Re-apply global daily cap
        by_day = defaultdict(list)
        for a in deduped:
            by_day[a["fired_at"][:10]].append(a)
        capped = []
        for day, day_als in by_day.items():
            day_als.sort(key=lambda x: -x["confluence"])
            capped.extend(day_als[:GLOBAL_DAILY_CAP])
        return capped

    # Variant A: 6 positive types from 4h
    hybrid_a = _build_hybrid(h4_types_positive, daily_alerts, alerts_4h)
    _stats(daily_alerts, "Baseline daily (all types)")
    _stats(hybrid_a, "Hybrid A: 6 types 4h + rest daily")

    # Variant B: only the 2 clearly improved types from 4h
    h4_types_best2 = {"liq_short_squeeze", "momentum_divergence"}
    hybrid_b = _build_hybrid(h4_types_best2, daily_alerts, alerts_4h)
    _stats(hybrid_b, "Hybrid B: lss+mom_div 4h + rest daily")

    # Variant C: 4 best types (top improvement)
    h4_types_4 = {"liq_short_squeeze", "momentum_divergence", "div_squeeze_3d", "div_top_1d"}
    hybrid_c = _build_hybrid(h4_types_4, daily_alerts, alerts_4h)
    _stats(hybrid_c, "Hybrid C: 4 types 4h + rest daily")

    # Per-type breakdown for best hybrid
    print()
    print("--- HYBRID A: PER TYPE ---")
    print(f"  {'Signal':<22} {'N':>4} {'WR':>6} {'EV':>7} {'src':<5}")
    print("  " + "-" * 50)
    hybrid_by_type = defaultdict(list)
    for a in hybrid_a:
        hybrid_by_type[a["type"]].append(a)
    for sig_type in sorted(hybrid_by_type.keys()):
        group = hybrid_by_type[sig_type]
        r = _eval_group(group, TP, SL)
        if r["total"] == 0:
            continue
        src = "4h" if sig_type in h4_types_positive else "daily"
        print(f"  {sig_type:<22} {r['total']:>4} {r['wr']:>5.1f}% {r['ev']:>+6.2f}% {src:<5}")

    # Direction breakdown for hybrid
    print()
    print("--- HYBRID A: DIRECTION ---")
    for dir_name in ["long", "short"]:
        h_dir = [a for a in hybrid_a if a["direction"] == dir_name]
        d_dir = [a for a in daily_alerts if a["direction"] == dir_name]
        h_r = _eval_group(h_dir, TP, SL)
        d_r = _eval_group(d_dir, TP, SL)
        print(f"  {dir_name.upper():<8} Daily: {d_r['total']:>4}, EV={d_r['ev']:+.2f}%  |  "
              f"Hybrid: {h_r['total']:>4}, EV={h_r['ev']:+.2f}%")

    # Timeline for hybrid
    print()
    print("--- HYBRID A: TIMELINE ---")
    h_by_q = defaultdict(int)
    for a in hybrid_a:
        y = a["fired_at"][:4]
        m = int(a["fired_at"][5:7])
        h_by_q[f"{y}-Q{(m-1)//3+1}"] += 1
    all_q = sorted(set(list(d_by_q.keys()) + list(h_by_q.keys())))
    print(f"  {'Quarter':<10} {'Daily':>6} {'Hybrid':>7} {'Ratio':>7}")
    print("  " + "-" * 35)
    for q in all_q:
        d_n = d_by_q.get(q, 0)
        h_n = h_by_q.get(q, 0)
        ratio = f"{h_n/d_n:.1f}x" if d_n > 0 else "  -  "
        print(f"  {q:<10} {d_n:>6} {h_n:>7} {ratio:>7}")

    # ═══════════════════════════════════════════════════════════════
    #  FINAL VERDICT
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print("  FINAL VERDICT")
    print("=" * 100)
    d_r = _eval_group(daily_alerts, TP, SL)
    ha_r = _eval_group(hybrid_a, TP, SL)
    hb_r = _eval_group(hybrid_b, TP, SL)
    hc_r = _eval_group(hybrid_c, TP, SL)
    h4_r = _eval_group(alerts_4h, TP, SL)
    print(f"  {'Config':<40} {'N':>5} {'WR':>6} {'EV':>7} {'PnL':>8}")
    print("  " + "-" * 70)
    print(f"  {'Daily baseline':<40} {d_r['total']:>5} {d_r['wr']:>5.1f}% {d_r['ev']:>+6.2f}% {d_r['total_pnl']:>+7.1f}%")
    print(f"  {'4h full':<40} {h4_r['total']:>5} {h4_r['wr']:>5.1f}% {h4_r['ev']:>+6.2f}% {h4_r['total_pnl']:>+7.1f}%")
    print(f"  {'Hybrid A (6 types 4h)':<40} {ha_r['total']:>5} {ha_r['wr']:>5.1f}% {ha_r['ev']:>+6.2f}% {ha_r['total_pnl']:>+7.1f}%")
    print(f"  {'Hybrid B (lss+mom_div 4h)':<40} {hb_r['total']:>5} {hb_r['wr']:>5.1f}% {hb_r['ev']:>+6.2f}% {hb_r['total_pnl']:>+7.1f}%")
    print(f"  {'Hybrid C (4 types 4h)':<40} {hc_r['total']:>5} {hc_r['wr']:>5.1f}% {hc_r['ev']:>+6.2f}% {hc_r['total_pnl']:>+7.1f}%")
    print()


asyncio.run(main())
