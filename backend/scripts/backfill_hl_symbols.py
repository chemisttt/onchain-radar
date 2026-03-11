#!/usr/bin/env python3
"""Backfill all data for 4 new HL symbols: HYPE, ZEC, BCH, WLD.

Fills: daily_derivatives, derivatives_4h, ohlcv_4h, daily_momentum.
Safe to re-run (UPSERT / INSERT OR IGNORE).

Usage:
  cd backend && python3 scripts/backfill_hl_symbols.py
"""

import asyncio
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import init_db, get_db

NEW_SYMBOLS = os.environ.get("BACKFILL_SYMBOLS", "HYPEUSDT,ZECUSDT,BCHUSDT,WLDUSDT").split(",")
COINALYZE_API_KEY = os.environ.get("COINALYZE_API_KEY", "")
START = datetime(2022, 1, 1, tzinfo=timezone.utc)
FOUR_HOURS_MS = 4 * 3600 * 1000

DAILY_UPSERT = """
INSERT INTO daily_derivatives
  (symbol, date, close_price, open_interest_usd, funding_rate,
   liquidations_long, liquidations_short, liquidations_delta,
   volume_usd, oi_binance_usd)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, date) DO UPDATE SET
  close_price = CASE WHEN excluded.close_price > 0
    THEN excluded.close_price ELSE daily_derivatives.close_price END,
  open_interest_usd = CASE WHEN excluded.open_interest_usd > 0
    THEN excluded.open_interest_usd ELSE daily_derivatives.open_interest_usd END,
  oi_binance_usd = CASE WHEN excluded.oi_binance_usd > 0
    THEN excluded.oi_binance_usd ELSE daily_derivatives.oi_binance_usd END,
  funding_rate = CASE WHEN excluded.funding_rate != 0
    THEN excluded.funding_rate ELSE daily_derivatives.funding_rate END,
  liquidations_long = CASE WHEN excluded.liquidations_long > 0
    THEN excluded.liquidations_long ELSE daily_derivatives.liquidations_long END,
  liquidations_short = CASE WHEN excluded.liquidations_short > 0
    THEN excluded.liquidations_short ELSE daily_derivatives.liquidations_short END,
  liquidations_delta = CASE WHEN excluded.liquidations_long > 0
    THEN excluded.liquidations_delta ELSE daily_derivatives.liquidations_delta END,
  volume_usd = CASE WHEN excluded.volume_usd > 0
    THEN excluded.volume_usd ELSE daily_derivatives.volume_usd END
"""

H4_UPSERT = """
INSERT INTO derivatives_4h
  (symbol, ts, close_price, open_interest_usd, funding_rate,
   liquidations_long, liquidations_short, liquidations_delta,
   volume_usd, oi_binance_usd)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, ts) DO UPDATE SET
  close_price = CASE WHEN excluded.close_price > 0
    THEN excluded.close_price ELSE derivatives_4h.close_price END,
  open_interest_usd = CASE WHEN excluded.open_interest_usd > 0
    THEN excluded.open_interest_usd ELSE derivatives_4h.open_interest_usd END,
  oi_binance_usd = CASE WHEN excluded.oi_binance_usd > 0
    THEN excluded.oi_binance_usd ELSE derivatives_4h.oi_binance_usd END,
  funding_rate = CASE WHEN excluded.funding_rate != 0
    THEN excluded.funding_rate ELSE derivatives_4h.funding_rate END,
  liquidations_long = CASE WHEN excluded.liquidations_long > 0
    THEN excluded.liquidations_long ELSE derivatives_4h.liquidations_long END,
  liquidations_short = CASE WHEN excluded.liquidations_short > 0
    THEN excluded.liquidations_short ELSE derivatives_4h.liquidations_short END,
  liquidations_delta = CASE WHEN excluded.liquidations_long > 0
    THEN excluded.liquidations_delta ELSE derivatives_4h.liquidations_delta END,
  volume_usd = CASE WHEN excluded.volume_usd > 0
    THEN excluded.volume_usd ELSE derivatives_4h.volume_usd END
"""


def _snap_to_4h(ts_ms: int) -> int:
    return (ts_ms // FOUR_HOURS_MS) * FOUR_HOURS_MS


# ── Binance fetchers ──

async def fetch_binance_klines(session, symbol, interval, start_ms, pages=8):
    """Fetch klines. Returns {key: (open, high, low, close, volume)} where key is date or ts_ms."""
    result = {}
    timeout = aiohttp.ClientTimeout(total=20)
    cursor = start_ms
    daily = interval == "1d"

    for page in range(pages):
        try:
            async with session.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": symbol, "interval": interval, "limit": 1500, "startTime": cursor},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    break
                data = await resp.json()
                if not data:
                    break
                for k in data:
                    ts = int(k[0])
                    if daily:
                        key = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                    else:
                        key = ts
                    result[key] = (float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[7]))
                cursor = int(data[-1][0]) + 1
                if len(data) < 1500:
                    break
        except Exception as e:
            print(f"    klines {symbol} {interval} page {page}: {e}")
            break
        await asyncio.sleep(0.12)
    return result


async def fetch_binance_funding(session, symbol, start_ms):
    """Returns {date: avg_rate} for daily, {ts_ms_snapped: rate} for 4h."""
    funding_all = []
    timeout = aiohttp.ClientTimeout(total=20)
    cursor = start_ms

    for _ in range(5):
        try:
            async with session.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": symbol, "limit": 1000, "startTime": cursor},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    break
                page = await resp.json()
                if not page:
                    break
                funding_all.extend(page)
                cursor = int(page[-1].get("fundingTime", 0)) + 1
                if len(page) < 1000:
                    break
        except Exception as e:
            print(f"    funding {symbol}: {e}")
            break
        await asyncio.sleep(0.12)

    # Daily average
    daily: dict[str, list[float]] = {}
    # 4h snapped
    h4: dict[int, float] = {}

    for item in funding_all:
        ts_ms = int(item.get("fundingTime", 0))
        rate = float(item.get("fundingRate", 0) or 0)
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        daily.setdefault(dt, []).append(rate)
        bar_ts = _snap_to_4h(ts_ms)
        if bar_ts in h4:
            h4[bar_ts] = (h4[bar_ts] + rate) / 2
        else:
            h4[bar_ts] = rate

    daily_avg = {dt: sum(rs) / len(rs) for dt, rs in daily.items()}
    return daily_avg, h4


# ── Coinalyze fetchers ──

async def fetch_coinalyze_liq(session, symbols, start_ts, end_ts, interval="daily"):
    """Returns {symbol: {key: (long, short, delta)}}. key=date for daily, ts_ms for 4hour."""
    result = {}
    ca_symbols = ",".join(f"{s}_PERP.A" for s in symbols)
    try:
        url = (
            f"https://api.coinalyze.net/v1/liquidation-history"
            f"?symbols={ca_symbols}&interval={interval}"
            f"&from={start_ts}&to={end_ts}&convert_to_usd=true"
            f"&api_key={COINALYZE_API_KEY}"
        )
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status == 200:
                data = await resp.json()
                for item in data:
                    sym = item["symbol"].replace("_PERP.A", "")
                    sym_data = {}
                    for h in item.get("history", []):
                        ts_sec = h["t"]
                        liq_long = h.get("l", 0) or 0
                        liq_short = h.get("s", 0) or 0
                        if interval == "daily":
                            key = datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime("%Y-%m-%d")
                        else:
                            key = ts_sec * 1000
                        sym_data[key] = (liq_long, liq_short, liq_long - liq_short)
                    result[sym] = sym_data
            else:
                text = await resp.text()
                print(f"    Coinalyze liq {interval} HTTP {resp.status}: {text[:100]}")
    except Exception as e:
        print(f"    Coinalyze liq {interval}: {e}")
    return result


async def fetch_coinalyze_oi(session, symbols, start_ts, end_ts):
    """Daily OI only (4h doesn't work on Coinalyze). Returns {symbol: {date: oi_usd}}."""
    result = {}
    ca_symbols = ",".join(f"{s}_PERP.A" for s in symbols)
    try:
        url = (
            f"https://api.coinalyze.net/v1/open-interest-history"
            f"?symbols={ca_symbols}&interval=daily"
            f"&from={start_ts}&to={end_ts}&convert_to_usd=true"
            f"&api_key={COINALYZE_API_KEY}"
        )
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status == 200:
                data = await resp.json()
                for item in data:
                    sym = item["symbol"].replace("_PERP.A", "")
                    sym_data = {}
                    for h in item.get("history", []):
                        dt = datetime.fromtimestamp(h["t"], tz=timezone.utc).strftime("%Y-%m-%d")
                        oi_close = h.get("c", 0) or 0
                        if oi_close > 0:
                            sym_data[dt] = oi_close
                    result[sym] = sym_data
            else:
                text = await resp.text()
                print(f"    Coinalyze OI HTTP {resp.status}: {text[:100]}")
    except Exception as e:
        print(f"    Coinalyze OI: {e}")
    return result


# ── Momentum ──

def _decile(rank_pct):
    return max(1, min(10, int(rank_pct * 10) + 1))

def _directional_intensity(returns, window=20):
    if len(returns) < window:
        return 0.0
    recent = returns[-window:]
    pos = sum(1 for r in recent if r > 0)
    neg = sum(1 for r in recent if r < 0)
    return round((pos - neg) / len(recent), 4)

def _vol_regime(returns, short_window=10, long_window=30):
    if len(returns) < long_window:
        return 0.0
    short_std = (sum(r ** 2 for r in returns[-short_window:]) / short_window) ** 0.5
    long_std = (sum(r ** 2 for r in returns[-long_window:]) / long_window) ** 0.5
    if long_std == 0:
        return 0.0
    return round(short_std - long_std, 6)

def _compute_momentum_value(cs, ts, rel, di, vr):
    decile_avg = ((cs - 5.5) / 4.5 + (ts - 5.5) / 4.5 + (rel - 5.5) / 4.5) / 3
    vr_signal = 1.0 if vr > 0 else (-1.0 if vr < 0 else 0.0)
    raw = decile_avg * 60 + di * 30 + vr_signal * 10
    return round(max(-100, min(100, raw)), 1)


async def compute_momentum_for_symbols(db, symbols):
    """Compute daily_momentum for given symbols using all symbols for cross-sectional ranking."""
    print("  Computing momentum...")

    # Load ALL daily_derivatives (for cross-sectional ranking)
    all_syms_rows = await db.execute_fetchall(
        "SELECT DISTINCT symbol FROM daily_derivatives WHERE close_price > 0"
    )
    all_syms = [r["symbol"] for r in all_syms_rows]

    all_data = {}
    for sym in all_syms:
        rows = await db.execute_fetchall(
            """SELECT date, close_price, volume_usd FROM daily_derivatives
               WHERE symbol = ? AND close_price > 0 ORDER BY date ASC""",
            (sym,),
        )
        if len(rows) >= 60:
            all_data[sym] = [{"date": r["date"], "price": r["close_price"],
                              "vol": r["volume_usd"] or 0} for r in rows]

    # Only compute for target symbols
    target_syms = [s for s in symbols if s in all_data]
    if not target_syms:
        print("  No target symbols with enough data for momentum")
        return

    # Find dates for target symbols
    target_dates = set()
    for sym in target_syms:
        for r in all_data[sym][60:]:
            target_dates.add(r["date"])
    target_dates = sorted(target_dates)

    # Check existing
    existing = set()
    for sym in target_syms:
        rows = await db.execute_fetchall(
            "SELECT date FROM daily_momentum WHERE symbol = ?", (sym,))
        for r in rows:
            existing.add(f"{sym}:{r['date']}")

    inserted = 0
    batch = []

    for di, target_date in enumerate(target_dates):
        # Build arrays up to target_date
        prices_up_to = {}
        volumes_up_to = {}
        for sym, rows in all_data.items():
            p, v = [], []
            for r in rows:
                if r["date"] > target_date:
                    break
                p.append(r["price"])
                v.append(r["vol"])
            if len(p) >= 60:
                prices_up_to[sym] = p
                volumes_up_to[sym] = v

        if len(prices_up_to) < 5:
            continue

        # 1-month returns for cross-sectional ranking
        one_month_returns = {}
        one_month_histories = {}
        daily_returns = {}

        for sym, p in prices_up_to.items():
            dr = [(p[i] - p[i-1]) / p[i-1] for i in range(1, len(p)) if p[i-1] > 0]
            daily_returns[sym] = dr
            if len(p) >= 31:
                one_month_returns[sym] = (p[-1] - p[-31]) / p[-31] if p[-31] > 0 else 0
            hist = []
            for i in range(30, len(p)):
                if p[i-30] > 0:
                    hist.append((p[i] - p[i-30]) / p[i-30])
            one_month_histories[sym] = hist

        if not one_month_returns:
            continue

        # Cross-sectional ranking (ALL symbols)
        sorted_returns = sorted(one_month_returns.items(), key=lambda x: x[1])
        n_syms = len(sorted_returns)
        cs_ranks = {}
        for rank, (sym, _) in enumerate(sorted_returns):
            cs_ranks[sym] = _decile(rank / n_syms)

        btc_return = one_month_returns.get("BTCUSDT", 0)

        # Only compute for TARGET symbols
        for sym in target_syms:
            if sym not in one_month_returns or sym not in prices_up_to:
                continue
            if f"{sym}:{target_date}" in existing:
                continue
            dr = daily_returns.get(sym, [])
            if len(dr) < 20:
                continue

            cs = cs_ranks.get(sym, 5)
            hist = one_month_histories.get(sym, [])
            current_ret = one_month_returns[sym]
            ts = 5
            if len(hist) >= 20:
                below = sum(1 for h in hist if h < current_ret)
                ts = _decile(below / len(hist))

            # Relative vs BTC
            if sym == "BTCUSDT":
                rel = 5
            else:
                all_rel = {s: one_month_returns.get(s, 0) - btc_return
                           for s in one_month_returns if s != "BTCUSDT"}
                sorted_rel = sorted(all_rel.items(), key=lambda x: x[1])
                rel = 5
                for rank, (s, _) in enumerate(sorted_rel):
                    if s == sym:
                        rel = _decile(rank / len(sorted_rel))
                        break

            di_val = _directional_intensity(dr)
            vr = _vol_regime(dr)

            vols = volumes_up_to.get(sym, [])
            rel_vol = 1.0
            if len(vols) >= 2:
                avg_vol = sum(vols[-31:-1]) / min(30, len(vols)-1) if len(vols) > 1 else 1
                rel_vol = round(vols[-1] / avg_vol, 2) if avg_vol > 0 else 1.0

            p = prices_up_to[sym]
            high_52w = max(p[-252:]) if len(p) >= 252 else max(p)
            prox_52w = round((high_52w - p[-1]) / high_52w * 100, 1) if high_52w > 0 else 0

            momentum = _compute_momentum_value(cs, ts, rel, di_val, vr)
            batch.append((sym, target_date, momentum, cs, ts, rel, di_val, vr, rel_vol, prox_52w))
            inserted += 1

        if len(batch) >= 500:
            await db.executemany(
                """INSERT INTO daily_momentum
                   (symbol, date, momentum_value, cs_decile, ts_decile, rel_decile,
                    directional_intensity, vol_regime, relative_volume, proximity_52w_high)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, date) DO UPDATE SET
                     momentum_value=excluded.momentum_value""",
                batch,
            )
            await db.commit()
            batch = []

        if (di + 1) % 200 == 0:
            print(f"    [{di+1}/{len(target_dates)}] {target_date} — {inserted} rows")

    if batch:
        await db.executemany(
            """INSERT INTO daily_momentum
               (symbol, date, momentum_value, cs_decile, ts_decile, rel_decile,
                directional_intensity, vol_regime, relative_volume, proximity_52w_high)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET
                 momentum_value=excluded.momentum_value""",
            batch,
        )
        await db.commit()

    print(f"  Momentum: {inserted} rows for {len(target_syms)} symbols")


async def backfill():
    await init_db()
    db = get_db()
    t0 = time.time()

    # Show before counts
    for table in ["daily_derivatives", "derivatives_4h", "ohlcv_4h", "daily_momentum"]:
        for sym in NEW_SYMBOLS:
            r = await db.execute_fetchall(f"SELECT COUNT(*) as c FROM {table} WHERE symbol=?", (sym,))
            count = r[0]["c"] if r else 0
            if count > 0:
                print(f"  {table}.{sym}: {count} existing rows")

    start_ts = int(START.timestamp())
    start_ms = start_ts * 1000
    end_ts = int(datetime.now(timezone.utc).timestamp())

    async with aiohttp.ClientSession() as session:
        # ── Phase 1: Coinalyze (daily liq + daily OI + 4h liq) ──
        print("\n  [1/4] Coinalyze: daily liq + OI...")
        daily_liq = await fetch_coinalyze_liq(session, NEW_SYMBOLS, start_ts, end_ts, "daily")
        await asyncio.sleep(5)
        daily_oi = await fetch_coinalyze_oi(session, NEW_SYMBOLS, start_ts, end_ts)
        await asyncio.sleep(5)
        print("         Coinalyze: 4h liq...")
        h4_liq = await fetch_coinalyze_liq(session, NEW_SYMBOLS, start_ts, end_ts, "4hour")

        for sym in NEW_SYMBOLS:
            dl = len(daily_liq.get(sym, {}))
            do = len(daily_oi.get(sym, {}))
            hl = len(h4_liq.get(sym, {}))
            print(f"         {sym}: daily_liq={dl} daily_oi={do} h4_liq={hl}")

        # ── Phase 2: Binance per-symbol ──
        print("\n  [2/4] Binance: klines + funding per symbol...")
        for idx, sym in enumerate(NEW_SYMBOLS):
            # Daily klines
            daily_klines = await fetch_binance_klines(session, sym, "1d", start_ms, pages=2)
            await asyncio.sleep(0.3)
            # 4h klines
            h4_klines = await fetch_binance_klines(session, sym, "4h", start_ms, pages=8)
            await asyncio.sleep(0.3)
            # Funding (returns both daily and 4h)
            fund_daily, fund_4h = await fetch_binance_funding(session, sym, start_ms)
            await asyncio.sleep(0.3)

            sym_d_liq = daily_liq.get(sym, {})
            sym_d_oi = daily_oi.get(sym, {})
            sym_h_liq = h4_liq.get(sym, {})

            # ── Insert daily_derivatives ──
            saved_daily = 0
            for dt, (o, h, l, close, volume) in sorted(daily_klines.items()):
                fund = fund_daily.get(dt, 0)
                liq = sym_d_liq.get(dt, (0, 0, 0))
                oi = sym_d_oi.get(dt, 0)
                await db.execute(DAILY_UPSERT, (
                    sym, dt, close, oi, fund,
                    liq[0], liq[1], liq[2], volume, oi,
                ))
                saved_daily += 1

            # ── Insert ohlcv_4h ──
            saved_ohlcv = 0
            for ts_ms, (o, h, l, close, volume) in sorted(h4_klines.items()):
                await db.execute(
                    """INSERT OR IGNORE INTO ohlcv_4h
                       (symbol, ts, open, high, low, close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (sym, ts_ms, o, h, l, close, volume),
                )
                saved_ohlcv += 1

            # ── Insert derivatives_4h ──
            saved_h4 = 0
            for ts_ms, (o, h, l, close, volume) in sorted(h4_klines.items()):
                fund = fund_4h.get(ts_ms, 0)
                liq = sym_h_liq.get(ts_ms, (0, 0, 0))
                # OI: propagate from daily
                bar_date = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                oi = sym_d_oi.get(bar_date, 0)
                await db.execute(H4_UPSERT, (
                    sym, ts_ms, close, oi, fund,
                    liq[0], liq[1], liq[2], volume, oi,
                ))
                saved_h4 += 1

            await db.commit()

            dates = sorted(daily_klines.keys())
            date_range = f"{dates[0]} → {dates[-1]}" if dates else "no data"
            print(f"  [{idx+1}/{len(NEW_SYMBOLS)}] {sym}: "
                  f"daily={saved_daily} ohlcv_4h={saved_ohlcv} deriv_4h={saved_h4} "
                  f"({date_range})")

    # ── Phase 3: Momentum ──
    print("\n  [3/4] Computing momentum for new symbols...")
    await compute_momentum_for_symbols(db, NEW_SYMBOLS)

    # ── Phase 4: Summary ──
    print("\n  [4/4] Summary:")
    for table in ["daily_derivatives", "derivatives_4h", "ohlcv_4h", "daily_momentum"]:
        for sym in NEW_SYMBOLS:
            r = await db.execute_fetchall(f"SELECT COUNT(*) as c FROM {table} WHERE symbol=?", (sym,))
            count = r[0]["c"] if r else 0
            print(f"    {table}.{sym}: {count} rows")

    elapsed = time.time() - t0
    print(f"\n  Done in {elapsed:.1f}s\n")


if __name__ == "__main__":
    asyncio.run(backfill())
