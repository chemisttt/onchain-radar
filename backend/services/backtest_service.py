"""Backtest service — replay alert conditions on historical data.

Walks daily_derivatives history, recomputes rolling z-scores at each day,
checks the same conditions as market_analyzer (+ relaxed variants), and returns
simulated alerts with actual forward returns computed from price data.
"""

import logging
from datetime import datetime

from db import get_db

log = logging.getLogger("backtest_service")

# Same thresholds as market_analyzer
Z_MODERATE = 2.0
Z_STRONG = 3.0
CONFLUENCE_SETUP = 3
CONFLUENCE_SIGNAL = 4
CONFLUENCE_TRIGGER = 6
Z_WINDOW = 365

MIN_POINTS = 30
COOLDOWN_DAYS = 1  # per symbol:type


def _zscore(values: list[float]) -> float:
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


def _compute_confluence(
    oi_z: float, fund_z: float, liq_z: float, vol_z: float,
    price_momentum: float, z_accel: float,
) -> tuple[int, list[str]]:
    """Confluence scoring with momentum and z-acceleration."""
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

    return score, factors


async def simulate_alerts(symbol: str, days: int = 180) -> list[dict]:
    """Simulate historical alerts for a symbol."""
    db = get_db()

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

    dates = [r["date"] for r in rows]
    prices = [r["close_price"] or 0 for r in rows]
    ois = [r["open_interest_usd"] or 0 for r in rows]
    fundings = [r["funding_rate"] or 0 for r in rows]
    liq_deltas = [r["liquidations_delta"] or 0 for r in rows]
    volumes = [r["volume_usd"] or 0 for r in rows]

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

        # Rolling z-scores
        start = max(0, i - Z_WINDOW + 1)
        oi_z = oi_zscores[i]
        fund_z = _zscore(fundings[start:i + 1])
        liq_z = _zscore(liq_deltas[start:i + 1])
        vol_z = _zscore(volumes[start:i + 1])

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

        confluence, factors = _compute_confluence(
            oi_z, fund_z, liq_z, vol_z, price_momentum, z_accel,
        )

        # Check conditions
        triggered: list[tuple[str, str]] = []

        # 1. OVERHEAT — OI + funding both elevated
        if oi_z > Z_MODERATE and fund_z > Z_MODERATE:
            triggered.append(("overheat", "short"))

        # 2. CAPITULATION — both washed out
        if oi_z < -Z_MODERATE and fund_z < -Z_MODERATE:
            triggered.append(("capitulation", "long"))

        # 3. OI EXTREME — standalone OI z-score (relaxed: no funding requirement)
        if abs(oi_z) > 2.5 and abs(fund_z) <= Z_MODERATE:
            direction = "short" if oi_z > 0 else "long"
            triggered.append(("oi_extreme", direction))

        # 4. DIVERGENCE SQUEEZE (OI↑ Price↓) — relaxed thresholds
        if oi_chg > 5 and price_chg < -1.5:
            triggered.append(("divergence_squeeze", "short"))

        # 5. DIVERGENCE TOP (OI↓ Price↑) — relaxed thresholds
        if oi_chg < -5 and price_chg > 3:
            triggered.append(("divergence_top", "short"))

        # 6. LIQ FLUSH — relaxed
        if liq_z > 1.5 and price_chg < -3 and oi_chg < -2:
            triggered.append(("liq_flush", "long"))

        # 7. FUNDING EXTREME — standalone
        if abs(fund_z) > 2.5:
            direction = "short" if fund_z > 0 else "long"
            triggered.append(("funding_extreme", direction))

        for alert_type, direction in triggered:
            tier = _score_to_tier(confluence)
            if not tier:
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
                "factors": factors[:4],
                "zscores": {
                    "oi": round(oi_z, 2),
                    "funding": round(fund_z, 2),
                    "liq": round(liq_z, 2),
                    "volume": round(vol_z, 2),
                },
            })

    return alerts
