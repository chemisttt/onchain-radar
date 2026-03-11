#!/usr/bin/env python3
"""Quick backtest of BCH replacement candidates: XMR, TAO, ZRO.

Usage:
  cd backend && python3 scripts/test_candidates.py
"""

import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import init_db
from services.backtest_service import simulate_alerts, simulate_alerts_4h_detection

CANDIDATES = ["BCHUSDT", "XMRUSDT", "TAOUSDT", "ZROUSDT"]
TP, SL = 5, 3


def eval_trade(a):
    mfe, mae = a.get("mfe_return"), a.get("mae_return")
    if mfe is None or mae is None:
        return None
    hit_tp, hit_sl = mfe >= TP, mae >= SL
    if hit_tp and not hit_sl:
        return TP
    if hit_sl and not hit_tp:
        return -SL
    if hit_tp and hit_sl:
        return TP if mfe > mae * 1.5 else -SL
    r = a.get("return_7d")
    if r is None:
        return 0
    return -r if a["direction"] == "short" else r


def stats(alerts):
    pnls = [eval_trade(a) for a in alerts]
    pnls = [p for p in pnls if p is not None]
    n = len(pnls)
    if n == 0:
        return {"n": 0, "wr": 0, "ev": 0, "pnl": 0}
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    resolved = wins + losses
    wr = wins / resolved * 100 if resolved > 0 else 0
    total = sum(pnls)
    return {"n": n, "wr": wr, "ev": total / n, "pnl": total}


async def main():
    await init_db()

    print(f"\n{'Symbol':<12} {'D.N':>4} {'D.WR':>6} {'D.EV':>7} {'D.PnL':>7} | "
          f"{'4h.N':>5} {'4h.WR':>6} {'4h.EV':>7} {'4h.PnL':>7}")
    print("-" * 80)

    for sym in CANDIDATES:
        try:
            daily = await simulate_alerts(sym, days=1095)
            for a in daily:
                a["symbol"] = sym
            d = stats(daily)
        except Exception as e:
            print(f"  {sym} daily error: {e}")
            daily = []
            d = {"n": 0, "wr": 0, "ev": 0, "pnl": 0}

        try:
            h4 = await simulate_alerts_4h_detection(sym, days=1095)
            for a in h4:
                a["symbol"] = sym
            h = stats(h4)
        except Exception as e:
            print(f"  {sym} 4h error: {e}")
            h4 = []
            h = {"n": 0, "wr": 0, "ev": 0, "pnl": 0}

        print(f"{sym:<12} {d['n']:>4} {d['wr']:>5.1f}% {d['ev']:>+6.2f}% {d['pnl']:>+6.1f}% | "
              f"{h['n']:>5} {h['wr']:>5.1f}% {h['ev']:>+6.2f}% {h['pnl']:>+6.1f}%")

        # Per type breakdown
        for src, alerts in [("D", daily), ("4h", h4)]:
            by_type = defaultdict(list)
            for a in alerts:
                by_type[a["type"]].append(a)
            for t in sorted(by_type.keys()):
                s = stats(by_type[t])
                if s["n"] > 0:
                    print(f"  [{src:>2}] {t:<20} N={s['n']:>3}  WR={s['wr']:>5.1f}%  EV={s['ev']:>+6.2f}%")
        print()

    print("Done.\n")


asyncio.run(main())
