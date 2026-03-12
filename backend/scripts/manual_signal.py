"""Manually inject a missed signal into the trading service.

Calls on_signal() with the same alert dict format as telegram_service would.
The trading service handles everything: equity check, market order, SL, DB, Telegram.

Usage:
  cd backend && python3 scripts/manual_signal.py \
    --symbol BTCUSDT --type distribution --direction down --confluence 6 --tier SIGNAL
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db, get_db
from services import trading_service


async def main():
    parser = argparse.ArgumentParser(description="Inject a missed signal")
    parser.add_argument("--symbol", required=True, help="e.g. BTCUSDT")
    parser.add_argument("--type", required=True, dest="signal_type",
                        help="e.g. distribution")
    parser.add_argument("--direction", required=True, choices=["up", "down"],
                        help="up=long, down=short")
    parser.add_argument("--confluence", type=int, default=6)
    parser.add_argument("--tier", default="SIGNAL")
    args = parser.parse_args()

    await init_db()
    db = get_db()

    # Init wallet (normally done by start())
    from eth_account import Account
    from config import settings
    if not settings.hl_wallet_key:
        print("ERROR: HL_WALLET_KEY not set in .env")
        return
    trading_service._wallet = Account.from_key(settings.hl_wallet_key)
    trading_service._address = trading_service._wallet.address
    print(f"Wallet: {trading_service._address}")

    # Build alert dict matching telegram_service format
    alert_key = f"{args.signal_type}:{args.symbol}"
    alert = {
        "key": alert_key,
        "symbol": args.symbol,
        "expected_direction": args.direction,
        "confluence": args.confluence,
        "tier": args.tier,
        "entry_price": 0,  # on_signal will use mid price from HL
        "title": f"[MANUAL] {args.signal_type} {args.symbol}",
    }

    # Get current price from HL for entry_price field
    import aiohttp
    async with aiohttp.ClientSession() as session:
        await trading_service._load_meta(session)
        mids = await trading_service._get_all_mids(session)
        coin = trading_service._normalize_coin(args.symbol)
        mid = mids.get(coin, 0)
        if not mid:
            print(f"ERROR: No mid price for {coin} on HL")
            return
        alert["entry_price"] = mid
        print(f"Current mid price: {mid}")

    # Record in alert_tracking first (so on_signal can find alert_id)
    dir_db = args.direction  # already "up"/"down"
    await db.execute(
        """INSERT INTO alert_tracking
           (alert_key, alert_type, symbol, tier, confluence, fired_at,
            entry_price, expected_direction)
           VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?)""",
        (alert_key, args.signal_type, args.symbol, args.tier,
         args.confluence, mid, dir_db),
    )
    await db.commit()
    print(f"Recorded alert in alert_tracking")

    # Call on_signal — this does the full flow
    print(f"Calling on_signal: {args.signal_type} {args.symbol} "
          f"{'SHORT' if args.direction == 'down' else 'LONG'} @ {mid}")
    await trading_service.on_signal(alert)

    # Check if trade was created
    rows = await db.execute_fetchall(
        "SELECT * FROM trades WHERE symbol=? AND status='open' "
        "ORDER BY id DESC LIMIT 1", (args.symbol,))
    row = rows[0] if rows else None
    if row:
        print(f"\n✅ Trade opened: #{row['id']} {row['direction']} {row['symbol']} "
              f"@ {row['entry_price']} SL={row['sl_price']}")
    else:
        print("\n❌ Trade was NOT opened — check logs for errors")


if __name__ == "__main__":
    asyncio.run(main())
