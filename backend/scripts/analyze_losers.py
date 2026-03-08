#!/usr/bin/env python3
"""Analyze winning vs losing signals per type.

Shows feature distributions for winners vs losers to find optimal thresholds.

Usage:
  cd backend && python scripts/analyze_losers.py
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

# All signal types — analyze everything
ANALYZE_TYPES = None  # None = analyze all types found

FEATURES = [
    "price_chg", "oi_chg", "price_chg_3d", "oi_chg_3d",
    "price_momentum", "price_vs_sma", "trend",
    "liq_long_z", "liq_short_z", "fund_rate",
]

TP, SL = 5.0, 3.0  # For win/loss classification


def classify_trade(alert: dict) -> str:
    """WIN/LOSS/TIMEOUT using MFE/MAE."""
    mfe = alert.get("mfe_return")
    mae = alert.get("mae_return")
    if mfe is None or mae is None:
        # Fallback to 7d return
        r = alert.get("return_7d")
        if r is None:
            return "NO_DATA"
        adj = -r if alert["direction"] == "short" else r
        return "WIN" if adj > 0 else "LOSS"

    hit_tp = mfe >= TP
    hit_sl = mae >= SL
    if hit_tp and not hit_sl:
        return "WIN"
    if hit_sl and not hit_tp:
        return "LOSS"
    if hit_tp and hit_sl:
        return "LOSS" if mae < mfe * 0.5 else "WIN" if mfe > mae * 1.5 else "LOSS"
    return "TIMEOUT"


def analyze_type(type_name: str, alerts: list[dict]):
    """Print winner vs loser feature comparison."""
    winners = []
    losers = []

    for a in alerts:
        result = classify_trade(a)
        f = a.get("features", {})
        z = a.get("zscores", {})
        row = {**f, **{f"z_{k}": v for k, v in z.items()}}
        row["confluence"] = a["confluence"]
        row["tier"] = a["tier"]
        row["mfe"] = a.get("mfe_return", 0) or 0
        row["mae"] = a.get("mae_return", 0) or 0
        row["symbol"] = a.get("symbol", "?")
        row["date"] = a.get("fired_at", "?")[:10]
        row["result"] = result

        if result == "WIN":
            winners.append(row)
        elif result == "LOSS":
            losers.append(row)

    total = len(winners) + len(losers)
    if total < 2:
        print(f"\n  {type_name}: слишком мало данных ({total})")
        return

    wr = len(winners) / total * 100

    print(f"\n{'='*90}")
    print(f"  {type_name.upper()} — {total} сигналов, WR={wr:.1f}% ({len(winners)}W / {len(losers)}L)")
    print(f"{'='*90}")

    # Numeric features comparison
    numeric_features = [
        "price_chg", "oi_chg", "price_chg_3d", "oi_chg_3d",
        "price_momentum", "price_vs_sma",
        "liq_long_z", "liq_short_z", "fund_rate",
        "z_oi", "z_funding", "z_liq", "z_volume",
        "confluence", "mfe", "mae",
    ]

    print(f"\n  {'Feature':<18} {'Winners avg':>12} {'Losers avg':>12} {'Δ':>10} {'Insight':<30}")
    print(f"  {'-'*80}")

    for feat in numeric_features:
        w_vals = [r[feat] for r in winners if feat in r and r[feat] is not None and isinstance(r[feat], (int, float))]
        l_vals = [r[feat] for r in losers if feat in r and r[feat] is not None and isinstance(r[feat], (int, float))]

        if not w_vals or not l_vals:
            continue

        w_avg = sum(w_vals) / len(w_vals)
        l_avg = sum(l_vals) / len(l_vals)
        delta = w_avg - l_avg

        # Insight
        insight = ""
        if abs(delta) > abs(l_avg) * 0.3 and abs(delta) > 0.5:
            if delta > 0:
                insight = f"Winners HIGHER → filter > {l_avg:.1f}?"
            else:
                insight = f"Winners LOWER → filter < {w_avg + abs(delta)*0.3:.1f}?"

        print(f"  {feat:<18} {w_avg:>+11.2f} {l_avg:>+11.2f} {delta:>+9.2f} {insight:<30}")

    # Trend distribution
    print(f"\n  Trend distribution:")
    for group_name, group in [("Winners", winners), ("Losers", losers)]:
        trends = defaultdict(int)
        for r in group:
            trends[r.get("trend", "?")] += 1
        trend_str = ", ".join(f"{k}={v}" for k, v in sorted(trends.items()))
        print(f"    {group_name}: {trend_str}")

    # Tier distribution
    print(f"\n  Tier distribution:")
    for group_name, group in [("Winners", winners), ("Losers", losers)]:
        tiers = defaultdict(int)
        for r in group:
            tiers[r.get("tier", "?")] += 1
        tier_str = ", ".join(f"{k}={v}" for k, v in sorted(tiers.items()))
        print(f"    {group_name}: {tier_str}")

    # Symbol distribution (top losers)
    if losers:
        sym_counts = defaultdict(int)
        for r in losers:
            sym_counts[r["symbol"]] += 1
        top_losers = sorted(sym_counts.items(), key=lambda x: -x[1])[:5]
        print(f"\n  Top losing symbols: {', '.join(f'{s}({n})' for s, n in top_losers)}")

    # All signals detail
    n_show = min(len(winners), 5)
    print(f"\n  WINNERS ({len(winners)} total, showing {n_show}):")
    for r in winners[:n_show]:
        print(f"    {r['date']} {r['symbol']:<10} price_chg={r.get('price_chg',0):+.1f}% "
              f"oi_chg={r.get('oi_chg',0):+.1f}% momentum={r.get('price_momentum',0):+.1f}% "
              f"trend={r.get('trend','?')} conf={r.get('confluence',0)} "
              f"fund={r.get('fund_rate',0) or 0:+.4f} "
              f"MFE={r['mfe']:+.1f}% MAE={r['mae']:+.1f}%")

    n_show = min(len(losers), 8)
    print(f"\n  LOSERS ({len(losers)} total, showing {n_show}):")
    for r in losers[:n_show]:
        print(f"    {r['date']} {r['symbol']:<10} price_chg={r.get('price_chg',0):+.1f}% "
              f"oi_chg={r.get('oi_chg',0):+.1f}% momentum={r.get('price_momentum',0):+.1f}% "
              f"trend={r.get('trend','?')} conf={r.get('confluence',0)} "
              f"fund={r.get('fund_rate',0) or 0:+.4f} "
              f"MFE={r['mfe']:+.1f}% MAE={r['mae']:+.1f}%")


async def main():
    await init_db()

    all_alerts = []
    for sym in SYMBOLS:
        alerts = await simulate_alerts(sym, days=365)
        for a in alerts:
            a["symbol"] = sym
        all_alerts.extend(alerts)

    print(f"\n  Всего сигналов: {len(all_alerts)}")

    by_type = defaultdict(list)
    for a in all_alerts:
        by_type[a["type"]].append(a)

    # Sort by EV (worst first)
    types_to_analyze = ANALYZE_TYPES or sorted(by_type.keys())
    for t in types_to_analyze:
        if t in by_type:
            analyze_type(t, by_type[t])

    print()


asyncio.run(main())
