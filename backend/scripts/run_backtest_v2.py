#!/usr/bin/env python3
"""Backtest runner — sequential daily SL/TP evaluation.

Simulates real swing trades: enter on signal, check SL/TP each day,
first hit determines outcome. 7-day max holding period.

Usage:
  cd backend && python scripts/run_backtest_v2.py
"""

import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import init_db
from services.backtest_service import simulate_alerts

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


def _simulate_trade(alert: dict, tp_pct: float, sl_pct: float) -> dict:
    """Simulate a trade with sequential daily SL/TP check.

    Walks through days 1-7, checks close price against SL and TP.
    First level hit determines outcome.

    Returns: {result: "WIN"/"LOSS"/"TIMEOUT", days: N, pnl: float}
    """
    entry = alert["entry_price"]
    direction = alert["direction"]

    # Build daily prices array from return data
    # We have return_1d, return_3d, return_7d + actual prices
    daily_prices = []
    for key in ["price_1d", "price_3d", "price_7d"]:
        p = alert.get(key)
        if p is not None:
            daily_prices.append(p)

    if not daily_prices:
        return {"result": "NO_DATA", "days": 0, "pnl": 0}

    # We only have prices at days 1, 3, 7 — not 2, 4, 5, 6
    # Use what we have: check at available checkpoints
    checkpoints = []
    if alert.get("price_1d") is not None:
        checkpoints.append((1, alert["price_1d"]))
    if alert.get("price_3d") is not None:
        checkpoints.append((3, alert["price_3d"]))
    if alert.get("price_7d") is not None:
        checkpoints.append((7, alert["price_7d"]))

    for day, price in checkpoints:
        if direction == "long":
            pnl = (price - entry) / entry * 100
        else:
            pnl = (entry - price) / entry * 100

        if pnl <= -sl_pct:
            return {"result": "LOSS", "days": day, "pnl": -sl_pct}
        if pnl >= tp_pct:
            return {"result": "WIN", "days": day, "pnl": tp_pct}

    # Timeout — exit at last available price
    last_day, last_price = checkpoints[-1]
    if direction == "long":
        final_pnl = (last_price - entry) / entry * 100
    else:
        final_pnl = (entry - last_price) / entry * 100

    return {"result": "TIMEOUT", "days": last_day, "pnl": final_pnl}


def _simulate_mfe_trade(alert: dict, tp_pct: float, sl_pct: float) -> dict:
    """Simulate trade using MFE/MAE data.

    MFE = max favorable move within 7 days
    MAE = max adverse move within 7 days

    Logic: if MAE < SL → never stopped out. If MFE >= TP → reached target.
    If both exceeded → conservative: assume SL hit first (unless MFE >> MAE).
    """
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
        # Both triggered — use MFE/MAE ratio as heuristic
        # If MFE much bigger than MAE, likely TP hit first (dip then rally)
        if mfe > mae * 1.5:
            return {"result": "WIN", "pnl": tp_pct}
        return {"result": "LOSS", "pnl": -sl_pct}
    # Neither hit
    # Use final 7d return as PnL
    r = alert.get("return_7d")
    if r is None:
        return {"result": "TIMEOUT", "pnl": 0}
    adj_r = -r if alert["direction"] == "short" else r
    return {"result": "TIMEOUT", "pnl": adj_r}


def _eval_group(alerts: list[dict], tp: float, sl: float, use_mfe: bool = True) -> dict:
    """Evaluate a group of alerts with given TP/SL."""
    wins, losses, timeouts = 0, 0, 0
    total_pnl = 0.0
    win_days = []
    quick_wins = 0  # TP hit in <=1 day

    for a in alerts:
        # Try MFE-based first, fallback to checkpoint-based
        trade = None
        if use_mfe:
            trade = _simulate_mfe_trade(a, tp, sl)
        if trade is None:
            trade = _simulate_trade(a, tp, sl)

        if trade["result"] == "NO_DATA":
            continue
        elif trade["result"] == "WIN":
            wins += 1
            total_pnl += trade["pnl"]
            if "days" in trade:
                win_days.append(trade["days"])
                if trade["days"] <= 1:
                    quick_wins += 1
        elif trade["result"] == "LOSS":
            losses += 1
            total_pnl += trade["pnl"]
        elif trade["result"] == "TIMEOUT":
            timeouts += 1
            total_pnl += trade["pnl"]

    total = wins + losses + timeouts
    if total == 0:
        return {}

    resolved = wins + losses
    wr = wins / resolved * 100 if resolved > 0 else 0
    ev = total_pnl / total
    avg_win_days = sum(win_days) / len(win_days) if win_days else 0

    return {
        "total": total, "wins": wins, "losses": losses, "timeouts": timeouts,
        "wr": wr, "ev": ev, "total_pnl": total_pnl,
        "avg_win_days": avg_win_days, "quick_wins": quick_wins,
    }


async def main():
    await init_db()

    all_alerts = []
    for sym in SYMBOLS:
        alerts = await simulate_alerts(sym, days=365)
        for a in alerts:
            a["symbol"] = sym
        all_alerts.extend(alerts)

    # Global daily cap
    GLOBAL_DAILY_CAP = 5
    by_day_global = defaultdict(list)
    for a in all_alerts:
        day = a["fired_at"][:10]
        by_day_global[day].append(a)
    all_alerts = []
    for day, day_alerts in by_day_global.items():
        day_alerts.sort(key=lambda x: -x["confluence"])
        all_alerts.extend(day_alerts[:GLOBAL_DAILY_CAP])

    print(f"\n  Всего сигналов: {len(all_alerts)}")

    # Count MFE data availability
    with_mfe = sum(1 for a in all_alerts if a.get("mfe_return") is not None)
    print(f"  С MFE/MAE данными: {with_mfe} ({with_mfe/len(all_alerts)*100:.0f}%)")

    by_type = defaultdict(list)
    for a in all_alerts:
        by_type[a["type"]].append(a)

    by_tier = defaultdict(list)
    for a in all_alerts:
        by_tier[a["tier"]].append(a)

    # ═══════════════════════════════════════════════════════════════
    #  Общий WR по разным TP/SL
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 95)
    print("  ОБЩИЙ WR ПО КОМБИНАЦИЯМ TP/SL (все сигналы)")
    print("=" * 95)
    print(f"  {'TP/SL':<8} {'RR':>5} {'WR':>7} {'Wins':>5} {'Loss':>5} {'T/O':>5} {'EV':>8} {'QuickW':>7} {'AvgDays':>8}")
    print("  " + "-" * 70)

    tp_sl_combos = [(3, 2), (5, 3), (5, 2), (8, 3), (8, 5), (3, 3), (5, 5)]
    for tp, sl in tp_sl_combos:
        r = _eval_group(all_alerts, tp, sl)
        if not r:
            continue
        label = f"{tp}%/{sl}%"
        print(f"  {label:<8} {tp/sl:>4.1f}x {r['wr']:>6.1f}% {r['wins']:>5} {r['losses']:>5} "
              f"{r['timeouts']:>5} {r['ev']:>+7.2f}% {r['quick_wins']:>7} {r['avg_win_days']:>7.1f}d")

    # ═══════════════════════════════════════════════════════════════
    #  Per-type breakdown (TP=5% / SL=3%)
    # ═══════════════════════════════════════════════════════════════
    TP, SL = 5, 3
    print()
    print("=" * 95)
    print(f"  ПО ТИПАМ СИГНАЛОВ (TP={TP}% / SL={SL}%)")
    print("=" * 95)
    print(f"  {'Тип':<22} {'N':>4} {'WR':>7} {'W':>4} {'L':>4} {'T/O':>4} {'EV':>8} {'Quick':>6} {'Dir':<6}")
    print("  " + "-" * 75)

    type_results = []
    for t, alerts in sorted(by_type.items(), key=lambda x: -len(x[1])):
        r = _eval_group(alerts, TP, SL)
        if not r or r["total"] < 1:
            continue
        dirs = "/".join(set(a["direction"] for a in alerts))
        print(f"  {t:<22} {r['total']:>4} {r['wr']:>6.1f}% {r['wins']:>4} {r['losses']:>4} "
              f"{r['timeouts']:>4} {r['ev']:>+7.2f}% {r['quick_wins']:>6} {dirs:<6}")
        type_results.append((t, r, dirs))

    # ═══════════════════════════════════════════════════════════════
    #  Per-tier breakdown
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 95)
    print(f"  ПО ТИРАМ (TP={TP}% / SL={SL}%)")
    print("=" * 95)
    for tier in ["SETUP", "SIGNAL", "TRIGGER"]:
        alerts = by_tier.get(tier, [])
        r = _eval_group(alerts, TP, SL)
        if not r:
            continue
        print(f"  {tier:<10} {r['total']:>4} signals, WR={r['wr']:.1f}%, EV={r['ev']:+.2f}%, "
              f"wins={r['wins']}, losses={r['losses']}, timeouts={r['timeouts']}")

    # ═══════════════════════════════════════════════════════════════
    #  TOP 10 vs REST
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 95)
    print(f"  TOP 10 OI vs ALTS (TP={TP}% / SL={SL}%)")
    print("=" * 95)
    for label, group in [("TOP 10 OI", [a for a in all_alerts if a["symbol"] in TOP_OI]),
                          ("ALTS", [a for a in all_alerts if a["symbol"] not in TOP_OI])]:
        r = _eval_group(group, TP, SL)
        if not r:
            continue
        print(f"  {label:<12} {r['total']:>4} signals, WR={r['wr']:.1f}%, EV={r['ev']:+.2f}%")

    # ═══════════════════════════════════════════════════════════════
    #  Лучший TP/SL для каждого типа сигнала
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 95)
    print("  ЛУЧШИЙ TP/SL ДЛЯ КАЖДОГО ТИПА (по EV)")
    print("=" * 95)
    print(f"  {'Тип':<22} {'N':>4} {'Best':>8} {'WR':>7} {'EV':>8} {'RR':>5} {'Verdict':<12}")
    print("  " + "-" * 75)

    for t, alerts in sorted(by_type.items(), key=lambda x: -len(x[1])):
        if len(alerts) < 3:
            continue
        best_ev = -999
        best_combo = None
        best_r = None
        for tp, sl in tp_sl_combos:
            r = _eval_group(alerts, tp, sl)
            if r and r["ev"] > best_ev:
                best_ev = r["ev"]
                best_combo = (tp, sl)
                best_r = r
        if best_r and best_combo:
            tp, sl = best_combo
            label = f"{tp}%/{sl}%"
            verdict = "PROFITABLE" if best_r["ev"] > 0 else "MARGINAL" if best_r["ev"] > -0.5 else "LOSING"
            print(f"  {t:<22} {best_r['total']:>4} {label:>8} {best_r['wr']:>6.1f}% "
                  f"{best_r['ev']:>+7.2f}% {tp/sl:>4.1f}x {verdict:<12}")

    # ═══════════════════════════════════════════════════════════════
    #  MFE Distribution (насколько далеко ходит цена в нашу сторону)
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 95)
    print("  MFE DISTRIBUTION (макс. движение в нашу сторону за 7д)")
    print("=" * 95)
    mfes = [a["mfe_return"] for a in all_alerts if a.get("mfe_return") is not None]
    maes = [a["mae_return"] for a in all_alerts if a.get("mae_return") is not None]
    if mfes:
        buckets = [1, 2, 3, 5, 8, 10, 15, 20]
        for b in buckets:
            pct = sum(1 for m in mfes if m >= b) / len(mfes) * 100
            bar = "█" * int(pct / 2)
            print(f"  MFE >= {b:>2}%: {pct:>5.1f}% {bar}")
        print()
        for b in buckets:
            pct = sum(1 for m in maes if m >= b) / len(maes) * 100
            bar = "█" * int(pct / 2)
            print(f"  MAE >= {b:>2}%: {pct:>5.1f}% {bar}")

    print()


asyncio.run(main())
