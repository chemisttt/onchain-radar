#!/usr/bin/env python3
"""Backtest comparison: current 30 symbols vs HL 30 symbols.

Runs three configs:
  1. Old 30 symbols + daily detection (current baseline)
  2. HL 30 symbols + daily detection (symbol change impact)
  3. HL 30 symbols + Hybrid C (4 types on 4h + rest on daily)

Usage:
  cd backend && python3 scripts/compare_hl_symbols.py
"""

import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import init_db
from services.backtest_service import simulate_alerts, simulate_alerts_4h_detection

# Current production symbols
OLD_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT", "AAVEUSDT", "DOTUSDT",
    "FILUSDT", "ATOMUSDT", "TRXUSDT", "JUPUSDT", "SEIUSDT", "TIAUSDT",
    "INJUSDT", "TRUMPUSDT", "WIFUSDT", "TONUSDT", "RENDERUSDT", "ENAUSDT",
]

# HL symbol set (drop RENDER/FIL/ATOM/TIA, add HYPE/ZEC/TAO/WLD)
HL_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT", "AAVEUSDT", "DOTUSDT",
    "TRXUSDT", "TONUSDT", "ENAUSDT", "TRUMPUSDT", "WIFUSDT",
    "JUPUSDT", "INJUSDT", "SEIUSDT",
    "HYPEUSDT", "ZECUSDT", "TAOUSDT", "WLDUSDT",
]

# Hybrid C: these types use 4h detection, rest use daily
HYBRID_C_4H_TYPES = {"liq_short_squeeze", "momentum_divergence", "div_top_1d"}

TOP_OI = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "DOGEUSDT", "TRXUSDT", "UNIUSDT", "SUIUSDT", "ADAUSDT",
}

GLOBAL_DAILY_CAP = 5
TP, SL = 5, 3


def _simulate_mfe_trade(alert, tp_pct, sl_pct):
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
        return {"result": "WIN", "pnl": tp_pct} if mfe > mae * 1.5 else {"result": "LOSS", "pnl": -sl_pct}
    r = alert.get("return_7d")
    if r is None:
        return {"result": "TIMEOUT", "pnl": 0}
    adj_r = -r if alert["direction"] == "short" else r
    return {"result": "TIMEOUT", "pnl": adj_r}


def _eval_group(alerts, tp=5, sl=3):
    wins = losses = timeouts = 0
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
    pf = (wins * tp) / (losses * sl) if losses > 0 else float("inf")
    return {"total": total, "wins": wins, "losses": losses, "timeouts": timeouts,
            "wr": wr, "ev": ev, "total_pnl": total_pnl, "pf": pf}


def _apply_global_cap(all_alerts):
    by_day = defaultdict(list)
    for a in all_alerts:
        by_day[a["fired_at"][:10]].append(a)
    result = []
    for day, day_alerts in by_day.items():
        day_alerts.sort(key=lambda x: -x["confluence"])
        result.extend(day_alerts[:GLOBAL_DAILY_CAP])
    return result


def _print_stats(label, alerts, indent=2):
    r = _eval_group(alerts, TP, SL)
    if r["total"] == 0:
        print(f"{' '*indent}{label:<45} N=   0")
        return r
    print(f"{' '*indent}{label:<45} N={r['total']:>4}  WR={r['wr']:>5.1f}%  "
          f"EV={r['ev']:>+6.2f}%  PF={r['pf']:>5.2f}x  PnL={r['total_pnl']:>+7.1f}%")
    return r


async def collect_daily(symbols, label=""):
    """Collect daily detection signals for given symbols."""
    alerts = []
    for sym in symbols:
        try:
            sa = await simulate_alerts(sym, days=1095)
            for a in sa:
                a["symbol"] = sym
            alerts.extend(sa)
        except Exception as e:
            print(f"  WARN: {sym} daily: {e}")
    alerts = _apply_global_cap(alerts)
    if label:
        print(f"  {label}: {len(alerts)} signals")
    return alerts


async def collect_4h(symbols, label=""):
    """Collect 4h detection signals for given symbols."""
    alerts = []
    for sym in symbols:
        try:
            sa = await simulate_alerts_4h_detection(sym, days=1095)
            for a in sa:
                a["symbol"] = sym
            alerts.extend(sa)
        except Exception as e:
            print(f"  WARN: {sym} 4h: {e}")
    alerts = _apply_global_cap(alerts)
    if label:
        print(f"  {label}: {len(alerts)} signals")
    return alerts


def build_hybrid_c(daily_alerts, h4_alerts):
    """Take HYBRID_C_4H_TYPES from 4h, rest from daily. Dedup + cap."""
    hybrid = []
    for a in h4_alerts:
        if a["type"] in HYBRID_C_4H_TYPES:
            hybrid.append(a)
    for a in daily_alerts:
        if a["type"] not in HYBRID_C_4H_TYPES:
            hybrid.append(a)

    # Dedup by symbol+day+direction (keep higher confluence)
    seen = {}
    for a in hybrid:
        key = f"{a.get('symbol','?')}:{a['fired_at'][:10]}:{a['direction']}"
        if key not in seen or a["confluence"] > seen[key]["confluence"]:
            seen[key] = a
    return _apply_global_cap(list(seen.values()))


async def main():
    await init_db()

    print("\n" + "=" * 100)
    print("  HL SYMBOL SET BACKTEST COMPARISON")
    print("=" * 100)

    # ── Collect signals ──
    print("\n  Collecting signals...")

    # 1. Old symbols, daily detection
    old_daily = await collect_daily(OLD_SYMBOLS, "Old 30 daily")

    # 2. HL symbols, daily detection
    hl_daily = await collect_daily(HL_SYMBOLS, "HL 30 daily")

    # 3. HL symbols, 4h detection (for Hybrid C)
    hl_4h = await collect_4h(HL_SYMBOLS, "HL 30 4h")

    # 4. Build Hybrid C
    hl_hybrid_c = build_hybrid_c(hl_daily, hl_4h)
    print(f"  HL Hybrid C: {len(hl_hybrid_c)} signals")

    # ═══════════════════════════════════════════════════════════════
    #  OVERALL COMPARISON
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print(f"  OVERALL (TP={TP}% / SL={SL}%)")
    print("=" * 100)

    _print_stats("Old 30 + daily (baseline)", old_daily)
    _print_stats("HL 30 + daily", hl_daily)
    _print_stats("HL 30 + Hybrid C", hl_hybrid_c)

    # ═══════════════════════════════════════════════════════════════
    #  DROPPED vs ADDED SYMBOLS
    # ═══════════════════════════════════════════════════════════════
    dropped = {"RENDERUSDT", "FILUSDT", "ATOMUSDT", "TIAUSDT"}
    added = {"HYPEUSDT", "ZECUSDT", "TAOUSDT", "WLDUSDT"}

    print()
    print("=" * 100)
    print("  DROPPED SYMBOLS (in old baseline)")
    print("=" * 100)
    for sym in sorted(dropped):
        sym_alerts = [a for a in old_daily if a["symbol"] == sym]
        _print_stats(sym, sym_alerts)

    print()
    print("=" * 100)
    print("  ADDED SYMBOLS (in HL set)")
    print("=" * 100)
    for sym in sorted(added):
        sym_daily = [a for a in hl_daily if a["symbol"] == sym]
        sym_hybrid = [a for a in hl_hybrid_c if a["symbol"] == sym]
        r_d = _print_stats(f"{sym} (daily)", sym_daily)
        r_h = _print_stats(f"{sym} (hybrid C)", sym_hybrid)

    # ═══════════════════════════════════════════════════════════════
    #  PER SIGNAL TYPE
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print(f"  PER SIGNAL TYPE (TP={TP}% / SL={SL}%)")
    print("=" * 100)
    print(f"  {'Signal':<22} {'Old.N':>5} {'Old.EV':>7} │ {'HL.D.N':>6} {'HL.D.EV':>7} │ "
          f"{'HC.N':>5} {'HC.EV':>7} │ {'Δ':>6}")
    print("  " + "-" * 90)

    old_by_type = defaultdict(list)
    for a in old_daily:
        old_by_type[a["type"]].append(a)
    hl_d_by_type = defaultdict(list)
    for a in hl_daily:
        hl_d_by_type[a["type"]].append(a)
    hc_by_type = defaultdict(list)
    for a in hl_hybrid_c:
        hc_by_type[a["type"]].append(a)

    all_types = sorted(set(list(old_by_type.keys()) + list(hl_d_by_type.keys()) + list(hc_by_type.keys())))
    for sig_type in all_types:
        o = _eval_group(old_by_type.get(sig_type, []))
        d = _eval_group(hl_d_by_type.get(sig_type, []))
        h = _eval_group(hc_by_type.get(sig_type, []))
        o_ev = f"{o['ev']:+.2f}%" if o["total"] > 0 else "   -  "
        d_ev = f"{d['ev']:+.2f}%" if d["total"] > 0 else "   -  "
        h_ev = f"{h['ev']:+.2f}%" if h["total"] > 0 else "   -  "
        delta = ""
        if o["total"] > 0 and h["total"] > 0:
            delta = f"{h['ev'] - o['ev']:+.2f}%"
        src = "4h" if sig_type in HYBRID_C_4H_TYPES else "D"
        print(f"  {sig_type:<22} {o['total']:>5} {o_ev:>7} │ {d['total']:>6} {d_ev:>7} │ "
              f"{h['total']:>5} {h_ev:>7} │ {delta:>6} [{src}]")

    # ═══════════════════════════════════════════════════════════════
    #  DIRECTION
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print("  DIRECTION BREAKDOWN")
    print("=" * 100)
    for dir_name in ["long", "short"]:
        o = _eval_group([a for a in old_daily if a["direction"] == dir_name])
        d = _eval_group([a for a in hl_daily if a["direction"] == dir_name])
        h = _eval_group([a for a in hl_hybrid_c if a["direction"] == dir_name])
        print(f"  {dir_name.upper():<6}  Old: {o['total']:>4} EV={o['ev']:+.2f}%  │  "
              f"HL daily: {d['total']:>4} EV={d['ev']:+.2f}%  │  "
              f"Hybrid C: {h['total']:>4} EV={h['ev']:+.2f}%")

    # ═══════════════════════════════════════════════════════════════
    #  TOP 10 vs ALTS
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print("  TOP 10 OI vs ALTS")
    print("=" * 100)
    for label, filt in [("TOP 10", lambda a: a["symbol"] in TOP_OI),
                         ("ALTS", lambda a: a["symbol"] not in TOP_OI)]:
        o = _eval_group([a for a in old_daily if filt(a)])
        d = _eval_group([a for a in hl_daily if filt(a)])
        h = _eval_group([a for a in hl_hybrid_c if filt(a)])
        print(f"  {label:<8} Old: {o['total']:>4} EV={o['ev']:+.2f}%  │  "
              f"HL daily: {d['total']:>4} EV={d['ev']:+.2f}%  │  "
              f"Hybrid C: {h['total']:>4} EV={h['ev']:+.2f}%")

    # ═══════════════════════════════════════════════════════════════
    #  TIMELINE
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print("  TIMELINE (per quarter)")
    print("=" * 100)

    def _q(a):
        y = a["fired_at"][:4]
        m = int(a["fired_at"][5:7])
        return f"{y}-Q{(m-1)//3+1}"

    o_q = defaultdict(list)
    for a in old_daily: o_q[_q(a)].append(a)
    h_q = defaultdict(list)
    for a in hl_hybrid_c: h_q[_q(a)].append(a)

    all_quarters = sorted(set(list(o_q.keys()) + list(h_q.keys())))
    print(f"  {'Quarter':<10} {'Old N':>6} {'Old EV':>7} │ {'HC N':>6} {'HC EV':>7}")
    print("  " + "-" * 45)
    for q in all_quarters:
        o = _eval_group(o_q.get(q, []))
        h = _eval_group(h_q.get(q, []))
        o_ev = f"{o['ev']:+.2f}%" if o["total"] > 0 else "   -  "
        h_ev = f"{h['ev']:+.2f}%" if h["total"] > 0 else "   -  "
        print(f"  {q:<10} {o['total']:>6} {o_ev:>7} │ {h['total']:>6} {h_ev:>7}")

    # ═══════════════════════════════════════════════════════════════
    #  SYMBOL-LEVEL PERFORMANCE (HL set)
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print("  PER-SYMBOL PERFORMANCE (HL Hybrid C)")
    print("=" * 100)
    print(f"  {'Symbol':<12} {'N':>4} {'WR':>6} {'EV':>7} {'PnL':>8}")
    print("  " + "-" * 45)

    sym_data = []
    for sym in HL_SYMBOLS:
        sym_alerts = [a for a in hl_hybrid_c if a["symbol"] == sym]
        r = _eval_group(sym_alerts)
        if r["total"] > 0:
            sym_data.append((sym, r))

    sym_data.sort(key=lambda x: -x[1]["ev"])
    for sym, r in sym_data:
        marker = " **NEW**" if sym in added else ""
        print(f"  {sym:<12} {r['total']:>4} {r['wr']:>5.1f}% {r['ev']:>+6.2f}% "
              f"{r['total_pnl']:>+7.1f}%{marker}")

    no_signals = [s for s in HL_SYMBOLS if not any(a["symbol"] == s for a in hl_hybrid_c)]
    if no_signals:
        print(f"\n  No signals: {', '.join(no_signals)}")

    # ═══════════════════════════════════════════════════════════════
    #  FINAL VERDICT
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 100)
    print("  FINAL VERDICT")
    print("=" * 100)
    print(f"  {'Config':<45} {'N':>5} {'WR':>6} {'EV':>7} {'PF':>6} {'PnL':>8}")
    print("  " + "-" * 80)

    for label, alerts in [
        ("Old 30 + daily (current baseline)", old_daily),
        ("HL 30 + daily", hl_daily),
        ("HL 30 + Hybrid C (CANDIDATE)", hl_hybrid_c),
    ]:
        r = _eval_group(alerts)
        print(f"  {label:<45} {r['total']:>5} {r['wr']:>5.1f}% {r['ev']:>+6.2f}% "
              f"{r['pf']:>5.2f}x {r['total_pnl']:>+7.1f}%")

    # Summary
    o_r = _eval_group(old_daily)
    h_r = _eval_group(hl_hybrid_c)
    print()
    if o_r["total"] > 0 and h_r["total"] > 0:
        n_delta = h_r["total"] - o_r["total"]
        ev_delta = h_r["ev"] - o_r["ev"]
        pnl_delta = h_r["total_pnl"] - o_r["total_pnl"]
        print(f"  HL Hybrid C vs Old baseline:")
        print(f"    Signals: {n_delta:+d} ({h_r['total']}/{o_r['total']})")
        print(f"    EV:      {ev_delta:+.2f}% ({h_r['ev']:+.2f}% vs {o_r['ev']:+.2f}%)")
        print(f"    PnL:     {pnl_delta:+.1f}% ({h_r['total_pnl']:+.1f}% vs {o_r['total_pnl']:+.1f}%)")
        print()
        if h_r["ev"] >= o_r["ev"] - 0.05 and h_r["total"] >= o_r["total"]:
            print("  → HL set maintains or improves performance — SAFE TO SWITCH")
        elif h_r["ev"] < o_r["ev"] - 0.1:
            print("  → HL set underperforms — INVESTIGATE per-symbol/per-type")
        else:
            print("  → Mixed results — review per-symbol breakdown")
    print()


asyncio.run(main())
