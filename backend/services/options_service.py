import asyncio
import logging
import math
from datetime import datetime, timezone, timedelta

import aiohttp

from db import get_db
from services.derivatives_service import SYMBOLS

log = logging.getLogger("options")

_task: asyncio.Task | None = None

DERIBIT_BASE = "https://www.deribit.com/api/v2/public"
OPTIONS_CURRENCIES = ["BTC", "ETH"]
POLL_INTERVAL = 300  # 5 minutes


# ── Deribit DVOL (IV 30d) ────────────────────────────────────────────

async def _fetch_dvol(session: aiohttp.ClientSession, currency: str, start_ts: int, end_ts: int) -> list[dict]:
    """Fetch DVOL index candles (IV 30d) from Deribit."""
    try:
        async with session.get(
            f"{DERIBIT_BASE}/get_volatility_index_data",
            params={
                "currency": currency,
                "start_timestamp": start_ts,
                "end_timestamp": end_ts,
                "resolution": "1D",
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        rows = data.get("result", {}).get("data", [])
        result = []
        for row in rows:
            # [timestamp, open, high, low, close]
            ts, _, _, _, close = row
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            result.append({"date": dt, "iv_30d": close})
        return result
    except Exception as e:
        log.warning(f"DVOL fetch {currency}: {e}")
        return []


# ── Deribit Historical RV ────────────────────────────────────────────

async def _fetch_deribit_rv(session: aiohttp.ClientSession, currency: str) -> list[dict]:
    """Fetch historical realized volatility from Deribit."""
    try:
        async with session.get(
            f"{DERIBIT_BASE}/get_historical_volatility",
            params={"currency": currency},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        rows = data.get("result", [])
        result = []
        for row in rows:
            # [timestamp, rv_value]
            ts, rv = row
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            result.append({"date": dt, "rv_30d": rv})
        return result
    except Exception as e:
        log.warning(f"Deribit RV {currency}: {e}")
        return []


# ── 25d Skew ─────────────────────────────────────────────────────────

async def _fetch_skew_25d(session: aiohttp.ClientSession, currency: str) -> float | None:
    """Compute 25-delta skew: IV(25d_put) - IV(25d_call) for nearest-30d expiry."""
    try:
        # Get all option book summaries
        async with session.get(
            f"{DERIBIT_BASE}/get_book_summary_by_currency",
            params={"currency": currency, "kind": "option"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
        summaries = data.get("result", [])
        if not summaries:
            return None

        # Find expiry closest to 30 days
        now = datetime.now(timezone.utc)
        target = now + timedelta(days=30)
        target_ts = target.timestamp() * 1000

        # Parse unique expiries from instrument names (e.g., BTC-28MAR25-60000-C)
        expiry_instruments: dict[str, list[dict]] = {}
        for s in summaries:
            name = s.get("instrument_name", "")
            parts = name.split("-")
            if len(parts) < 4:
                continue
            expiry_str = parts[1]
            mark_iv = s.get("mark_iv", 0)
            if mark_iv and mark_iv > 0:
                expiry_instruments.setdefault(expiry_str, []).append(s)

        if not expiry_instruments:
            return None

        # Find closest expiry to 30 days
        best_expiry = None
        best_diff = float("inf")
        for exp_str, instruments in expiry_instruments.items():
            # Parse expiry date
            try:
                exp_date = datetime.strptime(exp_str, "%d%b%y").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            diff = abs((exp_date - target).total_seconds())
            if diff < best_diff and len(instruments) >= 4:
                best_diff = diff
                best_expiry = exp_str

        if not best_expiry:
            return None

        # Get ticker data for instruments at this expiry to find deltas
        instruments = expiry_instruments[best_expiry]
        calls = []
        puts = []
        for s in instruments:
            name = s["instrument_name"]
            if name.endswith("-C"):
                calls.append(name)
            elif name.endswith("-P"):
                puts.append(name)

        # Fetch tickers for a sample of calls and puts to get greeks
        async def _get_delta(inst_name: str) -> tuple[str, float, float]:
            try:
                async with session.get(
                    f"{DERIBIT_BASE}/ticker",
                    params={"instrument_name": inst_name},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return inst_name, 0, 0
                    data = await resp.json()
                r = data.get("result", {})
                greeks = r.get("greeks", {})
                delta = greeks.get("delta", 0)
                iv = r.get("mark_iv", 0)
                return inst_name, delta, iv
            except Exception:
                return inst_name, 0, 0

        # Limit to 6 calls + 6 puts to avoid rate limits
        sample_calls = calls[:6]
        sample_puts = puts[:6]
        tasks = [_get_delta(n) for n in sample_calls + sample_puts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Find closest to 25-delta
        best_call_iv = None
        best_call_diff = float("inf")
        best_put_iv = None
        best_put_diff = float("inf")

        for r in results:
            if isinstance(r, Exception):
                continue
            name, delta, iv = r
            if iv <= 0:
                continue
            if name.endswith("-C") and delta > 0:
                diff = abs(delta - 0.25)
                if diff < best_call_diff:
                    best_call_diff = diff
                    best_call_iv = iv
            elif name.endswith("-P") and delta < 0:
                diff = abs(abs(delta) - 0.25)
                if diff < best_put_diff:
                    best_put_diff = diff
                    best_put_iv = iv

        if best_put_iv and best_call_iv:
            return round(best_put_iv - best_call_iv, 2)
        return None

    except Exception as e:
        log.warning(f"Skew fetch {currency}: {e}")
        return None


# ── RV computation from daily prices ─────────────────────────────────

async def _compute_rv_all():
    """Compute rolling 30d RV for all symbols from daily_derivatives close_price."""
    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for sym in SYMBOLS:
        rows = await db.execute_fetchall(
            """SELECT date, close_price FROM daily_derivatives
               WHERE symbol = ? AND close_price > 0
               ORDER BY date ASC""",
            (sym,),
        )
        if len(rows) < 31:
            continue

        prices = [r["close_price"] for r in rows]
        # Log returns
        log_returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]

        if len(log_returns) < 30:
            continue

        window = log_returns[-30:]
        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / len(window)
        rv_30d = math.sqrt(variance) * math.sqrt(365) * 100

        await db.execute(
            """INSERT INTO daily_rv (symbol, date, rv_30d)
               VALUES (?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET rv_30d=excluded.rv_30d""",
            (sym, today, round(rv_30d, 2)),
        )

    await db.commit()


# ── Backfill ─────────────────────────────────────────────────────────

async def _backfill_volatility():
    """Backfill DVOL 500d + Deribit historical RV for BTC/ETH."""
    db = get_db()
    now = datetime.now(timezone.utc)
    end_ts = int(now.timestamp() * 1000)
    start_ts = int((now - timedelta(days=500)).timestamp() * 1000)

    async with aiohttp.ClientSession() as session:
        for currency in OPTIONS_CURRENCIES:
            sym = f"{currency}USDT"

            # DVOL
            dvol_data = await _fetch_dvol(session, currency, start_ts, end_ts)
            # Deribit historical RV
            rv_data = await _fetch_deribit_rv(session, currency)

            rv_map = {r["date"]: r["rv_30d"] for r in rv_data}

            for item in dvol_data:
                dt = item["date"]
                iv = item["iv_30d"]
                rv = rv_map.get(dt)

                await db.execute(
                    """INSERT INTO daily_volatility (symbol, date, iv_30d, rv_30d, close_price)
                       VALUES (?, ?, ?, ?, (SELECT close_price FROM daily_derivatives WHERE symbol=? AND date=?))
                       ON CONFLICT(symbol, date) DO UPDATE SET
                         iv_30d=excluded.iv_30d,
                         rv_30d=COALESCE(excluded.rv_30d, daily_volatility.rv_30d)""",
                    (sym, dt, iv, rv, sym, dt),
                )

            # Save RV entries that don't have DVOL
            dvol_dates = {d["date"] for d in dvol_data}
            for r in rv_data:
                if r["date"] not in dvol_dates:
                    await db.execute(
                        """INSERT INTO daily_volatility (symbol, date, rv_30d, close_price)
                           VALUES (?, ?, ?, (SELECT close_price FROM daily_derivatives WHERE symbol=? AND date=?))
                           ON CONFLICT(symbol, date) DO UPDATE SET
                             rv_30d=COALESCE(excluded.rv_30d, daily_volatility.rv_30d)""",
                        (sym, r["date"], r["rv_30d"], sym, r["date"]),
                    )

            log.info(f"Volatility backfill {sym}: {len(dvol_data)} DVOL, {len(rv_data)} RV points")
            await asyncio.sleep(0.5)

    await db.commit()

    # Also backfill RV for all symbols
    await _backfill_rv_all()


async def _backfill_rv_all():
    """Compute historical rolling 30d RV for all symbols."""
    db = get_db()

    for sym in SYMBOLS:
        rows = await db.execute_fetchall(
            """SELECT date, close_price FROM daily_derivatives
               WHERE symbol = ? AND close_price > 0
               ORDER BY date ASC""",
            (sym,),
        )
        if len(rows) < 31:
            continue

        prices = [r["close_price"] for r in rows]
        dates = [r["date"] for r in rows]

        for i in range(30, len(prices)):
            window_prices = prices[i - 30:i + 1]
            log_returns = []
            for j in range(1, len(window_prices)):
                if window_prices[j - 1] > 0:
                    log_returns.append(math.log(window_prices[j] / window_prices[j - 1]))

            if len(log_returns) < 20:
                continue

            mean = sum(log_returns) / len(log_returns)
            variance = sum((x - mean) ** 2 for x in log_returns) / len(log_returns)
            rv = math.sqrt(variance) * math.sqrt(365) * 100

            await db.execute(
                """INSERT INTO daily_rv (symbol, date, rv_30d)
                   VALUES (?, ?, ?)
                   ON CONFLICT(symbol, date) DO UPDATE SET rv_30d=excluded.rv_30d""",
                (sym, dates[i], round(rv, 2)),
            )

        await db.commit()

    log.info("RV backfill complete for all symbols")


# ── Skew Z-Score computation ─────────────────────────────────────────

async def _update_skew_zscore(sym: str, current_skew: float):
    """Compute skew z-score from accumulated skew history."""
    db = get_db()
    rows = await db.execute_fetchall(
        """SELECT skew_25d FROM daily_volatility
           WHERE symbol = ? AND skew_25d IS NOT NULL
           ORDER BY date ASC""",
        (sym,),
    )
    vals = [r["skew_25d"] for r in rows]
    if len(vals) < 7:
        return 0.0

    mean = sum(vals) / len(vals)
    std = (sum((x - mean) ** 2 for x in vals) / len(vals)) ** 0.5
    if std == 0:
        return 0.0
    return round((current_skew - mean) / std, 4)


# ── Poll loop ────────────────────────────────────────────────────────

async def _poll_loop():
    log.info("Options polling started")

    # Backfill on first run
    try:
        await _backfill_volatility()
    except Exception as e:
        log.error(f"Volatility backfill error: {e}")

    while True:
        try:
            db = get_db()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
            day_start_ts = now_ts - 86400_000

            async with aiohttp.ClientSession() as session:
                for currency in OPTIONS_CURRENCIES:
                    sym = f"{currency}USDT"

                    # Fetch today's DVOL
                    dvol = await _fetch_dvol(session, currency, day_start_ts, now_ts)
                    iv_today = dvol[-1]["iv_30d"] if dvol else None

                    # Fetch current skew
                    skew = await _fetch_skew_25d(session, currency)

                    # Compute skew z-score
                    skew_z = None
                    if skew is not None:
                        skew_z = await _update_skew_zscore(sym, skew)

                    # Get current price
                    price_row = await db.execute_fetchall(
                        "SELECT close_price FROM daily_derivatives WHERE symbol=? ORDER BY date DESC LIMIT 1",
                        (sym,),
                    )
                    price = price_row[0]["close_price"] if price_row else None

                    await db.execute(
                        """INSERT INTO daily_volatility (symbol, date, iv_30d, skew_25d, skew_25d_zscore, close_price)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(symbol, date) DO UPDATE SET
                             iv_30d=COALESCE(excluded.iv_30d, daily_volatility.iv_30d),
                             skew_25d=COALESCE(excluded.skew_25d, daily_volatility.skew_25d),
                             skew_25d_zscore=COALESCE(excluded.skew_25d_zscore, daily_volatility.skew_25d_zscore),
                             close_price=COALESCE(excluded.close_price, daily_volatility.close_price)""",
                        (sym, today, iv_today, skew, skew_z, price),
                    )
                    await asyncio.sleep(1)

            # Compute RV for all symbols
            await _compute_rv_all()
            await db.commit()

            log.info("Options poll complete")
        except Exception as e:
            log.error(f"Options poll error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


# ── API responses ────────────────────────────────────────────────────

async def get_momentum_data(symbol: str, days: int = 365) -> dict:
    """Return momentum data for a symbol."""
    db = get_db()
    sym = symbol.upper()
    cutoff = f"-{days}"
    has_options = sym in ("BTCUSDT", "ETHUSDT")

    # Price + RV for all symbols
    rv_rows = await db.execute_fetchall(
        """SELECT r.date, r.rv_30d, d.close_price as price
           FROM daily_rv r
           JOIN daily_derivatives d ON r.symbol = d.symbol AND r.date = d.date
           WHERE r.symbol = ? AND r.date >= date('now', ? || ' days')
           ORDER BY r.date ASC""",
        (sym, cutoff),
    )
    price_rv = [{"date": r["date"], "price": r["price"], "rv_30d": r["rv_30d"]} for r in rv_rows]

    iv_rv: list[dict] = []
    skew_zscore: list[dict] = []

    if has_options:
        # IV + RV for BTC/ETH
        vol_rows = await db.execute_fetchall(
            """SELECT v.date, v.iv_30d, v.rv_30d, v.skew_25d, v.skew_25d_zscore, v.close_price as price
               FROM daily_volatility v
               WHERE v.symbol = ? AND v.date >= date('now', ? || ' days')
               ORDER BY v.date ASC""",
            (sym, cutoff),
        )
        iv_rv = [
            {
                "date": r["date"],
                "price": r["price"] or 0,
                "iv_30d": r["iv_30d"],
                "rv_30d": r["rv_30d"],
            }
            for r in vol_rows
            if r["iv_30d"] is not None
        ]
        skew_zscore = [
            {
                "date": r["date"],
                "price": r["price"] or 0,
                "skew_25d": r["skew_25d"],
                "skew_zscore": r["skew_25d_zscore"],
            }
            for r in vol_rows
            if r["skew_25d"] is not None
        ]

    return {
        "symbol": sym,
        "has_options_data": has_options,
        "price_rv": price_rv,
        "iv_rv": iv_rv,
        "skew_zscore": skew_zscore,
    }


# ── Service lifecycle ────────────────────────────────────────────────

def start():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll_loop())


def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
