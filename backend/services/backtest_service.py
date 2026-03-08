"""Backtest service — replay alert conditions on historical data.

Walks daily_derivatives history, recomputes rolling z-scores at each day,
checks the same conditions as market_analyzer (+ relaxed variants), and returns
simulated alerts with actual forward returns computed from price data.
"""

import logging
from datetime import datetime, timedelta

from db import get_db

log = logging.getLogger("backtest_service")

# Same thresholds as market_analyzer
Z_MODERATE = 2.0
Z_STRONG = 3.0
CONFLUENCE_SETUP = 3
CONFLUENCE_SIGNAL = 4
CONFLUENCE_TRIGGER = 6
Z_WINDOW = 365
SMA_PERIOD = 20  # trend filter

MIN_POINTS = 30
MIN_POINTS_4H = 120  # ~20 days of 4h candles
COOLDOWN_DAYS = 1  # per symbol:type

TOP_OI_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "DOGEUSDT", "TRXUSDT", "UNIUSDT", "SUIUSDT", "ADAUSDT",
}
ALT_MIN_CONFLUENCE = 5  # alts need SIGNAL+ to fire (not SETUP)
COOLDOWN_CANDLES_4H = 6  # 1 day in 4h candles
CLUSTER_GAP_DAYS = 2  # merge same-direction signals within N days
CLUSTER_GAP_CANDLES_4H = 6  # cluster 4h signals within ~1 day
Z_WINDOW_4H = 2190  # 6/day × 365 days


def _zscore(values: list[float]) -> float:
    n = len(values)
    if n < MIN_POINTS:
        return 0.0
    mean = sum(values) / n
    std = (sum((x - mean) ** 2 for x in values) / n) ** 0.5
    if std < 1e-10:
        return 0.0
    return (values[-1] - mean) / std


def _sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return values[-1] if values else 0.0
    return sum(values[-period:]) / period


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _score_to_tier(score: int) -> str | None:
    # Cap at SIGNAL max — TRIGGER tier consistently loses in backtest
    if score >= CONFLUENCE_SIGNAL:
        return "SIGNAL"
    if score >= CONFLUENCE_SETUP:
        return "SETUP"
    return None


def _compute_confluence(
    oi_z: float, fund_z: float, liq_z: float, vol_z: float,
    price_momentum: float, z_accel: float,
    liq_long_z: float, liq_short_z: float,
    rv_regime: str | None,
    direction: str | None = None,
    funding_rate: float = 0.0,
    trend: str = "neutral",
) -> tuple[int, list[str]]:
    """Confluence scoring with momentum, z-acceleration, liq directional, RV regime, trend."""
    score = 0
    factors = []

    # 1-2. OI extreme
    if abs(oi_z) > Z_STRONG:
        score += 2
        factors.append(f"OI_z extreme ({oi_z:+.1f})")
    elif abs(oi_z) > Z_MODERATE:
        score += 1
        factors.append(f"OI_z elevated ({oi_z:+.1f})")

    # 3-4. Funding extreme
    if abs(fund_z) > Z_STRONG:
        score += 2
        factors.append(f"Fund_z extreme ({fund_z:+.1f})")
    elif abs(fund_z) > Z_MODERATE:
        score += 1
        factors.append(f"Fund_z elevated ({fund_z:+.1f})")

    # 5. Liq extreme
    if abs(liq_z) > Z_MODERATE:
        score += 1
        factors.append(f"Liq_z ({liq_z:+.1f})")

    # 6. Volume extreme
    if abs(vol_z) > Z_MODERATE:
        score += 1
        factors.append(f"Vol_z ({vol_z:+.1f})")

    # 7. Price momentum (5d return extreme: >5% or <-5%)
    if abs(price_momentum) > 5:
        score += 1
        factors.append(f"Price 5d {price_momentum:+.1f}%")

    # 8. Z-score acceleration (biggest z moved >1 in 3 days)
    if abs(z_accel) > 1.0:
        score += 1
        factors.append(f"Z-accel {z_accel:+.1f}")

    # 9. Liq directional (one side getting liquidated hard)
    if liq_long_z > Z_MODERATE or liq_short_z > Z_MODERATE:
        score += 1
        side = "longs" if liq_long_z > liq_short_z else "shorts"
        factors.append(f"Liq {side} spike")

    # 10. RV regime (extreme vol = regime shift)
    if rv_regime in ("low", "high"):
        score += 1
        factors.append(f"RV {rv_regime}")

    # Bonus: funding direction confirmation
    if direction == "short" and funding_rate > 0:
        score += 1
        factors.append("Fund confirms short")
    elif direction == "long" and funding_rate < 0:
        score += 1
        factors.append("Fund confirms long")

    # Bonus: trend alignment (+1 with trend, -1 counter-trend)
    if direction and trend != "neutral":
        if (direction == "long" and trend == "up") or (direction == "short" and trend == "down"):
            score += 1
            factors.append(f"Trend aligned ({trend})")
        else:
            score -= 1
            factors.append(f"Counter-trend ({trend})")

    # Crash penalty: if price dropping hard AND multiple metrics extreme in same direction,
    # reduce confluence (prevents TRIGGER longs into falling knife)
    if direction == "long" and price_momentum < -5:
        extreme_count = sum([
            abs(oi_z) > 2.0,
            abs(liq_z) > 2.0,
            abs(vol_z) > 2.0,
        ])
        if extreme_count >= 2:
            score -= 2
            factors.append("Crash penalty (multi-extreme)")

    if direction == "short" and price_momentum > 5:
        extreme_count = sum([
            abs(oi_z) > 2.0,
            abs(liq_z) > 2.0,
            abs(vol_z) > 2.0,
        ])
        if extreme_count >= 2:
            score -= 2
            factors.append("Crash penalty (multi-extreme)")

    return score, factors


def _cluster_alerts(alerts: list[dict], max_gap_days: int = CLUSTER_GAP_DAYS) -> list[dict]:
    """Merge nearby same-direction signals, keeping highest confluence."""
    if len(alerts) <= 1:
        return alerts

    alerts.sort(key=lambda a: a["fired_at"])
    clustered: list[dict] = []

    for alert in alerts:
        merged = False
        for existing in clustered:
            if existing["direction"] != alert["direction"]:
                continue
            d1 = datetime.fromisoformat(existing["fired_at"])
            d2 = datetime.fromisoformat(alert["fired_at"])
            if abs((d2 - d1).days) <= max_gap_days:
                # Keep the one with higher confluence
                if alert["confluence"] > existing["confluence"]:
                    idx = clustered.index(existing)
                    clustered[idx] = alert
                merged = True
                break
        if not merged:
            clustered.append(alert)

    return clustered


async def simulate_alerts(symbol: str, days: int = 180) -> list[dict]:
    """Simulate historical alerts for a symbol."""
    db = get_db()

    # Main derivatives data — including liq_long + liq_short
    rows = await db.execute_fetchall(
        """SELECT date, close_price, open_interest_usd, oi_binance_usd, funding_rate,
                  liquidations_long, liquidations_short, liquidations_delta, volume_usd
           FROM daily_derivatives
           WHERE symbol = ?
           ORDER BY date ASC""",
        (symbol,),
    )
    if not rows or len(rows) < MIN_POINTS + 10:
        return []

    dates = [r["date"] for r in rows]
    prices = [r["close_price"] or 0 for r in rows]
    ois = [(r["oi_binance_usd"] or 0) or (r["open_interest_usd"] or 0) for r in rows]
    fundings = [r["funding_rate"] or 0 for r in rows]
    liq_deltas = [r["liquidations_delta"] or 0 for r in rows]
    liq_longs = [r["liquidations_long"] or 0 for r in rows]
    liq_shorts = [r["liquidations_short"] or 0 for r in rows]
    volumes = [r["volume_usd"] or 0 for r in rows]

    # Preload RV data (daily_rv) into dict by date
    rv_rows = await db.execute_fetchall(
        "SELECT date, rv_30d FROM daily_rv WHERE symbol = ? ORDER BY date ASC",
        (symbol,),
    )
    rv_by_date: dict[str, float] = {r["date"]: r["rv_30d"] for r in rv_rows if r["rv_30d"]}
    rv_all_values = [v for v in rv_by_date.values() if v > 0]
    rv_median_val = _median(rv_all_values) if rv_all_values else 0.0

    # Preload 4h candles for MFE computation
    ohlcv_rows = await db.execute_fetchall(
        "SELECT ts, high, low FROM ohlcv_4h WHERE symbol = ? ORDER BY ts ASC",
        (symbol,),
    )
    # Convert to list of (timestamp_sec, high, low)
    candle_data = [(r["ts"] // 1000, r["high"], r["low"]) for r in ohlcv_rows]

    total = len(dates)
    warmup_end = max(MIN_POINTS, total - days)

    alerts: list[dict] = []
    cooldowns: dict[str, str] = {}  # "type:symbol" → last fired date

    # Pre-compute z-scores for acceleration lookback
    oi_zscores: list[float] = []
    for i in range(total):
        start = max(0, i - Z_WINDOW + 1)
        oi_zscores.append(_zscore(ois[start:i + 1]))

    for i in range(warmup_end, total):
        date = dates[i]
        price = prices[i]
        if price <= 0 or i == 0:
            continue

        # Trend filter: price vs SMA20
        sma20 = _sma(prices[:i + 1], SMA_PERIOD)
        price_vs_sma = ((price - sma20) / sma20 * 100) if sma20 > 0 else 0
        if price_vs_sma > 2:
            trend = "up"
        elif price_vs_sma < -2:
            trend = "down"
        else:
            trend = "neutral"

        # Rolling z-scores
        start = max(0, i - Z_WINDOW + 1)
        oi_z = oi_zscores[i]
        fund_z = _zscore(fundings[start:i + 1])
        liq_z = _zscore(liq_deltas[start:i + 1])
        vol_z = _zscore(volumes[start:i + 1])
        liq_long_z = _zscore(liq_longs[start:i + 1])
        liq_short_z = _zscore(liq_shorts[start:i + 1])

        # 1d changes
        prev_price = prices[i - 1]
        prev_oi = ois[i - 1]
        price_chg = ((price - prev_price) / prev_price * 100) if prev_price > 0 else 0
        oi_chg = ((ois[i] - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0

        # 5d price momentum
        price_5d_ago = prices[i - 5] if i >= 5 else prices[0]
        price_momentum = ((price - price_5d_ago) / price_5d_ago * 100) if price_5d_ago > 0 else 0

        # Z-score acceleration (max z-score change over 3 days)
        z_accel = 0.0
        if i >= 3:
            z_accel = oi_z - oi_zscores[i - 3]

        # RV regime
        rv_today = rv_by_date.get(date)
        rv_prev_date = dates[i - 1] if i >= 1 else None
        rv_prev = rv_by_date.get(rv_prev_date) if rv_prev_date else None
        rv_regime: str | None = None
        if rv_today and rv_median_val > 0:
            if rv_today < rv_median_val * 0.6:
                rv_regime = "low"
            elif rv_today > rv_median_val * 1.5:
                rv_regime = "high"

        # Multi-day changes (3d, 5d) for broader divergence detection
        price_3d_ago = prices[i - 3] if i >= 3 else prices[0]
        oi_3d_ago = ois[i - 3] if i >= 3 else ois[0]
        price_chg_3d = ((price - price_3d_ago) / price_3d_ago * 100) if price_3d_ago > 0 else 0
        oi_chg_3d = ((ois[i] - oi_3d_ago) / oi_3d_ago * 100) if oi_3d_ago > 0 else 0

        price_5d_ago2 = prices[i - 5] if i >= 5 else prices[0]
        oi_5d_ago = ois[i - 5] if i >= 5 else ois[0]
        price_chg_5d = ((price - price_5d_ago2) / price_5d_ago2 * 100) if price_5d_ago2 > 0 else 0
        oi_chg_5d = ((ois[i] - oi_5d_ago) / oi_5d_ago * 100) if oi_5d_ago > 0 else 0

        # Volume trend (3d declining = distribution)
        vol_declining_3d = False
        if i >= 3:
            vol_declining_3d = volumes[i] < volumes[i-1] < volumes[i-2]

        # Check conditions — EVENT-based signals only (proven profitable)
        triggered: list[tuple[str, str]] = []

        # === SHORT signals (TOP detection) ===

        # 1. OVERHEAT — OI + funding both elevated (RELAXED: fund > 0.8)
        if oi_z > 1.5 and fund_z > 0.8:
            triggered.append(("overheat", "short"))

        # 2. FUNDING SPIKE — funding extreme without OI support → crowded longs
        if fund_z > 1.5 and price_momentum > 3:
            triggered.append(("fund_spike", "short"))

        # 3. DIVERGENCE SQUEEZE 1D (OI↑ Price↓) — tightened
        if oi_chg > 5 and price_chg < -2 and fund_z > 0 and trend != "down":
            triggered.append(("div_squeeze_1d", "short"))

        # 4. DIVERGENCE SQUEEZE 3D (slower buildup) — only if funding not already extreme short
        if oi_chg_3d > 5 and price_chg_3d < -2 and fund_z > -1.0:
            triggered.append(("div_squeeze_3d", "short"))

        # 5. DIVERGENCE SQUEEZE 5D (even slower)
        if oi_chg_5d > 8 and price_chg_5d < -3:
            triggered.append(("div_squeeze_5d", "short"))

        # 6. DIVERGENCE TOP 1D (OI↓ Price↑ — smart money exiting)
        if oi_chg < -3 and price_chg > 2:
            triggered.append(("div_top_1d", "short"))

        # 7. DIVERGENCE TOP 3D (relaxed: -5→-4, 3→2.5)
        if oi_chg_3d < -4 and price_chg_3d > 2.5:
            triggered.append(("div_top_3d", "short"))

        # 8. DISTRIBUTION — price up but volume declining (relaxed from 2 to 1.5)
        if price_chg_3d > 1.5 and vol_declining_3d and trend == "up":
            triggered.append(("distribution", "short"))

        # 9. OVEREXTENSION — price stretched far above SMA
        if price_vs_sma > 8 and fund_z > 0.5:
            triggered.append(("overextension", "short"))

        # 10. OI BUILDUP STALL — OI rising but price stalls → trap setup (relaxed)
        if oi_chg_3d > 4 and abs(price_chg_3d) < 2 and fund_z > 0.3:
            triggered.append(("oi_buildup_stall", "short"))

        # === LONG signals (BOTTOM detection) ===

        # 11. CAPITULATION — both washed out (not in downtrend — avoid falling knife)
        if oi_z < -1.5 and fund_z < -0.8 and trend != "down":
            triggered.append(("capitulation", "long"))

        # 12. LIQ FLUSH — cascade liquidation + OI dump (uptrend + deep momentum washout)
        if liq_z > 2.0 and price_chg < -5 and oi_chg < -3 and trend == "up" and price_momentum < -5:
            triggered.append(("liq_flush", "long"))

        # 13. LIQ FLUSH 3D — slower flush (tightened: uptrend + funding washed + negative momentum)
        if liq_z > 2.0 and price_chg_3d < -8 and oi_chg_3d < -5 and trend == "up" and fund_z < 0 and price_momentum < -5:
            triggered.append(("liq_flush_3d", "long"))

        # 14. VOLUME DIVERGENCE — volume spike but OI drops (closing, not opening)
        # Only in down/neutral — contrarian long fails in uptrends
        if vol_z > Z_MODERATE and oi_chg < -3 and abs(price_chg) > 2 and trend != "up":
            direction = "long" if price_chg < 0 else "short"
            triggered.append(("vol_divergence", direction))

        # 15. LIQ LONG FLUSH — DISABLED (unpredictable: WR 4.5% as long, 21% as short)
        # Neither direction works — cascading liquidations are noise
        # if liq_long_z > 2.5 and price_chg < -4 and oi_chg < -3 and price_momentum > 0:
        #     triggered.append(("liq_long_flush", "short"))

        # 16. LIQ SHORT SQUEEZE — shorts liquidated massively → momentum long
        # Caps: price_chg < 8 and oi_chg < 20 filter out late entries where squeeze already played out
        if liq_short_z > 3.0 and price_chg > 3 and price_chg < 8 and oi_chg < 20 and fund_z < 1.5 and trend != "down":
            triggered.append(("liq_short_squeeze", "long"))

        # 17. FUNDING REVERSAL — funding reverses from extreme
        if i >= 3:
            fund_3d_ago = fundings[i - 3]
            fund_delta = fundings[i] - fund_3d_ago
            if fund_z > 1.5 and fund_delta < -0.0005:
                triggered.append(("fund_reversal", "short"))
            if fund_z < -1.5 and fund_delta > 0.0005:
                triggered.append(("fund_reversal", "long"))

        # 18. VOL COMPRESSION → EXPANSION — DISABLED (WR 16.7%, unreliable)
        # if rv_today and rv_prev and rv_prev > 0:
        #     rv_ratio = rv_today / rv_prev
        #     if rv_ratio > 1.5 and rv_prev < rv_median_val:
        #         direction = "long" if price_chg > 0 else "short"
        #         triggered.append(("vol_expansion", direction))

        # 19. OI FLUSH + VOLUME SPIKE — DISABLED (unpredictable: WR 14% long, 19% short)
        # if oi_chg < -5 and vol_z > 1.5 and price_chg < -3:
        #     triggered.append(("oi_flush_vol", "short"))

        for alert_type, direction in triggered:
            # Compute confluence with direction-aware bonuses + trend
            confluence, factors = _compute_confluence(
                oi_z, fund_z, liq_z, vol_z, price_momentum, z_accel,
                liq_long_z, liq_short_z, rv_regime,
                direction=direction, funding_rate=fundings[i],
                trend=trend,
            )

            tier = _score_to_tier(confluence)
            if not tier:
                continue

            # OI tier filter: require higher confluence for altcoins
            if symbol not in TOP_OI_SYMBOLS and confluence < ALT_MIN_CONFLUENCE:
                continue

            # Per-symbol cooldown
            cd_key = f"{alert_type}:{symbol}"
            if cd_key in cooldowns:
                last = datetime.fromisoformat(cooldowns[cd_key])
                current = datetime.fromisoformat(date)
                if (current - last).days < COOLDOWN_DAYS:
                    continue

            cooldowns[cd_key] = date

            # Forward returns
            price_1d = prices[i + 1] if i + 1 < total else None
            price_3d = prices[i + 3] if i + 3 < total else None
            price_7d = prices[i + 7] if i + 7 < total else None

            return_1d = ((price_1d - price) / price * 100) if price_1d else None
            return_3d = ((price_3d - price) / price * 100) if price_3d else None
            return_7d = ((price_7d - price) / price * 100) if price_7d else None

            # MFE (Max Favorable Excursion) from 4h candles over 7 days
            mfe_return, mfe_price = _compute_mfe(
                candle_data, date, direction, price,
            )

            # MAE (Max Adverse Excursion) — worst drawdown against position
            mae_return = _compute_mae(
                candle_data, date, direction, price,
            )

            # Fallback: daily-resolution MFE/MAE when 4h data unavailable
            if mfe_return is None or mae_return is None:
                daily_mfe = 0.0
                daily_mae = 0.0
                for j in range(1, min(8, total - i)):
                    chg = (prices[i + j] - price) / price * 100
                    if direction == "long":
                        daily_mfe = max(daily_mfe, chg)
                        daily_mae = max(daily_mae, -chg)
                    else:
                        daily_mfe = max(daily_mfe, -chg)
                        daily_mae = max(daily_mae, chg)
                if mfe_return is None and daily_mfe > 0:
                    mfe_return = daily_mfe
                    mfe_price = None
                if mae_return is None and daily_mae > 0:
                    mae_return = daily_mae

            dt = datetime.fromisoformat(date)
            ts = int(dt.timestamp())
            snapped = (ts // 14400) * 14400

            alerts.append({
                "time": snapped,
                "type": alert_type,
                "tier": tier,
                "confluence": confluence,
                "fired_at": date,
                "entry_price": price,
                "direction": direction,
                "price_1d": price_1d,
                "price_3d": price_3d,
                "price_7d": price_7d,
                "return_1d": round(return_1d, 2) if return_1d is not None else None,
                "return_3d": round(return_3d, 2) if return_3d is not None else None,
                "return_7d": round(return_7d, 2) if return_7d is not None else None,
                "mfe_return": round(mfe_return, 2) if mfe_return is not None else None,
                "mfe_price": round(mfe_price, 2) if mfe_price is not None else None,
                "mae_return": round(mae_return, 2) if mae_return is not None else None,
                "simulated": True,
                "factors": factors[:5],
                "zscores": {
                    "oi": round(oi_z, 2),
                    "funding": round(fund_z, 2),
                    "liq": round(liq_z, 2),
                    "volume": round(vol_z, 2),
                },
                "features": {
                    "price_chg": round(price_chg, 2),
                    "oi_chg": round(oi_chg, 2),
                    "price_chg_3d": round(price_chg_3d, 2),
                    "oi_chg_3d": round(oi_chg_3d, 2),
                    "price_chg_5d": round(price_chg_5d, 2),
                    "oi_chg_5d": round(oi_chg_5d, 2),
                    "price_momentum": round(price_momentum, 2),
                    "price_vs_sma": round(price_vs_sma, 2),
                    "trend": trend,
                    "liq_long_z": round(liq_long_z, 2),
                    "liq_short_z": round(liq_short_z, 2),
                    "fund_rate": round(fundings[i], 6),
                },
            })

    # Cluster nearby same-direction signals
    alerts = _cluster_alerts(alerts)

    # Daily cap: keep only top-N confluence signals per day
    MAX_SIGNALS_PER_DAY = 3
    from collections import defaultdict
    by_date: dict[str, list[dict]] = defaultdict(list)
    for a in alerts:
        day = a["fired_at"][:10]  # YYYY-MM-DD
        by_date[day].append(a)
    capped_alerts: list[dict] = []
    for day, day_alerts in by_date.items():
        day_alerts.sort(key=lambda x: -x["confluence"])
        capped_alerts.extend(day_alerts[:MAX_SIGNALS_PER_DAY])
    alerts = capped_alerts

    return alerts


def _compute_mae(
    candle_data: list[tuple[int, float, float]],
    date: str, direction: str, entry_price: float,
    days: int = 7,
) -> float | None:
    """Compute MAE (Max Adverse Excursion) — worst move against position in 7d."""
    if not candle_data:
        return None

    dt = datetime.fromisoformat(date)
    start_ts = int(dt.timestamp())
    end_ts = start_ts + days * 86400

    worst = 0.0
    for ts, high, low in candle_data:
        if start_ts <= ts <= end_ts:
            if direction == "long":
                # Worst adverse = lowest low vs entry
                dd = (entry_price - low) / entry_price * 100
            else:
                # Worst adverse = highest high vs entry
                dd = (high - entry_price) / entry_price * 100
            worst = max(worst, dd)

    return worst if worst > 0 else None


def _compute_mfe(
    candle_data: list[tuple[int, float, float]],
    date: str, direction: str, entry_price: float,
    days: int = 7,
) -> tuple[float | None, float | None]:
    """Compute MFE (Max Favorable Excursion) from 4h candles."""
    if not candle_data:
        return None, None

    dt = datetime.fromisoformat(date)
    start_ts = int(dt.timestamp())
    end_ts = start_ts + days * 86400

    # Collect highs/lows in the 7-day window
    highs: list[float] = []
    lows: list[float] = []
    for ts, high, low in candle_data:
        if start_ts <= ts <= end_ts:
            highs.append(high)
            lows.append(low)

    if not highs:
        return None, None

    if direction == "long":
        best = max(highs)
        mfe_ret = (best - entry_price) / entry_price * 100
        return mfe_ret, best
    else:
        best = min(lows)
        mfe_ret = (entry_price - best) / entry_price * 100
        return mfe_ret, best


def _compute_mfe_4h(
    candle_data: list[tuple[int, float, float]],
    ts_sec: int, direction: str, entry_price: float,
    candles: int = 42,
) -> tuple[float | None, float | None]:
    """MFE for 4h alerts — window in seconds from ts."""
    if not candle_data:
        return None, None
    end_ts = ts_sec + candles * 14400
    highs, lows = [], []
    for ts, high, low in candle_data:
        if ts_sec <= ts <= end_ts:
            highs.append(high)
            lows.append(low)
    if not highs:
        return None, None
    if direction == "long":
        best = max(highs)
        return (best - entry_price) / entry_price * 100, best
    best = min(lows)
    return (entry_price - best) / entry_price * 100, best


def _compute_mae_4h(
    candle_data: list[tuple[int, float, float]],
    ts_sec: int, direction: str, entry_price: float,
    candles: int = 42,
) -> float | None:
    """MAE for 4h alerts — worst move against position in N candles."""
    if not candle_data:
        return None
    end_ts = ts_sec + candles * 14400
    worst = 0.0
    for ts, high, low in candle_data:
        if ts_sec <= ts <= end_ts:
            if direction == "long":
                dd = (entry_price - low) / entry_price * 100
            else:
                dd = (high - entry_price) / entry_price * 100
            worst = max(worst, dd)
    return worst if worst > 0 else None


async def simulate_alerts_4h(symbol: str, days: int = 180) -> list[dict]:
    """Simulate historical alerts on 4h derivatives data."""
    db = get_db()

    rows = await db.execute_fetchall(
        """SELECT ts, close_price, open_interest_usd, oi_binance_usd, funding_rate,
                  liquidations_long, liquidations_short, liquidations_delta, volume_usd
           FROM derivatives_4h
           WHERE symbol = ?
           ORDER BY ts ASC""",
        (symbol,),
    )
    if not rows or len(rows) < MIN_POINTS_4H + 10:
        return []

    timestamps = [r["ts"] // 1000 for r in rows]  # seconds
    prices = [r["close_price"] or 0 for r in rows]
    ois = [(r["oi_binance_usd"] or 0) or (r["open_interest_usd"] or 0) for r in rows]
    fundings = [r["funding_rate"] or 0 for r in rows]
    liq_deltas = [r["liquidations_delta"] or 0 for r in rows]
    liq_longs = [r["liquidations_long"] or 0 for r in rows]
    liq_shorts = [r["liquidations_short"] or 0 for r in rows]
    volumes = [r["volume_usd"] or 0 for r in rows]

    # RV data for regime
    rv_rows = await db.execute_fetchall(
        "SELECT date, rv_30d FROM daily_rv WHERE symbol = ? ORDER BY date ASC",
        (symbol,),
    )
    rv_by_date: dict[str, float] = {r["date"]: r["rv_30d"] for r in rv_rows if r["rv_30d"]}
    rv_all = [v for v in rv_by_date.values() if v > 0]
    rv_median_val = _median(rv_all) if rv_all else 0.0

    # 4h OHLCV candles for MFE/MAE
    ohlcv_rows = await db.execute_fetchall(
        "SELECT ts, high, low FROM ohlcv_4h WHERE symbol = ? ORDER BY ts ASC",
        (symbol,),
    )
    candle_data = [(r["ts"] // 1000, r["high"], r["low"]) for r in ohlcv_rows]

    total = len(rows)
    # How many candles correspond to requested days
    candles_per_day = 6
    lookback_candles = days * candles_per_day
    warmup_end = max(MIN_POINTS_4H, total - lookback_candles)

    SMA_PERIOD_4H = 120  # ~20 days

    # Pre-compute OI z-scores for acceleration
    oi_zscores_4h: list[float] = []
    for i in range(total):
        start = max(0, i - Z_WINDOW_4H + 1)
        oi_zscores_4h.append(_zscore(ois[start:i + 1]))

    alerts: list[dict] = []
    cooldowns: dict[str, int] = {}  # "type:symbol" → last fired index

    for i in range(warmup_end, total):
        price = prices[i]
        if price <= 0 or i == 0:
            continue

        ts_sec = timestamps[i]

        # Trend filter
        sma = _sma(prices[:i + 1], SMA_PERIOD_4H)
        price_vs_sma = ((price - sma) / sma * 100) if sma > 0 else 0
        if price_vs_sma > 2:
            trend = "up"
        elif price_vs_sma < -2:
            trend = "down"
        else:
            trend = "neutral"

        # Rolling z-scores
        start = max(0, i - Z_WINDOW_4H + 1)
        oi_z = oi_zscores_4h[i]
        fund_z = _zscore(fundings[start:i + 1])
        liq_z = _zscore(liq_deltas[start:i + 1])
        vol_z = _zscore(volumes[start:i + 1])
        liq_long_z = _zscore(liq_longs[start:i + 1])
        liq_short_z = _zscore(liq_shorts[start:i + 1])

        # 1-candle changes (4h → thresholds ÷3 vs daily)
        prev_price = prices[i - 1]
        prev_oi = ois[i - 1]
        price_chg = ((price - prev_price) / prev_price * 100) if prev_price > 0 else 0
        oi_chg = ((ois[i] - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0

        # 6-candle (~1d) changes — ≈ daily 1d thresholds
        idx_6 = max(0, i - 6)
        price_chg_6 = ((price - prices[idx_6]) / prices[idx_6] * 100) if prices[idx_6] > 0 else 0
        oi_chg_6 = ((ois[i] - ois[idx_6]) / ois[idx_6] * 100) if ois[idx_6] > 0 else 0

        # 18-candle (~3d) changes
        idx_18 = max(0, i - 18)
        price_chg_18 = ((price - prices[idx_18]) / prices[idx_18] * 100) if prices[idx_18] > 0 else 0
        oi_chg_18 = ((ois[i] - ois[idx_18]) / ois[idx_18] * 100) if ois[idx_18] > 0 else 0

        # 30-candle (~5d) momentum
        idx_30 = max(0, i - 30)
        price_momentum = ((price - prices[idx_30]) / prices[idx_30] * 100) if prices[idx_30] > 0 else 0

        # Z-accel (18 candles ≈ 3d)
        z_accel = oi_z - oi_zscores_4h[max(0, i - 18)] if i >= 18 else 0.0

        # Volume declining (18 candles)
        vol_declining = False
        if i >= 18:
            vol_declining = volumes[i] < volumes[i - 6] < volumes[i - 12]

        # RV regime (date-based lookup)
        date_str = datetime.utcfromtimestamp(ts_sec).strftime("%Y-%m-%d")
        rv_today = rv_by_date.get(date_str)
        rv_regime: str | None = None
        if rv_today and rv_median_val > 0:
            if rv_today < rv_median_val * 0.6:
                rv_regime = "low"
            elif rv_today > rv_median_val * 1.5:
                rv_regime = "high"

        triggered: list[tuple[str, str]] = []

        # === SHORT signals (thresholds scaled for 4h) ===
        if oi_z > 1.5 and fund_z > 0.8:
            triggered.append(("overheat", "short"))

        if fund_z > 1.5 and price_momentum > 3:
            triggered.append(("fund_spike", "short"))

        # 1-candle div squeeze (tightened: oi>3%, price<-1.5%, fund>0, not in downtrend)
        if oi_chg > 3 and price_chg < -1.5 and fund_z > 0 and trend != "down":
            triggered.append(("div_squeeze_1d", "short"))

        # 6-candle (~1d) div squeeze ≈ daily 1d — only if funding not extreme short
        if oi_chg_6 > 3 and price_chg_6 < -1 and fund_z > -1.0:
            triggered.append(("div_squeeze_3d", "short"))

        # 18-candle (~3d) div squeeze ≈ daily 3d
        if oi_chg_18 > 5 and price_chg_18 < -2:
            triggered.append(("div_squeeze_5d", "short"))

        # Div top 6-candle
        if oi_chg_6 < -3 and price_chg_6 > 2:
            triggered.append(("div_top_1d", "short"))

        # Div top 18-candle (relaxed)
        if oi_chg_18 < -4 and price_chg_18 > 2.5:
            triggered.append(("div_top_3d", "short"))

        if price_chg_18 > 1.5 and vol_declining and trend == "up":
            triggered.append(("distribution", "short"))

        if price_vs_sma > 8 and fund_z > 0.5:
            triggered.append(("overextension", "short"))

        if oi_chg_18 > 4 and abs(price_chg_18) < 2 and fund_z > 0.3:
            triggered.append(("oi_buildup_stall", "short"))

        # === LONG signals ===
        if oi_z < -1.5 and fund_z < -0.8 and trend != "down":
            triggered.append(("capitulation", "long"))

        if liq_z > 2.0 and price_chg_6 < -5 and oi_chg_6 < -3 and trend == "up" and price_momentum < -5:
            triggered.append(("liq_flush", "long"))

        if liq_z > 2.0 and price_chg_18 < -8 and oi_chg_18 < -5 and trend == "up" and fund_z < 0 and price_momentum < -5:
            triggered.append(("liq_flush_3d", "long"))

        if vol_z > Z_MODERATE and oi_chg_6 < -3 and abs(price_chg_6) > 2 and trend != "up":
            direction = "long" if price_chg_6 < 0 else "short"
            triggered.append(("vol_divergence", direction))

        # liq_long_flush — DISABLED
        # if liq_long_z > 2.5 and price_chg_6 < -4 and oi_chg_6 < -3 and price_momentum > 0:
        #     triggered.append(("liq_long_flush", "short"))

        if liq_short_z > 3.0 and price_chg_6 > 4 and price_chg_6 < 10 and oi_chg_6 < 20 and fund_z < 1.5 and trend != "down":
            triggered.append(("liq_short_squeeze", "long"))

        if i >= 18:
            fund_18ago = fundings[i - 18]
            fund_delta = fundings[i] - fund_18ago
            if fund_z > 1.5 and fund_delta < -0.0005:
                triggered.append(("fund_reversal", "short"))
            if fund_z < -1.5 and fund_delta > 0.0005:
                triggered.append(("fund_reversal", "long"))

        # VOL COMPRESSION → EXPANSION — DISABLED (WR 16.7%, unreliable)
        # if rv_today and rv_median_val > 0:
        #     rv_prev_idx = max(0, i - 6)
        #     rv_prev_date = datetime.utcfromtimestamp(timestamps[rv_prev_idx]).strftime("%Y-%m-%d")
        #     rv_prev = rv_by_date.get(rv_prev_date)
        #     if rv_prev and rv_prev > 0:
        #         rv_ratio = rv_today / rv_prev
        #         if rv_ratio > 1.5 and rv_prev < rv_median_val:
        #             direction = "long" if price_chg_6 > 0 else "short"
        #             triggered.append(("vol_expansion", direction))

        # oi_flush_vol — DISABLED
        # if oi_chg_6 < -5 and vol_z > 1.5 and price_chg_6 < -3:
        #     triggered.append(("oi_flush_vol", "short"))

        for alert_type, direction in triggered:
            confluence, factors = _compute_confluence(
                oi_z, fund_z, liq_z, vol_z, price_momentum, z_accel,
                liq_long_z, liq_short_z, rv_regime,
                direction=direction, funding_rate=fundings[i], trend=trend,
            )

            tier = _score_to_tier(confluence)
            if not tier or confluence < CONFLUENCE_SIGNAL:
                continue  # 4h: min SIGNAL tier (no SETUP noise)

            # OI tier filter: require higher confluence for altcoins
            if symbol not in TOP_OI_SYMBOLS and confluence < ALT_MIN_CONFLUENCE:
                continue

            cd_key = f"{alert_type}:{symbol}"
            if cd_key in cooldowns:
                if (i - cooldowns[cd_key]) < COOLDOWN_CANDLES_4H:
                    continue
            cooldowns[cd_key] = i

            # Forward returns: +6 candles (1d), +18 (3d), +42 (7d)
            p_6 = prices[i + 6] if i + 6 < total else None
            p_18 = prices[i + 18] if i + 18 < total else None
            p_42 = prices[i + 42] if i + 42 < total else None

            r_6 = ((p_6 - price) / price * 100) if p_6 else None
            r_18 = ((p_18 - price) / price * 100) if p_18 else None
            r_42 = ((p_42 - price) / price * 100) if p_42 else None

            mfe_return, mfe_price = _compute_mfe_4h(candle_data, ts_sec, direction, price)
            mae_return = _compute_mae_4h(candle_data, ts_sec, direction, price)

            snapped = (ts_sec // 14400) * 14400

            alerts.append({
                "time": snapped,
                "type": alert_type,
                "tier": tier,
                "confluence": confluence,
                "fired_at": datetime.utcfromtimestamp(ts_sec).strftime("%Y-%m-%dT%H:%M:%S"),
                "entry_price": price,
                "direction": direction,
                "price_1d": p_6,
                "price_3d": p_18,
                "price_7d": p_42,
                "return_1d": round(r_6, 2) if r_6 is not None else None,
                "return_3d": round(r_18, 2) if r_18 is not None else None,
                "return_7d": round(r_42, 2) if r_42 is not None else None,
                "mfe_return": round(mfe_return, 2) if mfe_return is not None else None,
                "mfe_price": round(mfe_price, 2) if mfe_price is not None else None,
                "mae_return": round(mae_return, 2) if mae_return is not None else None,
                "simulated": True,
                "timeframe": "4h",
                "factors": factors[:5],
                "zscores": {
                    "oi": round(oi_z, 2),
                    "funding": round(fund_z, 2),
                    "liq": round(liq_z, 2),
                    "volume": round(vol_z, 2),
                },
            })

    # Cluster nearby same-direction 4h signals
    alerts = _cluster_alerts(alerts, max_gap_days=1)
    return alerts


def _apply_mtf_upgrade(alerts_4h: list[dict], alerts_1d: list[dict]) -> list[dict]:
    """4h signal before 1d signal in same direction within 24h → tier++"""
    for a1d in alerts_1d:
        for a4h in alerts_4h:
            if a4h.get("direction") != a1d.get("direction"):
                continue
            gap = a1d["time"] - a4h["time"]
            if 0 < gap <= 86400:
                a1d["tier_upgraded"] = True
                a1d["original_tier"] = a1d["tier"]
                if a1d["tier"] == "SETUP":
                    a1d["tier"] = "SIGNAL"
                elif a1d["tier"] == "SIGNAL":
                    a1d["tier"] = "TRIGGER"
                a1d["confluence"] += 1
                a1d["factors"] = ["4h confirmed"] + a1d.get("factors", [])
                break
    return alerts_1d
