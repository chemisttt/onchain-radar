import asyncio
import logging
import time as _time
import aiohttp
from datetime import datetime, timezone, timedelta

from db import get_db
from services import funding_service

log = logging.getLogger("derivatives")

_task: asyncio.Task | None = None

# Top 30 symbols to track
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT", "AAVEUSDT", "DOTUSDT",
    "FILUSDT", "ATOMUSDT", "TRXUSDT", "JUPUSDT", "SEIUSDT", "TIAUSDT",
    "INJUSDT", "TRUMPUSDT", "WIFUSDT", "TONUSDT", "RENDERUSDT", "ENAUSDT",
]

# Binance uses 1000x symbols for low-price tokens
BINANCE_SYMBOL_MAP = {
    "1000PEPEUSDT": "PEPEUSDT",
    "1000SHIBUSDT": "SHIBUSDT",
}

POLL_INTERVAL = 300  # 5 minutes
_FOUR_HOURS = 14400  # 4h in seconds
_last_4h_ts: int = 0  # track last 4h snapshot boundary

# ── In-memory cache ──────────────────────────────────────────────────

_cached_screener: list[dict] = []
_cache_ts: float = 0
_CACHE_TTL = 45
_fetch_lock: asyncio.Lock | None = None

# ── Exchange OI anomaly detection ────────────────────────────────────
_prev_exchange_oi: dict[str, dict[str, float]] = {}  # sym → {exchange: oi_usd}
_exchange_anomalies: list[dict] = []


def get_exchange_anomalies() -> list[dict]:
    """Pop and return accumulated exchange OI anomalies."""
    global _exchange_anomalies
    result = _exchange_anomalies
    _exchange_anomalies = []
    return result


def _fmt_usd_log(val: float) -> str:
    if abs(val) >= 1e9:
        return f"${val / 1e9:.1f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:.0f}M"
    return f"${val:,.0f}"


def _get_lock() -> asyncio.Lock:
    global _fetch_lock
    if _fetch_lock is None:
        _fetch_lock = asyncio.Lock()
    return _fetch_lock


# ── OI Fetchers ──────────────────────────────────────────────────────

async def _fetch_binance_oi(session: aiohttp.ClientSession) -> dict[str, dict]:
    """Fetch OI + price + volume from Binance futures."""
    result: dict[str, dict] = {}
    try:
        # OI per symbol
        async with session.get(
            "https://fapi.binance.com/fapi/v1/openInterest",
            params={"symbol": "BTCUSDT"},  # dummy — we'll use ticker for all
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            pass  # individual endpoint, use batch below

        # Batch: ticker/24hr has price + volume for all symbols
        async with session.get(
            "https://fapi.binance.com/fapi/v1/ticker/24hr",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return {}
            tickers = await resp.json()

        ticker_map = {}
        for t in tickers:
            sym = t.get("symbol", "")
            # Map 1000x symbols to normalized names
            norm = BINANCE_SYMBOL_MAP.get(sym, sym)
            if norm in SYMBOLS:
                ticker_map[norm] = {
                    "price": float(t.get("lastPrice", 0) or 0),
                    "volume_usd": float(t.get("quoteVolume", 0) or 0),
                    "binance_symbol": sym,  # keep original for OI query
                }

        # OI for each symbol (batch via individual calls, gathered)
        async def _get_oi(sym: str):
            try:
                # Use the original Binance symbol (e.g., 1000PEPEUSDT) for API call
                binance_sym = ticker_map.get(sym, {}).get("binance_symbol", sym)
                async with session.get(
                    "https://fapi.binance.com/fapi/v1/openInterest",
                    params={"symbol": binance_sym},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return sym, 0
                    data = await resp.json()
                    oi_qty = float(data.get("openInterest", 0) or 0)
                    price = ticker_map.get(sym, {}).get("price", 0)
                    return sym, oi_qty * price
            except Exception:
                return sym, 0

        oi_tasks = [_get_oi(sym) for sym in SYMBOLS if sym in ticker_map]
        oi_results = await asyncio.gather(*oi_tasks, return_exceptions=True)

        for r in oi_results:
            if isinstance(r, tuple):
                sym, oi_usd = r
                info = ticker_map.get(sym, {})
                result[sym] = {
                    "oi_usd": oi_usd,
                    "price": info.get("price", 0),
                    "volume_usd": info.get("volume_usd", 0),
                }
    except Exception as e:
        log.warning(f"Binance OI error: {e}")
    return result


async def _fetch_bybit_oi(session: aiohttp.ClientSession) -> dict[str, dict]:
    """Fetch OI from Bybit. Returns OI in USD (openInterestValue field available)."""
    result: dict[str, dict] = {}
    try:
        for sym in SYMBOLS:
            try:
                async with session.get(
                    "https://api.bybit.com/v5/market/open-interest",
                    params={"category": "linear", "symbol": sym, "intervalTime": "5min", "limit": 1},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                items = data.get("result", {}).get("list", [])
                if items:
                    oi_val = float(items[0].get("openInterest", 0) or 0)
                    # Bybit returns OI in coins, need price for USD conversion
                    # We'll merge with Binance prices later
                    result[sym] = {"oi_coins": oi_val}
            except Exception:
                continue
            await asyncio.sleep(0.05)
    except Exception as e:
        log.warning(f"Bybit OI error: {e}")
    return result


async def _fetch_okx_oi(session: aiohttp.ClientSession) -> dict[str, dict]:
    """Fetch OI from OKX."""
    result: dict[str, dict] = {}
    try:
        for sym in SYMBOLS:
            inst_id = sym.replace("USDT", "-USDT-SWAP")
            try:
                async with session.get(
                    "https://www.okx.com/api/v5/public/open-interest",
                    params={"instType": "SWAP", "instId": inst_id},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                items = data.get("data", [])
                if items:
                    # oiCcy = OI in currency (coins), oi = contract count (NOT coins)
                    oi_coins = float(items[0].get("oiCcy", 0) or 0)
                    result[sym] = {"oi_coins": oi_coins}
            except Exception:
                continue
            await asyncio.sleep(0.05)
    except Exception as e:
        log.warning(f"OKX OI error: {e}")
    return result


async def _fetch_bitget_oi(session: aiohttp.ClientSession) -> dict[str, dict]:
    """Fetch OI from Bitget."""
    result: dict[str, dict] = {}
    try:
        for sym in SYMBOLS:
            # Bitget uses different symbol format
            bitget_sym = sym  # Try direct first
            try:
                async with session.get(
                    "https://api.bitget.com/api/v2/mix/market/open-interest",
                    params={"symbol": bitget_sym, "productType": "USDT-FUTURES"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                items = data.get("data", [])
                if items:
                    oi_usd = float(items[0].get("openInterestUsd", 0) or 0)
                    if oi_usd == 0:
                        # Try coins * price
                        oi_coins = float(items[0].get("openInterest", 0) or 0)
                        result[sym] = {"oi_coins": oi_coins}
                    else:
                        result[sym] = {"oi_usd": oi_usd}
            except Exception:
                continue
            await asyncio.sleep(0.05)
    except Exception as e:
        log.warning(f"Bitget OI error: {e}")
    return result


# ── Liquidations ─────────────────────────────────────────────────────

async def _fetch_liquidations(session: aiohttp.ClientSession) -> dict[str, dict]:
    """Fetch taker buy/sell volume from Binance as liquidation proxy.
    allForceOrders is deprecated — use takerlongshortRatio instead.
    buyVol = aggressive longs (proxy for long liquidation pressure when falling),
    sellVol = aggressive shorts (proxy for short liquidation pressure when rising).
    Delta = buyVol - sellVol → positive = net long aggression.
    """
    result: dict[str, dict] = {}
    try:
        for sym in SYMBOLS:
            try:
                async with session.get(
                    "https://fapi.binance.com/futures/data/takerlongshortRatio",
                    params={"symbol": sym, "period": "1d", "limit": 1},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                if data:
                    item = data[0]
                    buy_vol = float(item.get("buyVol", 0) or 0)
                    sell_vol = float(item.get("sellVol", 0) or 0)
                    result[sym] = {"long": buy_vol, "short": sell_vol}
            except Exception:
                continue
            await asyncio.sleep(0.05)
    except Exception as e:
        log.warning(f"Taker ratio fetch error: {e}")
    return result


# ── Funding (from existing service) ─────────────────────────────────

async def _get_funding_by_symbol() -> dict[str, float]:
    """Get average funding rate per symbol from funding_service cache."""
    rates = await funding_service.fetch_all_rates()
    by_sym: dict[str, list[float]] = {}
    for r in rates:
        sym = r["symbol"]
        if sym in SYMBOLS:
            # Normalize to 8h equivalent
            settlement = r.get("settlement_hours", 8)
            rate = r["rate"]
            if settlement == 1:
                rate *= 8
            by_sym.setdefault(sym, []).append(rate)

    return {sym: sum(rs) / len(rs) for sym, rs in by_sym.items() if rs}


# ── Z-Score computation ─────────────────────────────────────────────

def _compute_zscore(values: list[float], window: int = 365) -> tuple[float, float]:
    """Compute z-score and percentile for the last value in a rolling window."""
    data = values[-window:]
    n = len(data)
    if n < 7:
        return 0.0, 50.0
    mean = sum(data) / n
    std = (sum((x - mean) ** 2 for x in data) / n) ** 0.5
    if std == 0:
        return 0.0, 50.0
    z = (data[-1] - mean) / std
    pct = sum(1 for x in data if x < data[-1]) / n * 100
    return round(z, 4), round(pct, 2)


async def _compute_all_zscores():
    """Compute z-scores for all symbols using daily_derivatives history."""
    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for sym in SYMBOLS:
        rows = await db.execute_fetchall(
            """SELECT date, open_interest_usd, oi_binance_usd, funding_rate,
                      liquidations_delta, volume_usd, close_price
               FROM daily_derivatives
               WHERE symbol = ?
               ORDER BY date ASC""",
            (sym,),
        )
        if len(rows) < 7:
            continue

        # Use Binance-only OI for z-scores (fallback to aggregate for old data)
        oi_vals = [(r["oi_binance_usd"] or 0) or (r["open_interest_usd"] or 0) for r in rows]
        fund_vals = [r["funding_rate"] or 0 for r in rows]
        liq_vals = [abs(r["liquidations_delta"] or 0) for r in rows]
        vol_vals = [r["volume_usd"] or 0 for r in rows]
        prices = [r["close_price"] or 0 for r in rows]

        # Filter out 0-OI rows for z-score (backfill gaps)
        oi_nonzero = [v for v in oi_vals if v > 0]
        oi_z, oi_p = _compute_zscore(oi_nonzero) if len(oi_nonzero) >= 7 else (0.0, 50.0)
        fund_z, fund_p = _compute_zscore(fund_vals)
        liq_z, liq_p = _compute_zscore(liq_vals)
        vol_z, vol_p = _compute_zscore(vol_vals)

        # 24h changes — Binance-only OI, consecutive days only
        oi_change = 0.0
        price_change = 0.0
        if len(rows) >= 2:
            prev_date = rows[-2]["date"]
            curr_date = rows[-1]["date"]
            try:
                from datetime import date as _date
                d1 = _date.fromisoformat(prev_date)
                d2 = _date.fromisoformat(curr_date)
                if (d2 - d1).days <= 1:
                    if oi_vals[-2] > 0:
                        oi_change = (oi_vals[-1] - oi_vals[-2]) / oi_vals[-2] * 100
                    if prices[-2] > 0:
                        price_change = (prices[-1] - prices[-2]) / prices[-2] * 100
            except Exception:
                pass

        await db.execute(
            """INSERT INTO derivatives_zscores
               (symbol, date, oi_zscore, funding_zscore, liq_zscore, volume_zscore,
                oi_percentile, funding_percentile, liq_percentile, volume_percentile,
                oi_change_24h_pct, price_change_24h_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET
                 oi_zscore=excluded.oi_zscore, funding_zscore=excluded.funding_zscore,
                 liq_zscore=excluded.liq_zscore, volume_zscore=excluded.volume_zscore,
                 oi_percentile=excluded.oi_percentile, funding_percentile=excluded.funding_percentile,
                 liq_percentile=excluded.liq_percentile, volume_percentile=excluded.volume_percentile,
                 oi_change_24h_pct=excluded.oi_change_24h_pct, price_change_24h_pct=excluded.price_change_24h_pct""",
            (sym, today, oi_z, fund_z, liq_z, vol_z, oi_p, fund_p, liq_p, vol_p,
             round(oi_change, 2), round(price_change, 2)),
        )
    await db.commit()


# ── Data aggregation + save ──────────────────────────────────────────

async def _fetch_and_save():
    """Main poll cycle: fetch OI from 4 exchanges, liquidations, funding, save daily row, recompute z-scores."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = get_db()

    async with aiohttp.ClientSession() as session:
        # Concurrent fetch from all sources
        bn_oi, bb_oi, okx_oi, bg_oi, liqs = await asyncio.gather(
            _fetch_binance_oi(session),
            _fetch_bybit_oi(session),
            _fetch_okx_oi(session),
            _fetch_bitget_oi(session),
            _fetch_liquidations(session),
            return_exceptions=True,
        )

    # Handle exceptions
    if isinstance(bn_oi, Exception):
        log.warning(f"Binance OI exception: {bn_oi}")
        bn_oi = {}
    if isinstance(bb_oi, Exception):
        log.warning(f"Bybit OI exception: {bb_oi}")
        bb_oi = {}
    if isinstance(okx_oi, Exception):
        log.warning(f"OKX OI exception: {okx_oi}")
        okx_oi = {}
    if isinstance(bg_oi, Exception):
        log.warning(f"Bitget OI exception: {bg_oi}")
        bg_oi = {}
    if isinstance(liqs, Exception):
        log.warning(f"Liquidations exception: {liqs}")
        liqs = {}

    # Get funding rates
    funding_map = await _get_funding_by_symbol()

    # Build per-symbol aggregated data
    symbol_data: dict[str, dict] = {}
    for sym in SYMBOLS:
        total_oi = 0.0
        price = 0.0
        volume = 0.0

        bn = bn_oi.get(sym, {})
        if bn:
            total_oi += bn.get("oi_usd", 0)
            price = bn.get("price", 0)
            volume = bn.get("volume_usd", 0)

        if price > 0:
            for exchange_oi in [bb_oi, okx_oi, bg_oi]:
                ex = exchange_oi.get(sym, {})
                if "oi_usd" in ex:
                    total_oi += ex["oi_usd"]
                elif "oi_coins" in ex:
                    total_oi += ex["oi_coins"] * price

        liq = liqs.get(sym, {})
        liq_long = liq.get("long", 0)
        liq_short = liq.get("short", 0)
        liq_delta = liq_long - liq_short
        funding = funding_map.get(sym, 0)

        if total_oi > 0 or liq_long > 0 or liq_short > 0:
            symbol_data[sym] = {
                "price": price, "oi": total_oi, "funding": funding,
                "volume": volume, "liq_long": liq_long,
                "liq_short": liq_short, "liq_delta": liq_delta,
                "oi_bn": bn.get("oi_usd", 0) if bn else 0,
            }

    # Daily upsert
    saved = 0
    for sym, sd in symbol_data.items():
        await db.execute(
            """INSERT INTO daily_derivatives
               (symbol, date, close_price, open_interest_usd, funding_rate,
                liquidations_long, liquidations_short, liquidations_delta, volume_usd,
                oi_binance_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET
                 close_price = excluded.close_price,
                 open_interest_usd = excluded.open_interest_usd,
                 funding_rate = excluded.funding_rate,
                 liquidations_long = daily_derivatives.liquidations_long + excluded.liquidations_long,
                 liquidations_short = daily_derivatives.liquidations_short + excluded.liquidations_short,
                 liquidations_delta = daily_derivatives.liquidations_delta + excluded.liquidations_delta,
                 volume_usd = excluded.volume_usd,
                 oi_binance_usd = excluded.oi_binance_usd,
                 fetched_at = datetime('now')""",
            (sym, today, sd["price"], sd["oi"], sd["funding"],
             sd["liq_long"], sd["liq_short"], sd["liq_delta"], sd["volume"],
             sd["oi_bn"]),
        )
        saved += 1

    # 4h snapshot — check if we crossed a new 4h boundary
    global _last_4h_ts
    now_s = int(_time.time())
    snap_ts = (now_s // _FOUR_HOURS) * _FOUR_HOURS
    if snap_ts != _last_4h_ts:
        snap_ts_ms = snap_ts * 1000
        for sym, sd in symbol_data.items():
            await db.execute(
                """INSERT OR IGNORE INTO derivatives_4h
                   (symbol, ts, close_price, open_interest_usd, funding_rate,
                    liquidations_long, liquidations_short, liquidations_delta, volume_usd,
                    oi_binance_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sym, snap_ts_ms, sd["price"], sd["oi"], sd["funding"],
                 sd["liq_long"], sd["liq_short"], sd["liq_delta"], sd["volume"],
                 sd["oi_bn"]),
            )
        _last_4h_ts = snap_ts
        log.info(f"4h snapshot saved at {snap_ts}")

    await db.commit()

    # ── Exchange OI anomaly detection ─────────────────────────────────
    global _prev_exchange_oi, _exchange_anomalies
    exchange_sources = {"binance": bn_oi, "bybit": bb_oi, "okx": okx_oi, "bitget": bg_oi}
    for sym in SYMBOLS:
        current_ois: dict[str, float] = {}
        bn = bn_oi.get(sym, {})
        bn_current = bn.get("oi_usd", 0) if bn else 0
        for ex_name, ex_data in exchange_sources.items():
            ex = ex_data.get(sym, {})
            if not ex:
                continue
            oi = ex.get("oi_usd", 0)
            if oi <= 0 and "oi_coins" in ex and symbol_data.get(sym, {}).get("price", 0) > 0:
                oi = ex["oi_coins"] * symbol_data[sym]["price"]
            if oi > 0:
                current_ois[ex_name] = oi

        prev = _prev_exchange_oi.get(sym, {})
        if prev:
            for ex_name, cur_oi in current_ois.items():
                if ex_name == "binance":
                    continue
                prev_oi = prev.get(ex_name, 0)
                if prev_oi <= 0:
                    continue
                delta = cur_oi - prev_oi
                change_pct = delta / prev_oi * 100
                # Anomaly: one exchange OI delta > $200M AND change > 15%, Binance stable
                if abs(delta) > 200_000_000 and abs(change_pct) > 15:
                    bn_prev = prev.get("binance", 0)
                    bn_change_pct = ((bn_current - bn_prev) / bn_prev * 100) if bn_prev > 0 else 0
                    if abs(bn_change_pct) < 3:
                        _exchange_anomalies.append({
                            "symbol": sym,
                            "exchange": ex_name,
                            "prev": prev_oi,
                            "current": cur_oi,
                            "delta": delta,
                            "change_pct": change_pct,
                            "bn_change_pct": bn_change_pct,
                        })
                        log.warning(
                            f"Exchange OI anomaly: {sym} {ex_name} "
                            f"{_fmt_usd_log(prev_oi)} → {_fmt_usd_log(cur_oi)} "
                            f"({change_pct:+.1f}%), Binance stable ({bn_change_pct:+.1f}%)"
                        )
        _prev_exchange_oi[sym] = current_ois

    # Recompute z-scores
    await _compute_all_zscores()

    return saved


# ── Backfill (Binance OI history + klines) ───────────────────────────

def _binance_sym(sym: str) -> str:
    """Get original Binance symbol (e.g., 1000PEPEUSDT) from normalized symbol."""
    for bn_sym, norm_sym in BINANCE_SYMBOL_MAP.items():
        if norm_sym == sym:
            return bn_sym
    return sym


async def _backfill():
    """Backfill historical data from Binance: klines + OI + funding + taker ratio.
    Then compute rolling z-scores for all dates."""
    db = get_db()

    # Check per-symbol: only backfill symbols missing funding history
    # Note: OI/taker APIs only provide ~30 days history, so we check funding (500+ days available)
    needs_funding = await db.execute_fetchall(
        """SELECT symbol, COUNT(*) as cnt FROM daily_derivatives
           WHERE funding_rate != 0 AND funding_rate IS NOT NULL
           GROUP BY symbol"""
    )
    funded = {r["symbol"]: r["cnt"] for r in needs_funding}
    symbols_needed = [s for s in SYMBOLS if funded.get(s, 0) < 90]

    if not symbols_needed:
        log.info("Derivatives backfill skipped: all symbols have 90+ days funding data")
        return

    log.info(f"Starting derivatives backfill for {len(symbols_needed)} symbols...")

    start_ts = int((datetime.now(timezone.utc) - timedelta(days=500)).timestamp() * 1000)

    async with aiohttp.ClientSession() as session:
        for sym in symbols_needed:
            bn_sym = _binance_sym(sym)
            try:
                await _backfill_symbol(session, db, sym, bn_sym, start_ts)
            except Exception as e:
                log.warning(f"Backfill {sym}: {e}")
            await asyncio.sleep(0.5)

    await db.commit()
    log.info("Derivatives backfill complete, computing rolling z-scores...")
    await _backfill_rolling_zscores()


async def _backfill_symbol(
    session: aiohttp.ClientSession, db, sym: str, bn_sym: str, start_ts: int
):
    """Backfill a single symbol: klines + OI + funding + taker ratio."""
    timeout = aiohttp.ClientTimeout(total=20)

    # 1. Klines (price + volume, up to 500 days)
    async with session.get(
        "https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": bn_sym, "interval": "1d", "limit": 500, "startTime": start_ts},
        timeout=timeout,
    ) as resp:
        klines = await resp.json() if resp.status == 200 else []

    # 2. OI history (daily, max ~30 days — API doesn't support startTime/pagination)
    async with session.get(
        "https://fapi.binance.com/futures/data/openInterestHist",
        params={"symbol": bn_sym, "period": "1d", "limit": 500},
        timeout=timeout,
    ) as resp:
        oi_data = await resp.json() if resp.status == 200 else []
        if not isinstance(oi_data, list):
            oi_data = []

    # 3. Funding rate history (paginated, up to ~666 days)
    funding_all: list[dict] = []
    page_ts = start_ts
    for _ in range(2):
        async with session.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": bn_sym, "limit": 1000, "startTime": page_ts},
            timeout=timeout,
        ) as resp:
            if resp.status != 200:
                break
            page = await resp.json()
            if not page:
                break
            funding_all.extend(page)
            page_ts = int(page[-1].get("fundingTime", 0)) + 1
        await asyncio.sleep(0.1)

    # 4. Taker long/short ratio (daily, max ~30 days — API doesn't support startTime)
    async with session.get(
        "https://fapi.binance.com/futures/data/takerlongshortRatio",
        params={"symbol": bn_sym, "period": "1d", "limit": 500},
        timeout=timeout,
    ) as resp:
        taker_data = await resp.json() if resp.status == 200 else []
        if not isinstance(taker_data, list):
            taker_data = []

    # Build maps
    oi_map: dict[str, float] = {}
    for item in oi_data:
        ts = int(item.get("timestamp", 0))
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        oi_map[dt] = float(item.get("sumOpenInterestValue", 0) or 0)

    # Funding: aggregate 3x daily rates to single daily average
    funding_daily: dict[str, list[float]] = {}
    for item in funding_all:
        ts = int(item.get("fundingTime", 0))
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        rate = float(item.get("fundingRate", 0) or 0)
        funding_daily.setdefault(dt, []).append(rate)
    fund_map = {dt: sum(rs) / len(rs) for dt, rs in funding_daily.items()}

    # Taker ratio
    taker_map: dict[str, tuple[float, float]] = {}
    for item in taker_data:
        ts = int(item.get("timestamp", 0))
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        buy_vol = float(item.get("buyVol", 0) or 0)
        sell_vol = float(item.get("sellVol", 0) or 0)
        taker_map[dt] = (buy_vol, sell_vol)

    # Save: klines as base, merge OI + funding + taker
    saved = 0
    for k in klines:
        ts = int(k[0])
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        close_price = float(k[4])
        volume = float(k[7])
        oi_usd = oi_map.get(dt, 0)
        funding = fund_map.get(dt, 0)
        taker = taker_map.get(dt, (0, 0))
        liq_long, liq_short = taker
        liq_delta = liq_long - liq_short

        await db.execute(
            """INSERT INTO daily_derivatives
               (symbol, date, close_price, open_interest_usd, funding_rate,
                liquidations_long, liquidations_short, liquidations_delta, volume_usd,
                oi_binance_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET
                 close_price = COALESCE(excluded.close_price, daily_derivatives.close_price),
                 open_interest_usd = CASE WHEN excluded.open_interest_usd > 0
                                          THEN excluded.open_interest_usd
                                          ELSE daily_derivatives.open_interest_usd END,
                 funding_rate = CASE WHEN excluded.funding_rate != 0
                                     THEN excluded.funding_rate
                                     ELSE daily_derivatives.funding_rate END,
                 liquidations_long = CASE WHEN excluded.liquidations_long > 0
                                          THEN excluded.liquidations_long
                                          ELSE daily_derivatives.liquidations_long END,
                 liquidations_short = CASE WHEN excluded.liquidations_short > 0
                                           THEN excluded.liquidations_short
                                           ELSE daily_derivatives.liquidations_short END,
                 liquidations_delta = CASE WHEN excluded.liquidations_long > 0
                                           THEN excluded.liquidations_delta
                                           ELSE daily_derivatives.liquidations_delta END,
                 volume_usd = COALESCE(excluded.volume_usd, daily_derivatives.volume_usd),
                 oi_binance_usd = CASE WHEN excluded.oi_binance_usd > 0
                                       THEN excluded.oi_binance_usd
                                       ELSE daily_derivatives.oi_binance_usd END""",
            (sym, dt, close_price, oi_usd, funding, liq_long, liq_short, liq_delta, volume, oi_usd),
        )
        saved += 1

    await db.commit()
    log.info(f"Backfill {sym}: {saved} days, OI={len(oi_data)} fund={len(funding_all)} taker={len(taker_data)}")


async def _backfill_rolling_zscores():
    """Compute rolling z-scores for ALL historical dates (fills in z-score chart history).
    Filters out 0-values for OI (backfill gaps) to avoid extreme z-scores."""
    db = get_db()
    computed = 0

    for sym in SYMBOLS:
        rows = await db.execute_fetchall(
            """SELECT date, close_price, open_interest_usd, oi_binance_usd,
                      funding_rate, liquidations_delta, volume_usd
               FROM daily_derivatives
               WHERE symbol = ?
               ORDER BY date ASC""",
            (sym,),
        )
        if len(rows) < 7:
            continue

        # Use Binance-only OI with fallback to aggregate for old data
        oi_all = [(r["oi_binance_usd"] or 0) or (r["open_interest_usd"] or 0) for r in rows]
        fund_all = [r["funding_rate"] or 0 for r in rows]
        liq_all = [abs(r["liquidations_delta"] or 0) for r in rows]
        vol_all = [r["volume_usd"] or 0 for r in rows]
        prices = [r["close_price"] or 0 for r in rows]

        for i in range(6, len(rows)):
            date = rows[i]["date"]
            w = max(0, i - 364)

            # For OI: filter out 0s (backfill gaps where OI wasn't available)
            oi_window = [v for v in oi_all[w:i + 1] if v > 0]
            oi_z, oi_p = _compute_zscore(oi_window) if len(oi_window) >= 7 else (0.0, 50.0)

            fund_z, fund_p = _compute_zscore(fund_all[w:i + 1])
            liq_z, liq_p = _compute_zscore(liq_all[w:i + 1])
            vol_z, vol_p = _compute_zscore(vol_all[w:i + 1])

            # 24h changes
            oi_change = 0.0
            price_change = 0.0
            if i > 0 and oi_all[i - 1] > 0 and oi_all[i] > 0:
                oi_change = (oi_all[i] - oi_all[i - 1]) / oi_all[i - 1] * 100
            if i > 0 and prices[i - 1] > 0:
                price_change = (prices[i] - prices[i - 1]) / prices[i - 1] * 100

            await db.execute(
                """INSERT INTO derivatives_zscores
                   (symbol, date, oi_zscore, funding_zscore, liq_zscore, volume_zscore,
                    oi_percentile, funding_percentile, liq_percentile, volume_percentile,
                    oi_change_24h_pct, price_change_24h_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, date) DO UPDATE SET
                     oi_zscore=excluded.oi_zscore, funding_zscore=excluded.funding_zscore,
                     liq_zscore=excluded.liq_zscore, volume_zscore=excluded.volume_zscore,
                     oi_percentile=excluded.oi_percentile, funding_percentile=excluded.funding_percentile,
                     liq_percentile=excluded.liq_percentile, volume_percentile=excluded.volume_percentile,
                     oi_change_24h_pct=excluded.oi_change_24h_pct, price_change_24h_pct=excluded.price_change_24h_pct""",
                (sym, date, oi_z, fund_z, liq_z, vol_z, oi_p, fund_p, liq_p, vol_p,
                 round(oi_change, 2), round(price_change, 2)),
            )
            computed += 1

        await db.commit()

    log.info(f"Rolling z-scores: computed {computed} data points")


# ── 4h Backfill (Binance only — historical multi-exchange unavailable) ──

async def _paginated_fetch(
    session: aiohttp.ClientSession, url: str, params: dict,
    pages: int = 5, ts_field: str = "timestamp",
) -> list[dict]:
    """Fetch paginated Binance data using startTime."""
    all_data: list[dict] = []
    timeout = aiohttp.ClientTimeout(total=20)
    start_time = params.pop("startTime", None)

    for _ in range(pages):
        p = {**params}
        if start_time:
            p["startTime"] = start_time
        try:
            async with session.get(url, params=p, timeout=timeout) as resp:
                if resp.status != 200:
                    break
                page = await resp.json()
                if not page:
                    break
                all_data.extend(page)
                # Next page starts after last record
                last_ts = int(page[-1].get(ts_field, 0))
                start_time = last_ts + 1
                if len(page) < int(params.get("limit", 500)):
                    break  # last page
        except Exception:
            break
        await asyncio.sleep(0.1)
    return all_data


async def _paginated_klines(
    session: aiohttp.ClientSession, symbol: str, start_ts: int,
) -> list[list]:
    """Fetch paginated klines (up to 1500 per page)."""
    all_klines: list[list] = []
    timeout = aiohttp.ClientTimeout(total=20)
    cursor = start_ts

    for _ in range(3):  # 3 pages × 1500 = 4500 candles (~750 days)
        try:
            async with session.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": symbol, "interval": "4h", "limit": 1500, "startTime": cursor},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    break
                page = await resp.json()
                if not page:
                    break
                all_klines.extend(page)
                cursor = int(page[-1][0]) + 1
                if len(page) < 1500:
                    break
        except Exception:
            break
        await asyncio.sleep(0.1)
    return all_klines


async def _backfill_4h():
    """Backfill ~365 days of 4h derivatives data from Binance.
    Paginates OI hist, klines, taker ratio. ~3MB total for 30 symbols."""
    db = get_db()

    # Check if we already have enough data (~2000 per symbol = ~330 days)
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) as c FROM derivatives_4h"
    )
    cnt = rows[0]["c"] if rows else 0
    if cnt > 50000:  # 30 syms × ~2000 = 60k target
        log.info(f"4h backfill skipped: {cnt} rows already")
        return

    # Start from 400 days ago
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=400)).timestamp() * 1000)

    log.info("Starting 4h derivatives backfill (paginated, ~365 days)...")

    async with aiohttp.ClientSession() as session:
        for sym in SYMBOLS:
            bn_sym = _binance_sym(sym)
            try:
                # 1. Klines (paginated, up to 4500 = ~750 days)
                klines = await _paginated_klines(session, bn_sym, start_ts)
                await asyncio.sleep(0.1)

                # 2. OI history (paginated, 500 per page × 5 = 2500 = ~416 days)
                oi_data = await _paginated_fetch(
                    session,
                    "https://fapi.binance.com/futures/data/openInterestHist",
                    {"symbol": bn_sym, "period": "4h", "limit": 500, "startTime": start_ts},
                    pages=5,
                )
                await asyncio.sleep(0.1)

                # 3. Taker ratio (paginated, same)
                taker_data = await _paginated_fetch(
                    session,
                    "https://fapi.binance.com/futures/data/takerlongshortRatio",
                    {"symbol": bn_sym, "period": "4h", "limit": 500, "startTime": start_ts},
                    pages=5,
                )
                await asyncio.sleep(0.1)

                # Build maps by timestamp (ms)
                oi_map: dict[int, float] = {}
                for item in oi_data:
                    ts = int(item.get("timestamp", 0))
                    oi_map[ts] = float(item.get("sumOpenInterestValue", 0) or 0)

                taker_map: dict[int, tuple[float, float]] = {}
                for item in taker_data:
                    ts = int(item.get("timestamp", 0))
                    buy = float(item.get("buyVol", 0) or 0)
                    sell = float(item.get("sellVol", 0) or 0)
                    taker_map[ts] = (buy, sell)

                # Klines as base — INSERT OR IGNORE (don't overwrite live data)
                saved = 0
                for k in klines:
                    ts_ms = int(k[0])
                    close_price = float(k[4])
                    volume = float(k[7])
                    oi_usd = oi_map.get(ts_ms, 0)
                    liq_long, liq_short = taker_map.get(ts_ms, (0, 0))
                    liq_delta = liq_long - liq_short

                    await db.execute(
                        """INSERT OR IGNORE INTO derivatives_4h
                           (symbol, ts, close_price, open_interest_usd, funding_rate,
                            liquidations_long, liquidations_short, liquidations_delta, volume_usd)
                           VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)""",
                        (sym, ts_ms, close_price, oi_usd,
                         liq_long, liq_short, liq_delta, volume),
                    )
                    saved += 1

                await db.commit()
                log.info(f"4h backfill {sym}: {saved} candles, OI={len(oi_data)} taker={len(taker_data)}")

            except Exception as e:
                log.warning(f"4h backfill {sym}: {e}")
            await asyncio.sleep(0.3)

    log.info("4h derivatives backfill complete")


# ── Screener cache ───────────────────────────────────────────────────

async def get_screener(sort: str = "oi_zscore", limit: int = 50) -> list[dict]:
    """Return cached screener data. Refreshed by poll loop."""
    global _cached_screener, _cache_ts
    now = _time.time()
    if _cached_screener and now - _cache_ts < _CACHE_TTL:
        data = _cached_screener
    else:
        lock = _get_lock()
        if lock.locked():
            data = _cached_screener
        else:
            async with lock:
                data = await _build_screener()
                _cached_screener = data
                _cache_ts = _time.time()

    # Sort
    reverse = True
    if sort.startswith("-"):
        sort = sort[1:]
        reverse = False

    if sort in ("oi_zscore", "funding_zscore", "liq_zscore", "volume_zscore",
                "oi_percentile", "funding_percentile", "liq_percentile",
                "oi_change_24h_pct", "price_change_24h_pct"):
        data = sorted(data, key=lambda x: abs(x.get(sort, 0)), reverse=reverse)
    elif sort == "percentile_avg":
        data = sorted(data, key=lambda x: x.get("percentile_avg", 0), reverse=reverse)
    else:
        data = sorted(data, key=lambda x: abs(x.get("oi_zscore", 0)), reverse=True)

    return data[:limit]


async def _build_screener() -> list[dict]:
    """Build screener from daily_derivatives + derivatives_zscores."""
    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows = await db.execute_fetchall(
        """SELECT d.symbol, d.close_price, d.open_interest_usd, d.funding_rate,
                  d.liquidations_long, d.liquidations_short, d.liquidations_delta, d.volume_usd,
                  z.oi_zscore, z.funding_zscore, z.liq_zscore, z.volume_zscore,
                  z.oi_percentile, z.funding_percentile, z.liq_percentile, z.volume_percentile,
                  z.oi_change_24h_pct, z.price_change_24h_pct
           FROM daily_derivatives d
           LEFT JOIN derivatives_zscores z ON d.symbol = z.symbol AND z.date = (
               SELECT MAX(z2.date) FROM derivatives_zscores z2 WHERE z2.symbol = d.symbol
           )
           WHERE d.date = (SELECT MAX(d2.date) FROM daily_derivatives d2 WHERE d2.symbol = d.symbol)
           GROUP BY d.symbol
           ORDER BY d.symbol""",
    )

    result = []
    for r in rows:
        oi_z = r["oi_zscore"] or 0
        fund_z = r["funding_zscore"] or 0
        liq_z = r["liq_zscore"] or 0
        vol_z = r["volume_zscore"] or 0
        oi_p = r["oi_percentile"] or 50
        fund_p = r["funding_percentile"] or 50
        liq_p = r["liq_percentile"] or 50
        vol_p = r["volume_percentile"] or 50

        row_data = {
            "symbol": r["symbol"],
            "price": r["close_price"] or 0,
            "open_interest_usd": r["open_interest_usd"] or 0,
            "funding_rate": r["funding_rate"] or 0,
            "liquidations_long": r["liquidations_long"] or 0,
            "liquidations_short": r["liquidations_short"] or 0,
            "liquidations_delta": r["liquidations_delta"] or 0,
            "volume_usd": r["volume_usd"] or 0,
            "oi_zscore": oi_z,
            "funding_zscore": fund_z,
            "liq_zscore": liq_z,
            "volume_zscore": vol_z,
            "oi_percentile": oi_p,
            "funding_percentile": fund_p,
            "liq_percentile": liq_p,
            "volume_percentile": vol_p,
            "oi_change_24h_pct": r["oi_change_24h_pct"] or 0,
            "price_change_24h_pct": r["price_change_24h_pct"] or 0,
            "percentile_avg": round((oi_p + fund_p + liq_p + vol_p) / 4, 1),
            "ob_depth_usd": 0,
            "ob_skew": 0,
            "ob_skew_zscore": 0,
            "momentum_value": 0,
            "momentum_di": 0,
        }
        result.append(row_data)

    # Merge orderbook data
    try:
        from services import orderbook_service
        ob = orderbook_service.get_cache()
        for row_data in result:
            sym = row_data["symbol"]
            ob_data = ob.get(sym, {})
            row_data["ob_depth_usd"] = ob_data.get("ob_depth", 0)
            row_data["ob_skew"] = ob_data.get("ob_skew", 0)
            row_data["ob_skew_zscore"] = ob_data.get("ob_skew_zscore", 0)
    except Exception:
        pass

    # Merge momentum data
    try:
        mom_rows = await db.execute_fetchall(
            """SELECT symbol, momentum_value, directional_intensity
               FROM daily_momentum
               WHERE date = (SELECT MAX(date) FROM daily_momentum)"""
        )
        mom_map = {r["symbol"]: r for r in mom_rows}
        for row_data in result:
            m = mom_map.get(row_data["symbol"], {})
            row_data["momentum_value"] = m.get("momentum_value", 0) or 0
            row_data["momentum_di"] = m.get("directional_intensity", 0) or 0
    except Exception:
        pass

    return result


async def get_global_data(days: int = 365) -> dict:
    """Aggregated global data for the Global Dashboard."""
    db = get_db()
    cutoff = f"-{days}"

    # 1. Per-date aggregates: global OI (split BTC/ETH/Others), global liq delta
    #    Use Binance-only OI for charts to avoid aggregate spikes when exchanges connect
    rows = await db.execute_fetchall(
        """SELECT d.date,
                  SUM(CASE WHEN d.symbol='BTCUSDT' THEN COALESCE(NULLIF(d.oi_binance_usd, 0), d.open_interest_usd) ELSE 0 END) as btc_oi,
                  SUM(CASE WHEN d.symbol='ETHUSDT' THEN COALESCE(NULLIF(d.oi_binance_usd, 0), d.open_interest_usd) ELSE 0 END) as eth_oi,
                  SUM(CASE WHEN d.symbol NOT IN ('BTCUSDT','ETHUSDT') THEN COALESCE(NULLIF(d.oi_binance_usd, 0), d.open_interest_usd) ELSE 0 END) as others_oi,
                  SUM(COALESCE(NULLIF(d.oi_binance_usd, 0), d.open_interest_usd)) as total_oi,
                  SUM(d.liquidations_delta) as total_liq_delta
           FROM daily_derivatives d
           WHERE d.date >= date('now', ? || ' days')
           GROUP BY d.date
           ORDER BY d.date ASC""",
        (cutoff,),
    )

    global_oi = []
    global_liq = []
    total_oi_series = []
    alt_oi_dominance = []
    for r in rows:
        d = r["date"]
        total = r["total_oi"] or 0
        btc_oi = r["btc_oi"] or 0
        global_oi.append({
            "date": d,
            "btc": btc_oi,
            "eth": r["eth_oi"] or 0,
            "others": r["others_oi"] or 0,
            "total": total,
        })
        global_liq.append({"date": d, "value": r["total_liq_delta"] or 0})
        total_oi_series.append(total)
        alt_dom = round((total - btc_oi) / total * 100, 1) if total > 0 else 0
        alt_oi_dominance.append({"date": d, "value": alt_dom})

    # 2. Global OI Z-Score (rolling on total_oi_series)
    global_oi_zscore = []
    for i in range(len(total_oi_series)):
        if i < 6:
            global_oi_zscore.append({"date": rows[i]["date"], "zscore": 0})
            continue
        w = max(0, i - 364)
        window = [v for v in total_oi_series[w:i + 1] if v > 0]
        if len(window) < 7:
            global_oi_zscore.append({"date": rows[i]["date"], "zscore": 0})
            continue
        mean = sum(window) / len(window)
        std = (sum((x - mean) ** 2 for x in window) / len(window)) ** 0.5
        z = (total_oi_series[i] - mean) / std if std > 0 else 0
        global_oi_zscore.append({"date": rows[i]["date"], "zscore": round(z, 4)})

    # 3. Risk Appetite Index: avg composite z-score of top 10 by OI
    risk_rows = await db.execute_fetchall(
        """SELECT z.date,
                  AVG(z.oi_zscore + z.funding_zscore + z.liq_zscore) / 3.0 as composite
           FROM derivatives_zscores z
           WHERE z.symbol IN (
               SELECT symbol FROM daily_derivatives
               WHERE date = (SELECT MAX(date) FROM daily_derivatives)
               ORDER BY open_interest_usd DESC LIMIT 10
           )
           AND z.date >= date('now', ? || ' days')
           GROUP BY z.date
           ORDER BY z.date ASC""",
        (cutoff,),
    )
    risk_appetite = [{"date": r["date"], "value": round(r["composite"] or 0, 4)} for r in risk_rows]

    # 4. Performance: cumulative % change for top 10 symbols
    top_syms = await db.execute_fetchall(
        """SELECT symbol FROM daily_derivatives
           WHERE date = (SELECT MAX(date) FROM daily_derivatives)
           ORDER BY open_interest_usd DESC LIMIT 10"""
    )
    top_sym_list = [r["symbol"] for r in top_syms]

    performance: dict[str, list] = {}
    for sym in top_sym_list:
        price_rows = await db.execute_fetchall(
            """SELECT date, close_price FROM daily_derivatives
               WHERE symbol = ? AND date >= date('now', ? || ' days')
               ORDER BY date ASC""",
            (sym, cutoff),
        )
        if not price_rows or not price_rows[0]["close_price"]:
            continue
        base = price_rows[0]["close_price"]
        performance[sym] = [
            {"date": r["date"], "pct": round((r["close_price"] - base) / base * 100, 2) if r["close_price"] else 0}
            for r in price_rows
        ]

    # 5. Funding heatmap: last 30 days × all symbols
    heatmap_rows = await db.execute_fetchall(
        """SELECT symbol, date, funding_rate
           FROM daily_derivatives
           WHERE date >= date('now', '-30 days')
           ORDER BY symbol, date ASC"""
    )
    heatmap_by_sym: dict[str, list] = {}
    heatmap_dates: set[str] = set()
    for r in heatmap_rows:
        sym = r["symbol"]
        heatmap_by_sym.setdefault(sym, []).append({
            "date": r["date"], "rate": r["funding_rate"] or 0
        })
        heatmap_dates.add(r["date"])

    heatmap = [
        {"symbol": sym, "data": entries}
        for sym, entries in heatmap_by_sym.items()
    ]
    heatmap_dates_sorted = sorted(heatmap_dates)

    return {
        "global_oi": global_oi,
        "global_oi_zscore": global_oi_zscore,
        "global_liquidations": global_liq,
        "risk_appetite": risk_appetite,
        "alt_oi_dominance": alt_oi_dominance,
        "performance": performance,
        "funding_heatmap": heatmap,
        "heatmap_dates": heatmap_dates_sorted,
    }


async def get_symbol_detail(symbol: str, days: int = 365) -> dict:
    """Get detailed history for a single symbol."""
    db = get_db()
    sym = symbol.upper()

    # Latest data
    latest_rows = await db.execute_fetchall(
        """SELECT d.*, z.oi_zscore, z.funding_zscore, z.liq_zscore, z.volume_zscore,
                  z.oi_percentile, z.funding_percentile, z.liq_percentile, z.volume_percentile,
                  z.oi_change_24h_pct, z.price_change_24h_pct
           FROM daily_derivatives d
           LEFT JOIN derivatives_zscores z ON d.symbol = z.symbol AND d.date = z.date
           WHERE d.symbol = ?
           ORDER BY d.date DESC LIMIT 1""",
        (sym,),
    )

    latest = {}
    if latest_rows:
        r = latest_rows[0]
        latest = {
            "price": r["close_price"] or 0,
            "open_interest_usd": r["open_interest_usd"] or 0,
            "funding_rate": r["funding_rate"] or 0,
            "liquidations_long": r["liquidations_long"] or 0,
            "liquidations_short": r["liquidations_short"] or 0,
            "liquidations_delta": r["liquidations_delta"] or 0,
            "volume_usd": r["volume_usd"] or 0,
            "oi_zscore": r["oi_zscore"] or 0,
            "funding_zscore": r["funding_zscore"] or 0,
            "liq_zscore": r["liq_zscore"] or 0,
            "volume_zscore": r["volume_zscore"] or 0,
            "oi_percentile": r["oi_percentile"] or 50,
            "funding_percentile": r["funding_percentile"] or 50,
            "liq_percentile": r["liq_percentile"] or 50,
            "volume_percentile": r["volume_percentile"] or 50,
            "oi_change_24h_pct": r["oi_change_24h_pct"] or 0,
            "price_change_24h_pct": r["price_change_24h_pct"] or 0,
        }

    # History — use Binance-only OI for chart (avoids aggregate spikes)
    history_rows = await db.execute_fetchall(
        """SELECT d.date, d.close_price, d.open_interest_usd, d.oi_binance_usd,
                  d.funding_rate, d.liquidations_delta, d.volume_usd,
                  z.oi_zscore, z.funding_zscore, z.liq_zscore, z.volume_zscore
           FROM daily_derivatives d
           LEFT JOIN derivatives_zscores z ON d.symbol = z.symbol AND d.date = z.date
           WHERE d.symbol = ? AND d.date >= date('now', ? || ' days')
           ORDER BY d.date ASC""",
        (sym, f"-{days}"),
    )

    history = []
    for r in history_rows:
        oi = (r["oi_binance_usd"] or 0) or (r["open_interest_usd"] or 0)
        history.append({
            "date": r["date"],
            "price": r["close_price"] or 0,
            "oi": oi,
            "funding": r["funding_rate"] or 0,
            "liq_delta": r["liquidations_delta"] or 0,
            "volume": r["volume_usd"] or 0,
            "oi_zscore": r["oi_zscore"] or 0,
            "funding_zscore": r["funding_zscore"] or 0,
            "liq_zscore": r["liq_zscore"] or 0,
        })

    return {"symbol": sym, "latest": latest, "history": history}


# ── Poll loop ────────────────────────────────────────────────────────

async def _poll_loop():
    log.info("Derivatives polling started")

    # Backfill on first run
    try:
        await _backfill()
    except Exception as e:
        log.error(f"Derivatives backfill error: {e}")

    try:
        await _backfill_4h()
    except Exception as e:
        log.error(f"4h backfill error: {e}")

    while True:
        try:
            saved = await _fetch_and_save()
            # Refresh screener cache
            lock = _get_lock()
            async with lock:
                global _cached_screener, _cache_ts
                _cached_screener = await _build_screener()
                _cache_ts = _time.time()
            log.info(f"Derivatives: updated {saved} symbols")
        except Exception as e:
            log.error(f"Derivatives poll error: {e}")
        await asyncio.sleep(POLL_INTERVAL)


def start():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll_loop())


def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
