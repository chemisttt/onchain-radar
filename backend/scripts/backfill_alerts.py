"""Backfill alert_tracking with signals from current Hybrid C strategy.

Replaces all existing alerts with properly simulated ones.
Strategy: 3 types on 4h detection, rest on daily (see docs/STRATEGY.md).

Usage: cd backend && python3 scripts/backfill_alerts.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db, get_db
from services import backtest_service
from services.derivatives_service import SYMBOLS

# Hybrid C: these 3 types use 4h detection, all others use daily
TYPES_4H = {"liq_short_squeeze", "momentum_divergence", "div_top_1d"}

# Direction mapping: backtest uses "long"/"short", alert_tracking uses "up"/"down"
DIR_MAP = {"long": "up", "short": "down"}


async def main():
    await init_db()
    db = get_db()

    # Count existing
    before = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM alert_tracking")
    print(f"Existing alerts: {before[0]['cnt']}")

    # Clear old alerts
    await db.execute("DELETE FROM alert_tracking")
    await db.commit()
    print("Cleared alert_tracking")

    total_inserted = 0
    errors = 0

    for symbol in SYMBOLS:
        try:
            # Daily detection (all types except TYPES_4H)
            daily_alerts = await backtest_service.simulate_alerts(symbol, days=1095)
            daily_filtered = [a for a in daily_alerts if a["type"] not in TYPES_4H]

            # 4h detection (only TYPES_4H)
            alerts_4h = await backtest_service.simulate_alerts_4h_detection(symbol, days=1095)
            h4_filtered = [a for a in alerts_4h if a["type"] in TYPES_4H]

            combined = daily_filtered + h4_filtered
            combined.sort(key=lambda a: a["fired_at"])

            inserted = 0
            for a in combined:
                direction = a.get("direction")
                if not direction:
                    continue

                alert_type = a["type"]
                key = f"{alert_type}:{symbol}"
                expected_dir = DIR_MAP.get(direction, direction)

                await db.execute(
                    """INSERT INTO alert_tracking
                       (alert_key, alert_type, symbol, tier, confluence, fired_at,
                        entry_price, expected_direction,
                        price_1d, price_3d, price_7d,
                        return_1d, return_3d, return_7d)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        key, alert_type, symbol, a["tier"], a["confluence"],
                        a["fired_at"], a["entry_price"], expected_dir,
                        a.get("price_1d"), a.get("price_3d"), a.get("price_7d"),
                        a.get("return_1d"), a.get("return_3d"), a.get("return_7d"),
                    ),
                )
                inserted += 1

            await db.commit()
            total_inserted += inserted
            daily_count = len(daily_filtered)
            h4_count = len(h4_filtered)
            print(f"  {symbol}: {inserted} alerts ({daily_count} daily + {h4_count} 4h)")

        except Exception as e:
            errors += 1
            print(f"  {symbol}: ERROR — {e}")

    print(f"\nDone: {total_inserted} alerts inserted for {len(SYMBOLS)} symbols")
    if errors:
        print(f"Errors: {errors}")


if __name__ == "__main__":
    asyncio.run(main())
