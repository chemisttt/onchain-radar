"""Momentum indicator service.

Computes multi-component momentum score [-100, +100] for each symbol daily.
Components: cross-sectional decile, time-series decile, relative decile,
directional intensity, volatility regime.
"""

import asyncio
import logging
import math
from datetime import datetime, timezone

from db import get_db
from services.derivatives_service import SYMBOLS

log = logging.getLogger("momentum")

_task: asyncio.Task | None = None
POLL_INTERVAL = 3600  # 1 hour (compute daily, check hourly)
MIN_HISTORY = 60  # minimum days needed


def _decile(rank_pct: float) -> int:
    """Convert percentile [0, 1] to decile [1, 10]."""
    return max(1, min(10, int(rank_pct * 10) + 1))


def _directional_intensity(returns: list[float], window: int = 20) -> float:
    """Measure consistency of price direction [-1, +1]."""
    if len(returns) < window:
        return 0.0
    recent = returns[-window:]
    pos = sum(1 for r in recent if r > 0)
    neg = sum(1 for r in recent if r < 0)
    return round((pos - neg) / len(recent), 4)


def _vol_regime(returns: list[float], short_window: int = 10, long_window: int = 30) -> float:
    """Current short-term vol vs smoothed trend."""
    if len(returns) < long_window:
        return 0.0
    short_std = (sum(r ** 2 for r in returns[-short_window:]) / short_window) ** 0.5
    long_std = (sum(r ** 2 for r in returns[-long_window:]) / long_window) ** 0.5
    if long_std == 0:
        return 0.0
    return round(short_std - long_std, 6)


def _compute_momentum_value(
    cs_decile: int,
    ts_decile: int,
    rel_decile: int,
    di: float,
    vr: float,
) -> float:
    """Combine components into single momentum score [-100, +100]."""
    # Deciles contribute 60% (normalized from 1-10 to -1..+1)
    decile_avg = ((cs_decile - 5.5) / 4.5 + (ts_decile - 5.5) / 4.5 + (rel_decile - 5.5) / 4.5) / 3
    # DI contributes 30%
    # VR contributes 10% (sign indicates expanding/contracting)
    vr_signal = 1.0 if vr > 0 else (-1.0 if vr < 0 else 0.0)
    raw = decile_avg * 60 + di * 30 + vr_signal * 10
    return round(max(-100, min(100, raw)), 1)


async def _compute_all():
    """Compute momentum for all symbols."""
    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check if already computed today
    existing = await db.execute_fetchall(
        "SELECT 1 FROM daily_momentum WHERE date = ? LIMIT 1", (today,)
    )
    if existing:
        return

    # Load price history for all symbols (last 365 days is enough for momentum)
    all_prices: dict[str, list[tuple[str, float]]] = {}
    for sym in SYMBOLS:
        rows = await db.execute_fetchall(
            """SELECT date, close_price FROM daily_derivatives
               WHERE symbol = ? AND close_price > 0
                 AND date >= date('now', '-365 days')
               ORDER BY date ASC""",
            (sym,),
        )
        if len(rows) >= MIN_HISTORY:
            all_prices[sym] = [(r["date"], r["close_price"]) for r in rows]

    if len(all_prices) < 5:
        return

    # 1-month returns for cross-sectional ranking
    one_month_returns: dict[str, float] = {}
    one_month_return_histories: dict[str, list[float]] = {}
    daily_returns: dict[str, list[float]] = {}

    for sym, prices in all_prices.items():
        p = [x[1] for x in prices]
        # Daily returns
        dr = [(p[i] - p[i - 1]) / p[i - 1] for i in range(1, len(p)) if p[i - 1] > 0]
        daily_returns[sym] = dr

        # 1-month return (last 30 days)
        if len(p) >= 31:
            one_month_returns[sym] = (p[-1] - p[-31]) / p[-31] if p[-31] > 0 else 0

        # Historical 1-month returns for time-series ranking
        hist_rets = []
        for i in range(30, len(p)):
            if p[i - 30] > 0:
                hist_rets.append((p[i] - p[i - 30]) / p[i - 30])
        one_month_return_histories[sym] = hist_rets

    # Cross-sectional ranking: rank among peers
    if not one_month_returns:
        return
    sorted_returns = sorted(one_month_returns.items(), key=lambda x: x[1])
    n_syms = len(sorted_returns)
    cs_ranks: dict[str, int] = {}
    for rank, (sym, _) in enumerate(sorted_returns):
        cs_ranks[sym] = _decile(rank / n_syms)

    # BTC return for relative momentum
    btc_return = one_month_returns.get("BTCUSDT", 0)

    for sym in all_prices:
        if sym not in one_month_returns:
            continue

        dr = daily_returns.get(sym, [])
        if len(dr) < 20:
            continue

        # Cross-sectional decile
        cs = cs_ranks.get(sym, 5)

        # Time-series decile: rank current return vs own history
        hist = one_month_return_histories.get(sym, [])
        current_ret = one_month_returns[sym]
        if len(hist) >= 20:
            below = sum(1 for h in hist if h < current_ret)
            ts = _decile(below / len(hist))
        else:
            ts = 5

        # Relative decile: vs BTC
        rel_return = current_ret - btc_return
        # Simplified: use sign and magnitude
        if sym == "BTCUSDT":
            rel = 5  # BTC vs itself = neutral
        else:
            # Rank relative returns
            all_rel = {s: one_month_returns.get(s, 0) - btc_return for s in one_month_returns if s != "BTCUSDT"}
            if all_rel:
                sorted_rel = sorted(all_rel.items(), key=lambda x: x[1])
                for rank, (s, _) in enumerate(sorted_rel):
                    if s == sym:
                        rel = _decile(rank / len(sorted_rel))
                        break
                else:
                    rel = 5
            else:
                rel = 5

        # Directional Intensity
        di = _directional_intensity(dr)

        # Volatility Regime
        vr = _vol_regime(dr)

        # Relative Volume (30d avg vs current)
        prices_list = [x[1] for x in all_prices[sym]]
        # Use volume from daily_derivatives
        vol_rows = await db.execute_fetchall(
            """SELECT volume_usd FROM daily_derivatives
               WHERE symbol = ? AND volume_usd > 0
               ORDER BY date DESC LIMIT 31""",
            (sym,),
        )
        rel_vol = 1.0
        if len(vol_rows) >= 2:
            current_vol = vol_rows[0]["volume_usd"]
            avg_vol = sum(r["volume_usd"] for r in vol_rows[1:]) / len(vol_rows[1:])
            rel_vol = round(current_vol / avg_vol, 2) if avg_vol > 0 else 1.0

        # Proximity to 52-week high
        if len(prices_list) >= 252:
            high_52w = max(prices_list[-252:])
        else:
            high_52w = max(prices_list)
        current_price = prices_list[-1]
        prox_52w = round((high_52w - current_price) / high_52w * 100, 1) if high_52w > 0 else 0

        # Final momentum value
        momentum = _compute_momentum_value(cs, ts, rel, di, vr)

        await db.execute(
            """INSERT INTO daily_momentum
               (symbol, date, momentum_value, cs_decile, ts_decile, rel_decile,
                directional_intensity, vol_regime, relative_volume, proximity_52w_high)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET
                 momentum_value=excluded.momentum_value,
                 cs_decile=excluded.cs_decile,
                 ts_decile=excluded.ts_decile,
                 rel_decile=excluded.rel_decile,
                 directional_intensity=excluded.directional_intensity,
                 vol_regime=excluded.vol_regime,
                 relative_volume=excluded.relative_volume,
                 proximity_52w_high=excluded.proximity_52w_high""",
            (sym, today, momentum, cs, ts, rel, di, vr, rel_vol, prox_52w),
        )

    await db.commit()
    log.info("Momentum computed for all symbols")


# ── API ──────────────────────────────────────────────────────────────

async def get_momentum_page(symbol: str, days: int = 365) -> dict:
    """Return full momentum page data for a symbol."""
    db = get_db()
    sym = symbol.upper()
    cutoff = f"-{days}"

    # Momentum history
    rows = await db.execute_fetchall(
        """SELECT m.*, d.close_price as price
           FROM daily_momentum m
           JOIN daily_derivatives d ON m.symbol = d.symbol AND m.date = d.date
           WHERE m.symbol = ? AND m.date >= date('now', ? || ' days')
           ORDER BY m.date ASC""",
        (sym, cutoff),
    )

    history = [
        {
            "date": r["date"],
            "price": r["price"],
            "momentum": r["momentum_value"],
            "di": r["directional_intensity"],
            "vr": r["vol_regime"],
        }
        for r in rows
    ]

    # Latest metrics
    latest = rows[-1] if rows else None
    metrics = {}
    if latest:
        metrics = {
            "momentum_value": latest["momentum_value"],
            "cs_decile": latest["cs_decile"],
            "ts_decile": latest["ts_decile"],
            "rel_decile": latest["rel_decile"],
            "di": latest["directional_intensity"],
            "vol_regime": latest["vol_regime"],
            "relative_volume": latest["relative_volume"],
            "proximity_52w_high": latest["proximity_52w_high"],
        }

    # DI vs Forward Return scatter data
    di_scatter = _build_scatter(history, "di", [10, 30, 60])
    vr_scatter = _build_scatter(history, "vr", [10, 30, 60])

    # Regime classification
    mv = metrics.get("momentum_value", 0) or 0
    if mv > 10:
        regime = "Bullish"
    elif mv < -10:
        regime = "Bearish"
    else:
        regime = "Neutral"

    # Price distribution (BTC/ETH only — needs IV)
    price_dist = await _compute_price_distribution(sym, metrics)

    # Momentum z-score + historical avg + 30d change
    momentum_stats = _compute_momentum_stats(history)

    # Skew data (BTC/ETH only)
    skew_stats = await _get_skew_stats(sym)

    return {
        "symbol": sym,
        "regime": regime,
        "metrics": metrics,
        "history": history,
        "di_scatter": di_scatter,
        "vr_scatter": vr_scatter,
        "price_distribution": price_dist,
        "momentum_stats": momentum_stats,
        "skew_stats": skew_stats,
    }


async def _compute_price_distribution(sym: str, metrics: dict) -> dict:
    """Compute implied vs momentum-adjusted price distribution (BTC/ETH only)."""
    if sym not in ("BTCUSDT", "ETHUSDT"):
        return {}

    db = get_db()
    # Get latest IV and price
    vol_row = await db.execute_fetchall(
        """SELECT iv_30d, close_price FROM daily_volatility
           WHERE symbol = ? AND iv_30d IS NOT NULL
           ORDER BY date DESC LIMIT 1""",
        (sym,),
    )
    if not vol_row or not vol_row[0]["iv_30d"]:
        return {}

    iv = vol_row[0]["iv_30d"]
    price = vol_row[0]["close_price"] or 0
    if price <= 0:
        return {}

    momentum = metrics.get("momentum_value", 0) or 0
    di = metrics.get("di", 0) or 0
    vr = metrics.get("vol_regime", 0) or 0

    # Get avg daily return for drift calculation
    dr_rows = await db.execute_fetchall(
        """SELECT close_price FROM daily_derivatives
           WHERE symbol = ? AND close_price > 0
           ORDER BY date DESC LIMIT 31""",
        (sym,),
    )
    avg_daily_ret = 0.0
    if len(dr_rows) >= 2:
        rets = []
        prices = [r["close_price"] for r in reversed(dr_rows)]
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                rets.append((prices[i] - prices[i - 1]) / prices[i - 1])
        if rets:
            avg_daily_ret = sum(rets) / len(rets)

    result = {}
    for horizon in [7, 10, 14, 30, 60]:
        iv_frac = iv / 100
        sqrt_t = (horizon / 365) ** 0.5

        # Implied distribution
        impl_vol = iv_frac * sqrt_t
        impl_1s_low = round(price * (1 - impl_vol))
        impl_1s_high = round(price * (1 + impl_vol))
        impl_2s_low = round(price * (1 - 2 * impl_vol))
        impl_2s_high = round(price * (1 + 2 * impl_vol))

        # Momentum-adjusted distribution
        drift = momentum / 100 * avg_daily_ret * horizon
        adj_center = price * (1 + drift)
        vol_regime_factor = 0.1 if vr > 0 else (-0.05 if vr < 0 else 0)
        adj_vol = iv_frac * (1 + vol_regime_factor) * sqrt_t

        adj_1s_low = round(adj_center * (1 - adj_vol))
        adj_1s_high = round(adj_center * (1 + adj_vol))
        adj_2s_low = round(adj_center * (1 - 2 * adj_vol))
        adj_2s_high = round(adj_center * (1 + 2 * adj_vol))

        result[str(horizon)] = {
            "implied": {
                "vol_pct": round(impl_vol * 100, 1),
                "low_1s": impl_1s_low,
                "high_1s": impl_1s_high,
                "low_2s": impl_2s_low,
                "high_2s": impl_2s_high,
            },
            "adjusted": {
                "vol_pct": round(adj_vol * 100, 1),
                "low_1s": adj_1s_low,
                "high_1s": adj_1s_high,
                "low_2s": adj_2s_low,
                "high_2s": adj_2s_high,
                "center": round(adj_center),
            },
        }

    return result


def _compute_momentum_stats(history: list[dict]) -> dict:
    """Compute momentum z-score, historical avg, 30d change."""
    if not history:
        return {}

    values = [h["momentum"] for h in history if h.get("momentum") is not None]
    if len(values) < 7:
        return {}

    current = values[-1]
    mean = sum(values) / len(values)
    std = (sum((x - mean) ** 2 for x in values) / len(values)) ** 0.5
    z = round((current - mean) / std, 2) if std > 0 else 0.0

    change_30d = 0.0
    if len(values) >= 30:
        change_30d = round(current - values[-30], 1)

    return {
        "score": current,
        "zscore": z,
        "avg": round(mean, 1),
        "change_30d": change_30d,
    }


async def _get_skew_stats(sym: str) -> dict:
    """Get skew statistics for signal gauge (BTC/ETH only)."""
    if sym not in ("BTCUSDT", "ETHUSDT"):
        return {}

    db = get_db()
    rows = await db.execute_fetchall(
        """SELECT skew_25d, skew_25d_zscore FROM daily_volatility
           WHERE symbol = ? AND skew_25d IS NOT NULL
           ORDER BY date ASC""",
        (sym,),
    )
    if len(rows) < 7:
        return {}

    values = [r["skew_25d"] for r in rows]
    current = values[-1]
    mean = sum(values) / len(values)
    current_z = rows[-1]["skew_25d_zscore"] or 0

    change_30d = 0.0
    if len(values) >= 30:
        change_30d = round(current - values[-30], 2)

    # Skew score: normalized to 0-100 (50 = neutral)
    # Positive skew (puts > calls) = bearish = lower score
    # Negative skew (calls > puts) = bullish = higher score
    score = round(50 - current * 2, 1)
    score = max(0, min(100, score))

    return {
        "score": score,
        "skew": current,
        "zscore": round(current_z, 2),
        "avg": round(mean, 2),
        "change_30d": change_30d,
    }


def _build_scatter(
    history: list[dict],
    key: str,
    periods: list[int],
) -> dict:
    """Build scatter data: key vs forward return at multiple periods."""
    result = {}
    for period in periods:
        points = []
        for i in range(len(history) - period):
            val = history[i].get(key)
            price_now = history[i].get("price", 0)
            price_future = history[i + period].get("price", 0)
            if val is None or not price_now or not price_future:
                continue
            ret = (price_future - price_now) / price_now * 100
            points.append({"x": val, "y": round(ret, 2)})

        # Linear regression
        r2 = 0.0
        slope = 0.0
        intercept = 0.0
        if len(points) >= 10:
            n = len(points)
            sx = sum(p["x"] for p in points)
            sy = sum(p["y"] for p in points)
            sxy = sum(p["x"] * p["y"] for p in points)
            sx2 = sum(p["x"] ** 2 for p in points)
            denom = n * sx2 - sx * sx
            if denom != 0:
                slope = (n * sxy - sx * sy) / denom
                intercept = (sy - slope * sx) / n
                ss_tot = sum((p["y"] - sy / n) ** 2 for p in points)
                ss_res = sum((p["y"] - (slope * p["x"] + intercept)) ** 2 for p in points)
                r2 = round(1 - ss_res / ss_tot, 4) if ss_tot > 0 else 0.0

        # Current value prediction
        current_val = history[-1].get(key, 0) if history else 0
        avg_at_current = round(slope * current_val + intercept, 2)

        result[str(period)] = {
            "points": points,
            "r2": r2,
            "n": len(points),
            "avg_at_current": avg_at_current,
            "current": current_val,
        }

    return result


# ── Lifecycle ────────────────────────────────────────────────────────

async def _poll_loop():
    log.info("Momentum service started")
    while True:
        try:
            await _compute_all()
        except Exception as e:
            log.error(f"Momentum compute error: {e}")
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
