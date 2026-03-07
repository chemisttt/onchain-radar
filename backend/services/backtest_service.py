"""Backtest service — replay alert conditions on historical data.

Walks daily_derivatives history, recomputes rolling z-scores at each day,
checks the same conditions as market_analyzer, and returns simulated alerts
with actual forward returns computed from price data.
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
Z_WINDOW = 365  # rolling z-score window in days

# Minimum data points for z-score calculation
MIN_POINTS = 30

# Cooldown between same alert type (days)
COOLDOWN_DAYS = 3


def _zscore(values: list[float]) -> float:
    """Compute z-score of the last value in the series."""
    n = len(values)
    if n < MIN_POINTS:
        return 0.0
    mean = sum(values) / n
    std = (sum((x - mean) ** 2 for x in values) / n) ** 0.5
    if std < 1e-10:
        return 0.0
    return (values[-1] - mean) / std


def _score_to_tier(score: int) -> str | None:
    if score >= CONFLUENCE_TRIGGER:
        return "TRIGGER"
    if score >= CONFLUENCE_SIGNAL:
        return "SIGNAL"
    if score >= CONFLUENCE_SETUP:
        return "SETUP"
    return None


def _compute_confluence_simple(oi_z: float, fund_z: float, liq_z: float, vol_z: float) -> int:
    """Simplified confluence scoring for backtest (no velocity/OB/momentum)."""
    score = 0
    if abs(oi_z) > Z_STRONG:
        score += 2
    elif abs(oi_z) > Z_MODERATE:
        score += 1
    if abs(fund_z) > Z_STRONG:
        score += 2
    elif abs(fund_z) > Z_MODERATE:
        score += 1
    if abs(liq_z) > Z_MODERATE:
        score += 1
    if abs(vol_z) > Z_MODERATE:
        score += 1
    return score


async def simulate_alerts(symbol: str, days: int = 180) -> list[dict]:
    """Simulate historical alerts for a symbol.

    Walks daily_derivatives from oldest to newest, at each day recomputes
    rolling z-scores and checks alert conditions.

    Returns list of simulated alerts with actual forward returns.
    """
    db = get_db()

    # Load all daily data for symbol (need Z_WINDOW extra days for warmup)
    rows = await db.execute_fetchall(
        """SELECT date, close_price, open_interest_usd, funding_rate,
                  liquidations_delta, volume_usd
           FROM daily_derivatives
           WHERE symbol = ?
           ORDER BY date ASC""",
        (symbol,),
    )
    if not rows or len(rows) < MIN_POINTS + 10:
        return []

    # Convert to lists for rolling computation
    dates = [r["date"] for r in rows]
    prices = [r["close_price"] or 0 for r in rows]
    ois = [r["open_interest_usd"] or 0 for r in rows]
    fundings = [r["funding_rate"] or 0 for r in rows]
    liq_deltas = [r["liquidations_delta"] or 0 for r in rows]
    volumes = [r["volume_usd"] or 0 for r in rows]

    # Determine simulation range
    total = len(dates)
    # Start simulating from warmup_end, only output last `days` days
    warmup_end = max(MIN_POINTS, total - days)

    alerts: list[dict] = []
    cooldowns: dict[str, str] = {}  # alert_type → last fired date

    for i in range(warmup_end, total):
        date = dates[i]
        price = prices[i]
        if price <= 0:
            continue

        # Rolling z-scores over window ending at i
        start = max(0, i - Z_WINDOW + 1)
        oi_z = _zscore(ois[start:i + 1])
        fund_z = _zscore(fundings[start:i + 1])
        liq_z = _zscore(liq_deltas[start:i + 1])
        vol_z = _zscore(volumes[start:i + 1])

        # 24h changes (vs previous day)
        if i == 0:
            continue
        prev_price = prices[i - 1]
        prev_oi = ois[i - 1]
        price_chg = ((price - prev_price) / prev_price * 100) if prev_price > 0 else 0
        oi_chg = ((ois[i] - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0

        confluence = _compute_confluence_simple(oi_z, fund_z, liq_z, vol_z)

        # Check conditions (same as market_analyzer)
        triggered: list[tuple[str, str]] = []  # (alert_type, direction)

        # 1. OVERHEAT
        if oi_z > Z_MODERATE and fund_z > Z_MODERATE:
            triggered.append(("overheat", "short"))

        # 2. CAPITULATION
        if oi_z < -Z_MODERATE and fund_z < -Z_MODERATE:
            triggered.append(("capitulation", "long"))

        # 3. DIVERGENCE SQUEEZE (OI↑ Price↓)
        if oi_chg > 8 and price_chg < -2:
            triggered.append(("divergence_squeeze", "short"))

        # 4. DIVERGENCE TOP (OI↓ Price↑)
        if oi_chg < -8 and price_chg > 4:
            triggered.append(("divergence_top", "short"))

        # 5. LIQ FLUSH
        if liq_z > Z_MODERATE and price_chg < -4 and oi_chg < -3:
            triggered.append(("liq_flush", "long"))

        for alert_type, direction in triggered:
            tier = _score_to_tier(confluence)
            if not tier:
                continue

            # Cooldown check
            cd_key = alert_type
            if cd_key in cooldowns:
                last = datetime.fromisoformat(cooldowns[cd_key])
                current = datetime.fromisoformat(date)
                if (current - last).days < COOLDOWN_DAYS:
                    continue

            cooldowns[cd_key] = date

            # Forward returns from actual price data
            price_1d = prices[i + 1] if i + 1 < total else None
            price_3d = prices[i + 3] if i + 3 < total else None
            price_7d = prices[i + 7] if i + 7 < total else None

            return_1d = ((price_1d - price) / price * 100) if price_1d else None
            return_3d = ((price_3d - price) / price * 100) if price_3d else None
            return_7d = ((price_7d - price) / price * 100) if price_7d else None

            # Snap date to nearest 4h candle timestamp (for chart alignment)
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
                "simulated": True,
                "zscores": {
                    "oi": round(oi_z, 2),
                    "funding": round(fund_z, 2),
                    "liq": round(liq_z, 2),
                    "volume": round(vol_z, 2),
                },
            })

    return alerts
