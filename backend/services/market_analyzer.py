"""Market analyzer — pure analytics, no Telegram dependency.

Two main functions:
- build_daily_digest() → list[str]  (HTML-formatted digest messages)
- check_alerts() → list[dict]       (triggered alert dicts with tier + confluence)
"""

import logging
import time as _time
from collections import deque
from datetime import datetime, timezone

from db import get_db
from services import derivatives_service, funding_service, price_service
from services.derivatives_service import SYMBOLS
from services.signal_conditions import (
    OI_Z_OVERHEAT, FUND_Z_OVERHEAT,
    OI_CHG_1D_SQUEEZE, PRICE_CHG_1D_SQUEEZE, FUND_Z_SQUEEZE_1D_MIN,
    OI_CHG_3D_SQUEEZE, PRICE_CHG_3D_SQUEEZE, FUND_Z_SQUEEZE_3D_MIN,
    OI_CHG_1D_TOP, PRICE_CHG_1D_TOP,
    PRICE_CHG_3D_DIST,
    PRICE_VS_SMA_OVEREXT_LO, PRICE_VS_SMA_OVEREXT_HI, FUND_Z_OVEREXT,
    OI_CHG_3D_STALL, PRICE_CHG_3D_STALL, FUND_Z_STALL,
    OI_Z_CAPITULATION, FUND_Z_CAPITULATION,
    LIQ_SHORT_Z_SQUEEZE, PRICE_CHG_SQUEEZE_LO, PRICE_CHG_SQUEEZE_HI,
    OI_CHG_SQUEEZE_CAP, FUND_Z_SQUEEZE_CAP,
    FUND_Z_REVERSAL, FUND_DELTA_REVERSAL,
)

log = logging.getLogger("market_analyzer")

# ── Alert tiers ──────────────────────────────────────────────────────

TIER_SETUP = "SETUP"
TIER_SIGNAL = "SIGNAL"
TIER_TRIGGER = "TRIGGER"

TIER_EMOJI = {
    TIER_SETUP: "🟡",
    TIER_SIGNAL: "🟠",
    TIER_TRIGGER: "🔴",
}

# ── Thresholds ───────────────────────────────────────────────────────

Z_MODERATE = 2.0
Z_STRONG = 3.0

CONFLUENCE_SETUP = 3
CONFLUENCE_SIGNAL = 4
CONFLUENCE_TRIGGER = 6

TOP_OI_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "DOGEUSDT", "TRXUSDT", "UNIUSDT", "SUIUSDT", "ADAUSDT",
}
ALT_MIN_CONFLUENCE = 5  # alts need SIGNAL+ to fire (not SETUP)
MAX_DIRECTIONAL_ALERTS_PER_CYCLE = 5

# Snapshot ring buffer: store every 5th call (=5min), keep 144 (=12h)
SNAPSHOT_INTERVAL = 5
SNAPSHOT_HISTORY_SIZE = 144
VELOCITY_LOOKBACK = 48  # 48 * 5min = 4h

VELOCITY_SIGNIFICANT = 0.5  # per hour

LIQ_PROXIMITY_PCT = 5.0
LIQ_MIN_WEIGHT = 0.20

OB_PRICE_DIVERGENCE_PCT = 2.0
OB_SKEW_DIVERGENCE_THRESHOLD = 0.15
OB_SKEW_Z_CONFIRMATION = 1.5

# Scalp alerts (vol_anomaly, ob_divergence) — disabled by default for swing focus
SCALP_ALERTS_ENABLED = False

# ── Regime labels ────────────────────────────────────────────────────

REGIME_LABELS = [
    (-2.0, "🟢 Deep Oversold", "Рынок вымыт. Ликвидации прошли, слабые руки вышли. Ищем лонг на зонах интереса."),
    (-1.0, "🔵 Oversold", "Перепроданность. Начало восстановления. Можно аккуратно набирать лонги."),
    (0.0, "🟡 Neutral Cool", "Нейтральная зона, уклон в cool. Нет чётких сигналов."),
    (1.0, "🟠 Neutral Hot", "Нейтральная зона, уклон в hot. Рынок разогревается."),
    (2.0, "🟠 Overbought", "Перекупленность. Сокращать экспозицию, не добавлять лонги."),
    (999, "🔴 Extreme", "Экстремальный перегрев. Каскадные ликвидации вероятны. Готовить шорт."),
]


def _regime_label(z: float) -> tuple[str, str]:
    for threshold, label, comment in REGIME_LABELS:
        if z <= threshold:
            return label, comment
    return REGIME_LABELS[-1][1], REGIME_LABELS[-1][2]


# ── Formatters ───────────────────────────────────────────────────────

def _fmt_usd(val: float) -> str:
    if abs(val) >= 1e9:
        return f"${val / 1e9:.1f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:.1f}M"
    if abs(val) >= 1e3:
        return f"${val / 1e3:.0f}K"
    return f"${val:.0f}"


def _fmt_price(val: float) -> str:
    """Format price with appropriate decimal places: $92,150 / $0.2634 / $1.42"""
    if val >= 1000:
        return f"${val:,.0f}"
    if val >= 1:
        return f"${val:,.2f}"
    if val >= 0.01:
        return f"${val:.4f}"
    return f"${val:.6f}"


def _fmt_pct(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _z_flag(z: float) -> str:
    if abs(z) >= 2:
        return " 🔴"
    if abs(z) >= 1.5:
        return " ⚠️"
    return ""


def _score_to_tier(score: int) -> str | None:
    """Convert confluence score to tier. Returns None if below minimum.
    Cap at SIGNAL max — TRIGGER tier consistently loses in backtest."""
    if score >= CONFLUENCE_SIGNAL:
        return TIER_SIGNAL
    if score >= CONFLUENCE_SETUP:
        return TIER_SETUP
    return None


# ── Snapshot ring buffer ─────────────────────────────────────────────

_snapshot_history: deque[dict[str, dict]] = deque(maxlen=SNAPSHOT_HISTORY_SIZE)
_check_counter: int = 0
_initialized: bool = False


def _store_snapshot(current: dict[str, dict]) -> None:
    """Store snapshot every SNAPSHOT_INTERVAL calls."""
    global _check_counter
    _check_counter += 1
    if _check_counter % SNAPSHOT_INTERVAL == 0:
        _snapshot_history.append(current)


def _get_prev_snapshot() -> dict[str, dict]:
    """Get most recent stored snapshot."""
    return _snapshot_history[-1] if _snapshot_history else {}


def _get_snapshot_n_ago(n: int) -> dict[str, dict] | None:
    """Get snapshot from ~n slots ago. None if not enough history."""
    if len(_snapshot_history) <= n:
        return None
    return _snapshot_history[-(n + 1)]


# ── Z-Score velocity ────────────────────────────────────────────────

def _compute_velocity(sym: str, metric: str, current_value: float) -> float | None:
    """Compute z-score change per hour over last 4 hours."""
    old = _get_snapshot_n_ago(VELOCITY_LOOKBACK)
    if old is None:
        return None
    old_data = old.get(sym)
    if old_data is None:
        return None
    old_value = old_data.get(metric)
    if old_value is None:
        return None
    hours = VELOCITY_LOOKBACK * SNAPSHOT_INTERVAL / 60  # = 4.0
    return round((current_value - old_value) / hours, 4)


def _compute_all_velocities(sym: str, cur: dict) -> dict[str, float | None]:
    return {
        "oi_z_vel": _compute_velocity(sym, "oi_z", cur["oi_z"]),
        "funding_z_vel": _compute_velocity(sym, "funding_z", cur["funding_z"]),
        "liq_z_vel": _compute_velocity(sym, "liq_z", cur["liq_z"]),
        "volume_z_vel": _compute_velocity(sym, "volume_z", cur["volume_z"]),
    }


def _velocity_context_lines(velocities: dict) -> list[str]:
    """Generate velocity lines for alert body."""
    names = {"oi_z_vel": "OI_z", "funding_z_vel": "Fund_z", "liq_z_vel": "Liq_z", "volume_z_vel": "Vol_z"}
    lines = []
    for key, vel in velocities.items():
        if vel is not None and abs(vel) > VELOCITY_SIGNIFICANT * 0.5:
            arrow = "↑" if vel > 0 else "↓"
            lines.append(f"Velocity {names.get(key, key)}: {arrow}{abs(vel):.2f}/h")
    return lines[:2]


def _ob_context_line(cur: dict) -> str:
    """Generate OB context line."""
    ob_skew = cur.get("ob_skew", 0)
    ob_skew_z = cur.get("ob_skew_zscore", 0)
    if abs(ob_skew_z) < 0.5:
        return ""
    direction = "bids heavy" if ob_skew > 0 else "asks heavy"
    return f"OB: {direction} (skew {ob_skew:+.2f}, z: {ob_skew_z:+.1f})"


# ── OB divergence detection ─────────────────────────────────────────

def _is_ob_divergence(price_chg: float, ob_skew: float, ob_skew_z: float) -> bool:
    """Check if orderbook skew diverges from price trend."""
    if abs(price_chg) < OB_PRICE_DIVERGENCE_PCT:
        return False
    if abs(ob_skew_z) < OB_SKEW_Z_CONFIRMATION:
        return False
    if price_chg > OB_PRICE_DIVERGENCE_PCT and ob_skew < -OB_SKEW_DIVERGENCE_THRESHOLD:
        return True
    if price_chg < -OB_PRICE_DIVERGENCE_PCT and ob_skew > OB_SKEW_DIVERGENCE_THRESHOLD:
        return True
    return False


# ── Liq cluster proximity ───────────────────────────────────────────

async def _check_liq_proximity(sym: str, current_price: float) -> list[dict]:
    """Return liquidation clusters near current price (from historical OI accumulation).

    Uses compute_liq_clusters() which walks 4h OI snapshots, projects where
    positions would be liquidated, and filters out already-hit levels.
    """
    from services.liquidation_service import compute_liq_clusters

    data = await compute_liq_clusters(sym)
    if not data["clusters"] or current_price <= 0:
        return []

    nearby = []
    for c in data["clusters"]:
        if abs(c["distance_pct"]) > LIQ_PROXIMITY_PCT:
            continue
        nearby.append({
            "level_price": c["level_price"],
            "distance_pct": abs(c["distance_pct"]),
            "direction": c["direction"],
            "volume_usd": c["volume_usd"],
            "long_vol": c["long_vol"],
            "short_vol": c["short_vol"],
        })

    nearby.sort(key=lambda x: x["volume_usd"], reverse=True)
    return nearby


# ── Confluence scoring ───────────────────────────────────────────────

def _compute_confluence(
    cur: dict,
    velocities: dict,
    liq_prox: dict | None,
    is_bullish: bool | None,
) -> tuple[int, list[str]]:
    """Compute confluence score (0-10) for a symbol. Returns (score, factors)."""
    score = 0
    factors = []

    oi_z = cur["oi_z"]
    fund_z = cur["funding_z"]
    liq_z = cur["liq_z"]
    vol_z = cur["volume_z"]
    ob_skew = cur.get("ob_skew", 0)
    ob_skew_z = cur.get("ob_skew_zscore", 0)
    momentum = cur.get("momentum_value", 0)
    price_chg = cur["price_change_24h_pct"]

    # 1. OI extreme
    if abs(oi_z) > Z_STRONG:
        score += 2
        factors.append(f"OI_z extreme ({oi_z:+.1f})")
    elif abs(oi_z) > Z_MODERATE:
        score += 1
        factors.append(f"OI_z elevated ({oi_z:+.1f})")

    # 2. Funding extreme
    if abs(fund_z) > Z_STRONG:
        score += 2
        factors.append(f"Fund_z extreme ({fund_z:+.1f})")
    elif abs(fund_z) > Z_MODERATE:
        score += 1
        factors.append(f"Fund_z elevated ({fund_z:+.1f})")

    # 3. Liq extreme
    if abs(liq_z) > Z_MODERATE:
        score += 1
        factors.append(f"Liq_z ({liq_z:+.1f})")

    # 4. Volume extreme
    if abs(vol_z) > Z_MODERATE:
        score += 1
        factors.append(f"Vol_z ({vol_z:+.1f})")

    # 5. OB skew divergence
    if _is_ob_divergence(price_chg, ob_skew, ob_skew_z):
        score += 1
        direction = "asks heavy" if ob_skew < 0 else "bids heavy"
        factors.append(f"OB divergence ({direction})")

    # 6. Momentum confirms direction
    if is_bullish is not None:
        if (is_bullish and momentum > 30) or (not is_bullish and momentum < -30):
            score += 1
            factors.append(f"Momentum confirms ({momentum:+.0f})")

    # 7. Liq proximity
    if liq_prox and liq_prox["distance_pct"] < LIQ_PROXIMITY_PCT:
        score += 1
        factors.append(f"Liq cluster ({liq_prox['distance_pct']:.1f}%)")

    # 8. Z-score velocity
    if velocities:
        max_vel = max((abs(v) for v in velocities.values() if v is not None), default=0)
        if max_vel > VELOCITY_SIGNIFICANT:
            score += 1
            fastest = max(
                ((k, v) for k, v in velocities.items() if v is not None),
                key=lambda x: abs(x[1]),
            )
            names = {"oi_z_vel": "OI", "funding_z_vel": "Fund", "liq_z_vel": "Liq", "volume_z_vel": "Vol"}
            factors.append(f"Velocity {names.get(fastest[0], fastest[0])}: {fastest[1]:+.2f}/h")

    return score, factors


# ── Alert format ─────────────────────────────────────────────────────

def _format_alert_body(
    what_we_see: list[str],
    indicators: list[str],
    action: list[str],
    tier: str = "",
    confluence: int = 0,
) -> str:
    """Format alert body with three sections + confidence footer."""
    lines = []
    lines.append("📊 <b>What we see:</b>")
    for item in what_we_see:
        if item:
            lines.append(f"• {item}")

    lines.append("\n🎯 <b>What indicators show:</b>")
    for item in indicators:
        lines.append(f"• {item}")

    lines.append("\n⚡ <b>How to act:</b>")
    for item in action:
        lines.append(f"• {item}")

    if tier and confluence:
        lines.append(f"\n<i>Confidence: {confluence}/10 | Tier: {tier}</i>")

    return "\n".join(lines)


# ── Alert builders ───────────────────────────────────────────────────

def _build_directional_alert(
    key: str, sym: str, short_sym: str, title_suffix: str,
    cur: dict, velocities: dict, confluence: int, tier: str, factors: list[str],
    indicators: list[str], action: list[str],
) -> dict:
    """Generic builder for directional alerts."""
    oi_z, fund_z, liq_z = cur["oi_z"], cur["funding_z"], cur["liq_z"]
    oi_chg = cur["oi_change_24h_pct"]
    price_chg = cur["price_change_24h_pct"]
    composite = (oi_z + fund_z + liq_z) / 3

    what_we_see = [
        f"OI_z: {oi_z:+.1f} | Fund_z: {fund_z:+.1f} | Liq_z: {liq_z:+.1f}",
        f"OI: {_fmt_usd(cur['open_interest_usd'])} ({_fmt_pct(oi_chg)} 24h)",
        f"Price: {_fmt_pct(price_chg)} 24h | Composite: {composite:+.1f}",
    ]
    what_we_see.extend(_velocity_context_lines(velocities))
    ob_line = _ob_context_line(cur)
    if ob_line:
        what_we_see.append(ob_line)

    indicators.append(f"Совпадение факторов: {confluence} ({', '.join(factors[:3])})")

    return {
        "key": f"{key}:{sym}",
        "symbol": sym,
        "price_change_pct": price_chg,
        "tier": tier,
        "confluence": confluence,
        "entry_price": cur["price"],
        "title": f"{TIER_EMOJI[tier]} {tier} | {short_sym} {title_suffix}",
        "body": _format_alert_body(what_we_see, indicators, action, tier, confluence),
    }


# ── Trade setup builder ───────────────────────────────────────────────

def _build_trade_setup(
    direction: str,
    price: float,
    structure: dict,
    liq_clusters: list[dict],
    min_rr: float = 3.0,
) -> dict | None:
    """Build concrete entry/stop/TP trade plan from price structure + liq clusters.

    direction: "up" (long) or "down" (short)
    Returns dict with entry, entry_zone, stop, tp1, tp2, rr or None.
    """
    atr = structure.get("atr_14", 0)
    levels = structure.get("key_levels", [])
    if not levels or atr <= 0:
        return None

    supports = [l for l in levels if l["type"] == "support"]
    resistances = [l for l in levels if l["type"] == "resistance"]

    if direction == "up":
        # Long: entry at nearest support below price
        if not supports:
            return None
        entry_level = supports[0]  # nearest (levels sorted by distance)
        entry = entry_level["price"]
        entry_zone = (round(entry - 0.3 * atr, 6), round(entry + 0.3 * atr, 6))

        # Stop: below entry by 1 ATR, or below nearest long liq cluster under entry — whichever is further
        stop_atr = entry - atr
        # Find long liq clusters below entry (these get liquidated on further drop)
        liq_below = [c for c in liq_clusters if c["direction"] == "long" and c["level_price"] < entry]
        stop_liq = min((c["level_price"] for c in liq_below), default=stop_atr) - 0.1 * atr
        stop = min(stop_atr, stop_liq)

        # TP1: nearest resistance above entry
        res_above = [l for l in resistances if l["price"] > entry]
        if not res_above:
            return None
        tp1 = res_above[0]["price"]

        # TP2: next resistance or short liq cluster above tp1
        tp2_candidates = [l["price"] for l in res_above[1:2]]
        liq_short_above = [c["level_price"] for c in liq_clusters
                           if c["direction"] == "short" and c["level_price"] > tp1]
        tp2_candidates.extend(liq_short_above[:1])
        tp2 = min(tp2_candidates) if tp2_candidates else None

        risk = entry - stop
        reward = tp1 - entry
        if risk <= 0:
            return None
        rr = reward / risk

    elif direction == "down":
        # Short: entry at nearest resistance above price
        if not resistances:
            return None
        entry_level = resistances[0]
        entry = entry_level["price"]
        entry_zone = (round(entry - 0.3 * atr, 6), round(entry + 0.3 * atr, 6))

        # Stop: above entry by 1 ATR, or above nearest short liq cluster over entry — whichever is further
        stop_atr = entry + atr
        liq_above = [c for c in liq_clusters if c["direction"] == "short" and c["level_price"] > entry]
        stop_liq = max((c["level_price"] for c in liq_above), default=stop_atr) + 0.1 * atr
        stop = max(stop_atr, stop_liq)

        # TP1: nearest support below entry
        sup_below = [l for l in supports if l["price"] < entry]
        if not sup_below:
            return None
        tp1 = sup_below[0]["price"]

        # TP2: next support or long liq cluster below tp1
        tp2_candidates = [l["price"] for l in sup_below[1:2]]
        liq_long_below = [c["level_price"] for c in liq_clusters
                          if c["direction"] == "long" and c["level_price"] < tp1]
        tp2_candidates.extend(liq_long_below[:1])
        tp2 = max(tp2_candidates) if tp2_candidates else None

        risk = stop - entry
        reward = entry - tp1
        if risk <= 0:
            return None
        rr = reward / risk
    else:
        return None

    if rr < min_rr:
        return None

    result = {
        "direction": direction,
        "entry": round(entry, 6),
        "entry_zone": (round(entry_zone[0], 6), round(entry_zone[1], 6)),
        "stop": round(stop, 6),
        "tp1": round(tp1, 6),
        "rr": round(rr, 1),
    }
    if tp2 is not None:
        result["tp2"] = round(tp2, 6)
    return result


def _format_trade_setup(setup: dict, liq_clusters: list[dict]) -> str:
    """Format trade setup as text block for alert body."""
    entry = setup["entry"]
    ez = setup["entry_zone"]
    stop = setup["stop"]
    tp1 = setup["tp1"]
    rr = setup["rr"]

    # Find what's at stop/tp levels for context
    stop_context = "ATR-based"
    for c in liq_clusters:
        if abs(c["level_price"] - stop) / max(abs(stop), 1) < 0.02:
            stop_context = f"{c['direction']} liq cluster ({_fmt_usd(c['volume_usd'])})"
            break

    tp1_context = "4h S/R"

    lines = [
        "\n📐 <b>TRADE PLAN:</b>",
        f"• Entry: {_fmt_price(ez[0])} – {_fmt_price(ez[1])} ({setup['direction']})",
        f"• Stop: {_fmt_price(stop)} ({stop_context})",
        f"• TP1: {_fmt_price(tp1)} ({tp1_context})",
    ]
    if "tp2" in setup:
        lines.append(f"• TP2: {_fmt_price(setup['tp2'])}")
    lines.append(f"• RR: 1:{rr}")
    return "\n".join(lines)


# ── Multi-day context for backtest-aligned signals ──────────────────

async def _get_multi_day_batch() -> dict[str, dict]:
    """Batch-load multi-day context for all symbols from daily_derivatives.

    Returns dict keyed by symbol with: 3d/5d price+OI changes,
    directional liq z-scores, SMA trend, volume patterns, funding delta.
    """
    db = get_db()
    rows = await db.execute_fetchall(
        """SELECT symbol, date, close_price, open_interest_usd, oi_binance_usd,
                  funding_rate, liquidations_long, liquidations_short, volume_usd
           FROM daily_derivatives
           ORDER BY symbol, date ASC""",
    )
    if not rows:
        return {}

    # Group by symbol
    by_sym: dict[str, list] = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)

    def _z(vals: list[float]) -> float:
        n = len(vals)
        if n < 30:
            return 0.0
        mean = sum(vals) / n
        std = (sum((x - mean) ** 2 for x in vals) / n) ** 0.5
        if std < 1e-10:
            return 0.0
        return (vals[-1] - mean) / std

    result: dict[str, dict] = {}
    for sym, sym_rows in by_sym.items():
        n = len(sym_rows)
        if n < 10:
            continue

        prices = [r["close_price"] or 0 for r in sym_rows]
        # Use Binance-only OI for multi-day changes (fallback to aggregate for old data)
        ois = [(r["oi_binance_usd"] or 0) or (r["open_interest_usd"] or 0) for r in sym_rows]
        fundings = [r["funding_rate"] or 0 for r in sym_rows]
        liq_longs = [r["liquidations_long"] or 0 for r in sym_rows]
        liq_shorts = [r["liquidations_short"] or 0 for r in sym_rows]
        volumes = [r["volume_usd"] or 0 for r in sym_rows]

        price = prices[-1]

        # Multi-day changes
        p3 = prices[-4] if n >= 4 else prices[0]
        p5 = prices[-6] if n >= 6 else prices[0]
        oi3 = ois[-4] if n >= 4 else ois[0]
        oi5 = ois[-6] if n >= 6 else ois[0]

        price_chg_3d = ((price - p3) / p3 * 100) if p3 > 0 else 0
        price_chg_5d = ((price - p5) / p5 * 100) if p5 > 0 else 0
        oi_chg_3d = ((ois[-1] - oi3) / oi3 * 100) if oi3 > 0 else 0
        oi_chg_5d = ((ois[-1] - oi5) / oi5 * 100) if oi5 > 0 else 0

        # Directional liq z-scores (full history)
        liq_long_z = _z(liq_longs)
        liq_short_z = _z(liq_shorts)

        # SMA20 trend
        sma_period = min(20, n)
        sma20 = sum(prices[-sma_period:]) / sma_period
        price_vs_sma = ((price - sma20) / sma20 * 100) if sma20 > 0 else 0
        if price_vs_sma > 2:
            trend = "up"
        elif price_vs_sma < -2:
            trend = "down"
        else:
            trend = "neutral"

        # Volume declining 3d
        vol_declining_3d = n >= 3 and volumes[-1] < volumes[-2] < volumes[-3]

        # Funding delta (3d)
        fund_3d_ago = fundings[-4] if n >= 4 else fundings[0]
        fund_delta = fundings[-1] - fund_3d_ago

        result[sym] = {
            "price_chg_3d": price_chg_3d,
            "price_chg_5d": price_chg_5d,
            "oi_chg_3d": oi_chg_3d,
            "oi_chg_5d": oi_chg_5d,
            "liq_long_z": liq_long_z,
            "liq_short_z": liq_short_z,
            "price_vs_sma": price_vs_sma,
            "trend": trend,
            "vol_declining_3d": vol_declining_3d,
            "fund_delta": fund_delta,
            "price_momentum": price_chg_5d,
        }

    # Load momentum data for new signals
    mom_rows = await db.execute_fetchall(
        """SELECT symbol, momentum_value, relative_volume
           FROM daily_momentum
           ORDER BY date DESC""",
    )
    # Keep only the latest row per symbol
    mom_seen: set[str] = set()
    for r in mom_rows:
        sym = r["symbol"]
        if sym in mom_seen:
            continue
        mom_seen.add(sym)
        if sym in result:
            result[sym]["momentum_value"] = r["momentum_value"] or 0.0
            result[sym]["relative_volume"] = r["relative_volume"] or 0.0

    return result


# ── check_alerts() — main entry ─────────────────────────────────────

async def check_alerts() -> list[dict]:
    """Check composite alert conditions with confluence scoring.

    Returns list of triggered alert dicts, each with:
      key, title, body, tier (SETUP/SIGNAL/TRIGGER), confluence (int)
    """
    global _initialized

    screener = await derivatives_service.get_screener(sort="oi_zscore", limit=30)
    if not screener:
        return []

    # Build current snapshot
    current: dict[str, dict] = {}
    for s in screener:
        current[s["symbol"]] = {
            "oi_z": s.get("oi_zscore", 0),
            "funding_z": s.get("funding_zscore", 0),
            "liq_z": s.get("liq_zscore", 0),
            "volume_z": s.get("volume_zscore", 0),
            "price": s.get("price", 0),
            "price_change_24h_pct": s.get("price_change_24h_pct", 0),
            "oi_change_24h_pct": s.get("oi_change_24h_pct", 0),
            "funding_rate": s.get("funding_rate", 0),
            "open_interest_usd": s.get("open_interest_usd", 0),
            "percentile_avg": s.get("percentile_avg", 50),
            "ob_skew": s.get("ob_skew", 0),
            "ob_skew_zscore": s.get("ob_skew_zscore", 0),
            "momentum_value": s.get("momentum_value", 0),
            "momentum_di": s.get("momentum_di", 0),
        }

    # First run — populate without alerting
    if not _initialized:
        _store_snapshot(current)
        _initialized = True
        log.info("Alert system initialized with ring buffer (no alerts on first run)")
        return []

    # Load multi-day context (3d/5d changes, directional liq z-scores, trend)
    multi_day = await _get_multi_day_batch()

    alerts = []

    for sym, cur in current.items():
        short_sym = sym.replace("USDT", "")
        oi_z = cur["oi_z"]
        fund_z = cur["funding_z"]
        liq_z = cur["liq_z"]
        vol_z = cur["volume_z"]
        price_chg = cur["price_change_24h_pct"]
        oi_chg = cur["oi_change_24h_pct"]
        ob_skew = cur.get("ob_skew", 0)
        ob_skew_z = cur.get("ob_skew_zscore", 0)
        price = cur["price"]

        # Multi-day context from daily_derivatives
        md = multi_day.get(sym, {})
        price_chg_3d = md.get("price_chg_3d", 0)
        price_chg_5d = md.get("price_chg_5d", 0)
        oi_chg_3d = md.get("oi_chg_3d", 0)
        oi_chg_5d = md.get("oi_chg_5d", 0)
        liq_long_z = md.get("liq_long_z", 0)
        liq_short_z = md.get("liq_short_z", 0)
        price_vs_sma = md.get("price_vs_sma", 0)
        trend = md.get("trend", "neutral")
        vol_declining_3d = md.get("vol_declining_3d", False)
        fund_delta = md.get("fund_delta", 0)
        price_momentum = md.get("price_momentum", 0)

        velocities = _compute_all_velocities(sym, cur)

        liq_clusters = await _check_liq_proximity(sym, price)
        # For confluence scoring, use closest cluster (if any)
        liq_prox = liq_clusters[0] if liq_clusters else None

        # Determine directional bias for confluence
        is_bullish = None
        if oi_z > 1 and fund_z > 0.5:
            is_bullish = True
        elif oi_z < -1 and fund_z < -0.5:
            is_bullish = False

        confluence, factors = _compute_confluence(cur, velocities, liq_prox, is_bullish)

        # Crash penalty: precompute directional adjustments
        _extreme_cnt = sum([abs(oi_z) > 2.0, abs(liq_z) > 2.0, abs(vol_z) > 2.0])
        _crash_long = -2 if (price_momentum < -5 and _extreme_cnt >= 2) else 0
        _crash_short = -2 if (price_momentum > 5 and _extreme_cnt >= 2) else 0

        # OI tier filter: skip weak alt signals entirely
        if sym not in TOP_OI_SYMBOLS and confluence < ALT_MIN_CONFLUENCE:
            continue

        # Momentum filter: skip signals against the trend
        _skip_long = trend == "down"
        _skip_short = trend == "up"

        # ── DIRECTIONAL ALERTS (backtest-aligned thresholds) ──
        # Signal families: only fire strongest timeframe (5d > 3d > 1d)
        fired_families: set[str] = set()

        # === SHORT signals ===

        # 1. OVERHEAT — OI + funding elevated (skip in uptrend — shorts into parabolas)
        if oi_z > OI_Z_OVERHEAT and fund_z > FUND_Z_OVERHEAT and trend != "up":
            tier = _score_to_tier(confluence + _crash_short)
            if tier:
                alerts.append(_build_directional_alert(
                    "overheat", sym, short_sym, "ПЕРЕГРЕВ — OI + Funding extreme",
                    cur, velocities, confluence + _crash_short, tier, factors,
                    indicators=[
                        "OI на экстремуме + лонги платят экстремальный фандинг",
                        "Каскадные ликвидации лонгов вероятны",
                    ],
                    action=[
                        "НЕ открывать новые лонги",
                        "Готовить шорт от верхней трендовой / сопротивления",
                    ],
                ))

        # 2. FUND SPIKE — DISABLED (3yr backtest: 108/324 trades, EV +0.1%, 45% timeouts, pure noise)
        # if fund_z > 1.5 and price_momentum > 3:
        #     tier = _score_to_tier(confluence + _crash_short)
        #     if tier:
        #         alerts.append(_build_directional_alert(
        #             "fund_spike", sym, short_sym, "FUND SPIKE — фандинг экстремальный",
        #             cur, velocities, confluence + _crash_short, tier, factors,
        #             indicators=[
        #                 f"Фандинг z: {fund_z:+.1f} при росте цены 5d: {price_momentum:+.1f}%",
        #                 "Лонги перегружены — коррекция вероятна",
        #             ],
        #             action=[
        #                 "Готовить шорт от сопротивления",
        #                 "Ждать разворот фандинга как подтверждение",
        #             ],
        #         ))

        # DIVERGENCE SQUEEZE family — strongest timeframe wins (5d > 3d > 1d)
        # div_squeeze_5d — DISABLED (negative EV across all exit strategies, setup backtest 2026-03-09)
        # if oi_chg_5d > 8 and price_chg_5d < -3:
        #     fired_families.add("div_squeeze")
        #     ...

        if oi_chg_3d > OI_CHG_3D_SQUEEZE and price_chg_3d < PRICE_CHG_3D_SQUEEZE and fund_z > FUND_Z_SQUEEZE_3D_MIN and not _skip_short:
            fired_families.add("div_squeeze")
            tier = _score_to_tier(confluence + _crash_short)
            if tier:
                alerts.append(_build_directional_alert(
                    "div_squeeze_3d", sym, short_sym, "ДИВЕРГЕНЦИЯ 3D — OI↑ Price↓",
                    cur, velocities, confluence + _crash_short, tier, factors,
                    indicators=[
                        f"OI +{oi_chg_3d:.1f}% при цене {price_chg_3d:+.1f}% за 3 дня",
                        "Устойчивое давление — продолжение снижения вероятно",
                    ],
                    action=[
                        "Шорт при откате к сопротивлению / верхней трендовой",
                    ],
                ))

        if "div_squeeze" not in fired_families and oi_chg > OI_CHG_1D_SQUEEZE and price_chg < PRICE_CHG_1D_SQUEEZE and fund_z > FUND_Z_SQUEEZE_1D_MIN and trend != "down" and not _skip_short:
            fired_families.add("div_squeeze")
            tier = _score_to_tier(confluence + _crash_short)
            if tier:
                alerts.append(_build_directional_alert(
                    "divergence_squeeze", sym, short_sym, "ДИВЕРГЕНЦИЯ 1D — OI↑ Price↓",
                    cur, velocities, confluence + _crash_short, tier, factors,
                    indicators=[
                        f"OI +{oi_chg:.1f}% при цене {price_chg:+.1f}% за 24ч",
                        "Новые позиции против тренда → давление продолжится",
                    ],
                    action=[
                        "НЕ ловить нож — тренд вниз подтверждён",
                        "Шорт при откате к сопротивлению",
                    ],
                ))

        # DIVERGENCE TOP family — strongest timeframe wins (3d > 1d)
        # div_top_3d DISABLED — 5/8 trades short at pvs 17-67% above SMA (parabolic), net negative over 3yr
        # if oi_chg_3d < -4 and price_chg_3d > 2.5:
        #     fired_families.add("div_top")
        #     tier = _score_to_tier(confluence + _crash_short)
        #     if tier:
        #         alerts.append(_build_directional_alert(
        #             "div_top_3d", sym, short_sym, "ДИВЕРГЕНЦИЯ ТОП 3D — OI↓ Price↑",
        #             cur, velocities, confluence + _crash_short, tier, factors,
        #             indicators=[
        #                 f"OI {oi_chg_3d:+.1f}% при росте {price_chg_3d:+.1f}% за 3 дня",
        #                 "Устойчивый выход позиций при росте — топ близко",
        #             ],
        #             action=[
        #                 "Сокращать лонги, искать шорт от сопротивления",
        #             ],
        #         ))

        if "div_top" not in fired_families and oi_chg < OI_CHG_1D_TOP and price_chg > PRICE_CHG_1D_TOP and trend != "up":
            fired_families.add("div_top")
            tier = _score_to_tier(confluence + _crash_short)
            if tier:
                alerts.append(_build_directional_alert(
                    "divergence_top", sym, short_sym, "ДИВЕРГЕНЦИЯ ТОП 1D — OI↓ Price↑",
                    cur, velocities, confluence + _crash_short, tier, factors,
                    indicators=[
                        f"OI {oi_chg:+.1f}% при росте цены {price_chg:+.1f}% за 24ч",
                        "Рост на закрытии шортов, новых покупателей нет",
                    ],
                    action=[
                        "НЕ добавлять лонги на текущих уровнях",
                        "Искать шорт от сопротивления",
                    ],
                ))

        # 8. DISTRIBUTION — цена растёт но объём падает (counter-trend by design, exempt from momentum filter)
        if price_chg_3d > PRICE_CHG_3D_DIST and vol_declining_3d and trend == "up":
            tier = _score_to_tier(confluence + _crash_short)
            if tier:
                alerts.append(_build_directional_alert(
                    "distribution", sym, short_sym, "РАСПРЕДЕЛЕНИЕ — рост без объёма",
                    cur, velocities, confluence + _crash_short, tier, factors,
                    indicators=[
                        f"Цена +{price_chg_3d:.1f}% за 3 дня при падающем объёме",
                        "Нет подтверждения ростом — слабость покупателей",
                    ],
                    action=[
                        "Сокращать лонги, готовить шорт",
                    ],
                ))

        # 9. OVEREXTENSION — цена далеко от SMA
        if PRICE_VS_SMA_OVEREXT_LO < price_vs_sma < PRICE_VS_SMA_OVEREXT_HI and fund_z > FUND_Z_OVEREXT and not _skip_short:
            tier = _score_to_tier(confluence + _crash_short)
            if tier:
                alerts.append(_build_directional_alert(
                    "overextension", sym, short_sym, f"ПЕРЕРАСТЯЖЕНИЕ — {price_vs_sma:+.1f}% от SMA",
                    cur, velocities, confluence + _crash_short, tier, factors,
                    indicators=[
                        f"Цена на {price_vs_sma:.1f}% выше SMA20 + фандинг повышен",
                        "Возврат к среднему вероятен",
                    ],
                    action=[
                        "НЕ открывать лонги, готовить шорт от сопротивления",
                    ],
                ))

        # 10. OI BUILDUP STALL — OI растёт но цена стоит → ловушка (relaxed)
        if oi_chg_3d > OI_CHG_3D_STALL and abs(price_chg_3d) < PRICE_CHG_3D_STALL and fund_z > FUND_Z_STALL and not _skip_short:
            tier = _score_to_tier(confluence + _crash_short)
            if tier:
                alerts.append(_build_directional_alert(
                    "oi_buildup_stall", sym, short_sym, "OI BUILDUP — рост OI без движения цены",
                    cur, velocities, confluence + _crash_short, tier, factors,
                    indicators=[
                        f"OI +{oi_chg_3d:.1f}% за 3 дня, цена {price_chg_3d:+.1f}%",
                        "Накопление позиций → ловушка, резкое движение вероятно",
                    ],
                    action=[
                        "Готовить шорт — лонг-трап вероятен",
                        "Стоп выше ближайшего сопротивления",
                    ],
                ))

        # === LONG signals ===

        # 11. CAPITULATION — OI + funding вымыты (not in downtrend — avoid falling knife)
        if oi_z < OI_Z_CAPITULATION and fund_z < FUND_Z_CAPITULATION and trend != "down":
            tier = _score_to_tier(confluence + _crash_long)
            if tier:
                alerts.append(_build_directional_alert(
                    "capitulation", sym, short_sym, "КАПИТУЛЯЦИЯ — вымытость + шорты платят",
                    cur, velocities, confluence + _crash_long, tier, factors,
                    indicators=[
                        "OI вымыт + шорты платят фандинг = слабые руки вышли",
                        "Зона накопления — высокая вероятность лонга",
                    ],
                    action=[
                        "Искать лонг от зон поддержки / нижней трендовой",
                        "Стоп под ближайший кластер ликвидаций",
                    ],
                ))

        # LIQ FLUSH family — DISABLED — MFE too small (avg 2.7%) for TP=5%, net negative over 3yr
        # if liq_z > 2.0 and price_chg_3d < -8 and oi_chg_3d < -5 and trend == "up" and fund_z < 0 and price_momentum < -5:
        #     fired_families.add("liq_flush")
        #     tier = _score_to_tier(confluence + _crash_long)
        #     if tier:
        #         alerts.append(_build_directional_alert(
        #             "liq_flush_3d", sym, short_sym, "LIQ FLUSH 3D — затяжной слив",
        #             cur, velocities, confluence + _crash_long, tier, factors,
        #             indicators=[
        #                 f"Цена {price_chg_3d:+.1f}%, OI {oi_chg_3d:+.1f}% за 3 дня",
        #                 "Затяжной слив — очищение рынка завершается",
        #             ],
        #             action=[
        #                 "Искать лонг от поддержки после стабилизации",
        #             ],
        #         ))

        # if "liq_flush" not in fired_families and liq_z > 2.0 and price_chg < -5 and oi_chg < -3 and trend == "up" and price_momentum < -5:
        #     fired_families.add("liq_flush")
        #     tier = _score_to_tier(confluence + _crash_long)
        #     if tier:
        #         alerts.append(_build_directional_alert(
        #             "liq_flush", sym, short_sym, "LIQ FLUSH — каскад + OI сброс",
        #             cur, velocities, confluence + _crash_long, tier, factors,
        #             indicators=[
        #                 "Каскадные ликвидации + слив OI = массовый сброс",
        #                 "Слабые лонги ликвидированы, рынок очищается",
        #             ],
        #             action=[
        #                 "НЕ шортить на минимумах — сброс уже произошёл",
        #                 "Ждать стабилизацию, затем лонг",
        #             ],
        #         ))

        # 14. VOL DIVERGENCE — объём аномальный + OI падает (only down/neutral)
        if vol_z > Z_MODERATE and oi_chg < -3 and abs(price_chg) > 2 and trend != "up":
            vd_direction = "long" if price_chg < 0 else "short"
            # Momentum filter for dynamic direction
            if not ((vd_direction == "long" and _skip_long) or (vd_direction == "short" and _skip_short)):
                _vd_crash = _crash_long if vd_direction == "long" else _crash_short
                tier = _score_to_tier(confluence + _vd_crash)
                if tier:
                    alerts.append(_build_directional_alert(
                        "vol_divergence", sym, short_sym, f"VOL DIVERGENCE — объём при сбросе OI",
                        cur, velocities, confluence + _vd_crash, tier, factors,
                        indicators=[
                            f"Объём z: {vol_z:+.1f}, OI {oi_chg:+.1f}% — закрытие позиций",
                            f"Направление: {'лонг (капитуляция)' if vd_direction == 'long' else 'шорт (фиксация)'}",
                        ],
                        action=[
                            f"{'Искать лонг после стабилизации' if vd_direction == 'long' else 'Искать шорт от сопротивления'}",
                        ],
                    ))

        # 15. LIQ LONG FLUSH — DISABLED (unpredictable in both directions)
        # if liq_long_z > 2.5 and price_chg < -4 and oi_chg < -3:
        #     ...pass...

        # 16. LIQ SHORT SQUEEZE — массовые ликвидации шортов → импульс вверх
        # Caps: price < 8% and oi < 20% filter late entries
        if liq_short_z > LIQ_SHORT_Z_SQUEEZE and PRICE_CHG_SQUEEZE_LO < price_chg < PRICE_CHG_SQUEEZE_HI and oi_chg < OI_CHG_SQUEEZE_CAP and fund_z < FUND_Z_SQUEEZE_CAP and trend != "down":
            tier = _score_to_tier(confluence + _crash_long)
            if tier:
                alerts.append(_build_directional_alert(
                    "liq_short_squeeze", sym, short_sym, "SHORT SQUEEZE — шорты ликвидируются",
                    cur, velocities, confluence + _crash_long, tier, factors,
                    indicators=[
                        f"Ликвидации шортов z: {liq_short_z:+.1f}, цена +{price_chg:.1f}%",
                        "Импульс вверх на ликвидациях — может продолжиться",
                    ],
                    action=[
                        "Лонг по тренду, цели — следующие кластеры ликвидаций шортов",
                    ],
                ))

        # 17. FUND REVERSAL — фандинг разворачивается от экстрема
        if fund_delta != 0:
            if fund_z > FUND_Z_REVERSAL and fund_delta < -FUND_DELTA_REVERSAL and not _skip_short:
                tier = _score_to_tier(confluence + _crash_short)
                if tier:
                    alerts.append(_build_directional_alert(
                        "fund_reversal", sym, short_sym, "FUND REVERSAL — разворот фандинга вниз",
                        cur, velocities, confluence + _crash_short, tier, factors,
                        indicators=[
                            f"Фандинг z: {fund_z:+.1f}, дельта 3д: {fund_delta:+.6f}",
                            "Фандинг разворачивается от экстрема — шорт",
                        ],
                        action=[
                            "Готовить шорт от сопротивления",
                        ],
                    ))
            elif fund_z < -FUND_Z_REVERSAL and fund_delta > FUND_DELTA_REVERSAL and not _skip_long:
                tier = _score_to_tier(confluence + _crash_long)
                if tier:
                    alerts.append(_build_directional_alert(
                        "fund_reversal", sym, short_sym, "FUND REVERSAL — разворот фандинга вверх",
                        cur, velocities, confluence + _crash_long, tier, factors,
                        indicators=[
                            f"Фандинг z: {fund_z:+.1f}, дельта 3д: {fund_delta:+.6f}",
                            "Фандинг разворачивается от экстрема — лонг",
                        ],
                        action=[
                            "Искать лонг от поддержки",
                        ],
                    ))

        # 18. OI FLUSH + VOL SPIKE — DISABLED (unpredictable in both directions)
        # if oi_chg < -5 and vol_z > 1.5 and price_chg < -3:
        #     ...pass...

        # === NEW SIGNALS (Phase A) ===
        _momentum_val = md.get("momentum_value", 0)
        _relative_vol = md.get("relative_volume", 0)

        # 19. MOMENTUM DIVERGENCE — price vs composite momentum disagree
        # Exempt from momentum filter (counter-trend by design)
        if price_chg_5d > 3 and _momentum_val < -20:
            tier = _score_to_tier(confluence + _crash_short)
            if tier:
                alerts.append(_build_directional_alert(
                    "momentum_divergence", sym, short_sym, "MOMENTUM DIV — цена↑ momentum↓",
                    cur, velocities, confluence + _crash_short, tier, factors,
                    indicators=[
                        f"Цена +{price_chg_5d:.1f}% за 5д, но momentum {_momentum_val:+.0f}",
                        "Дивергенция цены и моментума — разворот вероятен",
                    ],
                    action=[
                        "Готовить шорт от сопротивления",
                        "Стоп выше 5d high",
                    ],
                ))
        if price_chg_5d < -3 and _momentum_val > 20:
            tier = _score_to_tier(confluence + _crash_long)
            if tier:
                alerts.append(_build_directional_alert(
                    "momentum_divergence", sym, short_sym, "MOMENTUM DIV — цена↓ momentum↑",
                    cur, velocities, confluence + _crash_long, tier, factors,
                    indicators=[
                        f"Цена {price_chg_5d:+.1f}% за 5д, но momentum {_momentum_val:+.0f}",
                        "Дивергенция цены и моментума — разворот вверх вероятен",
                    ],
                    action=[
                        "Искать лонг от поддержки",
                        "Стоп ниже 5d low",
                    ],
                ))

        # 20. VOLUME SPIKE — PERMANENTLY REMOVED
        # Tested all variants (original, trend-aligned, 2-bar confirm, high-OI):
        # poisons system EV from +1.58% to -0.51% when enabled.

        # 21. LIQ RATIO EXTREME — skewed liquidations → reversal pressure
        if liq_long_z > 2.5 and liq_short_z < 1.0 and price_chg < -1:
            tier = _score_to_tier(confluence + _crash_long)
            if tier:
                alerts.append(_build_directional_alert(
                    "liq_ratio_extreme", sym, short_sym, "LIQ RATIO — лонги массово ликвидированы",
                    cur, velocities, confluence + _crash_long, tier, factors,
                    indicators=[
                        f"Liq long z: {liq_long_z:+.1f}, liq short z: {liq_short_z:+.1f}",
                        "Паническая ликвидация лонгов — дно близко",
                    ],
                    action=[
                        "Искать лонг после стабилизации",
                        "Стоп ниже кластера ликвидаций",
                    ],
                ))
        if liq_short_z > 2.5 and liq_long_z < 1.0 and price_chg > 1:
            tier = _score_to_tier(confluence + _crash_short)
            if tier:
                alerts.append(_build_directional_alert(
                    "liq_ratio_extreme", sym, short_sym, "LIQ RATIO — шорты массово ликвидированы",
                    cur, velocities, confluence + _crash_short, tier, factors,
                    indicators=[
                        f"Liq short z: {liq_short_z:+.1f}, liq long z: {liq_long_z:+.1f}",
                        "Short squeeze завершается — готовить шорт от top",
                    ],
                    action=[
                        "Готовить шорт от сопротивления после стабилизации",
                    ],
                ))

        # 22. FUND SPIKE — rehabilitated (extreme funding + price momentum → short)
        if fund_z > 1.5 and price_momentum > 3 and not _skip_short:
            tier = _score_to_tier(confluence + _crash_short)
            if tier:
                alerts.append(_build_directional_alert(
                    "fund_spike", sym, short_sym, "FUND SPIKE — фандинг экстремальный",
                    cur, velocities, confluence + _crash_short, tier, factors,
                    indicators=[
                        f"Фандинг z: {fund_z:+.1f} при росте цены 5d: {price_momentum:+.1f}%",
                        "Лонги перегружены — коррекция вероятна",
                    ],
                    action=[
                        "Готовить шорт от сопротивления",
                        "Ждать разворот фандинга как подтверждение",
                    ],
                ))

        # ── STRUCTURAL ALERTS ────────────────────────────────

        # 5. LIQ MAP — informational, no tier. 12h cooldown.
        #    Shows biggest liquidation clusters from historical OI accumulation.
        if liq_clusters:
            long_clusters = [c for c in liq_clusters if c["direction"] == "long"]
            short_clusters = [c for c in liq_clusters if c["direction"] == "short"]

            # Need at least one meaningful cluster
            top_long = long_clusters[0] if long_clusters else None
            top_short = short_clusters[0] if short_clusters else None
            if top_long or top_short:
                # Title parts with per-side volume
                parts = []
                if top_long:
                    parts.append(f"longs ↓{_fmt_price(top_long['level_price'])} ({_fmt_usd(top_long['long_vol'])})")
                if top_short:
                    parts.append(f"shorts ↑{_fmt_price(top_short['level_price'])} ({_fmt_usd(top_short['short_vol'])})")
                range_str = " | ".join(parts)

                # Body lines
                lines = [f"Price: {_fmt_price(price)} | OI_z: {oi_z:+.1f} | Fund_z: {fund_z:+.1f}"]
                if top_long:
                    lines.append(f"🔻 Long liq cluster: {_fmt_price(top_long['level_price'])} ({top_long['distance_pct']:.1f}% ниже, {_fmt_usd(top_long['long_vol'])})")
                if top_short:
                    lines.append(f"🔺 Short liq cluster: {_fmt_price(top_short['level_price'])} ({top_short['distance_pct']:.1f}% выше, {_fmt_usd(top_short['short_vol'])})")

                # Additional clusters (top 3 per side)
                for c in long_clusters[1:3]:
                    lines.append(f"  └ {_fmt_price(c['level_price'])} ({c['distance_pct']:.1f}%, {_fmt_usd(c['long_vol'])})")
                for c in short_clusters[1:3]:
                    lines.append(f"  └ {_fmt_price(c['level_price'])} ({c['distance_pct']:.1f}%, {_fmt_usd(c['short_vol'])})")

                body = "\n".join([
                    "📊 <b>What we see:</b>",
                    "\n".join(f"• {l}" for l in lines),
                    "",
                    "💡 Кластеры построены по исторической аккумуляции OI.",
                    "Пробой уровня = каскад ликвидаций → ускорение движения.",
                ])

                alerts.append({
                    "key": f"liq_proximity:{sym}",
                    "symbol": sym,
                    "price_change_pct": price_chg,
                    "tier": "INFO",
                    "confluence": 0,
                    "entry_price": price,
                    "cooldown_hours": 12,
                    "title": f"📍 {short_sym} LIQ MAP — {range_str}",
                    "body": body,
                })

        # 6. OB DIVERGENCE (scalp)
        if SCALP_ALERTS_ENABLED and _is_ob_divergence(price_chg, ob_skew, ob_skew_z):
            ob_confluence = max(confluence, CONFLUENCE_SETUP)
            tier = _score_to_tier(ob_confluence)
            if tier:
                if price_chg > 0 and ob_skew < 0:
                    trap_type = "ЛОВУШКА ДЛЯ ПОКУПАТЕЛЕЙ"
                    detail = "Цена растёт, но продавцы доминируют в стакане"
                    action_text = "Сокращать лонги, готовить шорт"
                else:
                    trap_type = "ЛОВУШКА ДЛЯ ПРОДАВЦОВ"
                    detail = "Цена падает, но покупатели доминируют в стакане"
                    action_text = "Ждать отскок, не добавлять шорты"

                alerts.append({
                    "key": f"ob_divergence:{sym}",
                    "symbol": sym,
                    "price_change_pct": price_chg,
                    "tier": tier,
                    "confluence": ob_confluence,
                    "entry_price": price,
                    "title": f"{TIER_EMOJI[tier]} {tier} | {short_sym} OB DIVERGENCE — {trap_type}",
                    "body": _format_alert_body(
                        what_we_see=[
                            f"Price: {_fmt_pct(price_chg)} 24h",
                            f"OB: {'asks heavy' if ob_skew < 0 else 'bids heavy'} (skew {ob_skew:+.2f}, z: {ob_skew_z:+.1f})",
                            detail,
                            f"OI_z: {oi_z:+.1f} | Fund_z: {fund_z:+.1f}",
                        ],
                        indicators=[
                            f"{'Продавцы доминируют в стакане при росте цены' if price_chg > 0 else 'Покупатели доминируют в стакане при падении цены'}",
                            "Расхождение стакана с ценой ловит фейковые движения до разворота",
                            f"Совпадение факторов: {ob_confluence} ({', '.join(factors[:3])})",
                        ],
                        action=[
                            action_text,
                            "Ждать пока стакан сойдётся с направлением цены",
                        ],
                        tier=tier,
                        confluence=ob_confluence,
                    ),
                })

        # 7. VOLUME ANOMALY (scalp)
        # Backtest: confluence <3 gives 35-40% hit (noise), ≥3 gives 54%.
        # Require confluence ≥ CONFLUENCE_SETUP to filter noise.
        if SCALP_ALERTS_ENABLED and vol_z > Z_MODERATE and (abs(oi_z) > 1.5 or abs(fund_z) > 1.5) and confluence >= CONFLUENCE_SETUP:
            tier = _score_to_tier(confluence)
            if tier:
                direction = "LONG" if price_chg > 0 else "SHORT" if price_chg < 0 else "—"
                alerts.append(_build_directional_alert(
                    "vol_anomaly", sym, short_sym, f"VOLUME ANOMALY — breakout {direction}",
                    cur, velocities, confluence, tier, factors,
                    indicators=[
                        f"Аномальный объём (z: {vol_z:+.1f}) + {'OI' if abs(oi_z) > 1.5 else 'фандинг'} подтверждает",
                        f"Направление: {direction}",
                        f"{'Кульминация покупок — осторожно' if price_chg > 5 and fund_z > 1.5 else 'Пробой может продолжиться' if abs(price_chg) > 2 else 'Объём без движения цены — накопление/распределение'}",
                    ],
                    action=[
                        f"Торговать {'лонг' if price_chg > 0 else 'шорт' if price_chg < 0 else 'по направлению'} при пробое уровня",
                        "Проверить карту ликвидаций для целей по прибыли",
                    ],
                ))

    # ── DIRECTIONAL ALERT CAP ────────────────────────────────
    # Keep only top-N directional alerts per cycle (prevents noise clusters)
    directional_alerts = [a for a in alerts if a.get("tier") in (TIER_SETUP, TIER_SIGNAL, TIER_TRIGGER)]
    non_directional = [a for a in alerts if a not in directional_alerts]
    if len(directional_alerts) > MAX_DIRECTIONAL_ALERTS_PER_CYCLE:
        directional_alerts.sort(key=lambda x: -(x.get("confluence", 0)))
        directional_alerts = directional_alerts[:MAX_DIRECTIONAL_ALERTS_PER_CYCLE]
    alerts = directional_alerts + non_directional

    # ── MACRO ALERTS ─────────────────────────────────────────

    # 8. REGIME SHIFT
    regime_alert = _check_regime_transition(current)
    if regime_alert:
        alerts.append(regime_alert)

    # 9. VOL REGIME (BTC/ETH)
    vol_alerts = await _check_vol_regime(current)
    alerts.extend(vol_alerts)

    # ── ATTACH TRADE SETUPS ──────────────────────────────────
    # For each directional alert, try to build a concrete trade plan
    _directional_types = {
            "overheat", "fund_spike", "divergence_squeeze", "div_squeeze_3d",
            "divergence_top", "div_top_3d", "distribution", "overextension", "oi_buildup_stall",
            "capitulation", "liq_flush", "liq_flush_3d", "vol_divergence",
            "liq_long_flush", "liq_short_squeeze", "fund_reversal", "oi_flush_vol",
            "momentum_divergence", "volume_spike", "liq_ratio_extreme",
        }
    for alert in alerts:
        key = alert.get("key", "")
        alert_type = key.split(":")[0] if ":" in key else ""
        if alert_type not in _directional_types:
            continue
        sym = alert.get("symbol", "")
        if not sym:
            continue
        structure = price_service.get_price_structure(sym)
        if not structure:
            continue
        expected_dir = _expected_direction(alert)
        if not expected_dir:
            continue
        # Get liq clusters for stop/tp context
        liq_clusters_for_setup = await _check_liq_proximity(sym, alert.get("entry_price", 0))
        setup = _build_trade_setup(expected_dir, alert.get("entry_price", 0), structure, liq_clusters_for_setup)
        if setup:
            alert["trade_setup"] = setup
            alert["body"] += _format_trade_setup(setup, liq_clusters_for_setup)

    # ── Exchange whale alerts ────────────────────────────────────────
    anomalies = derivatives_service.get_exchange_anomalies()
    for a in anomalies:
        short_sym = a["symbol"].replace("USDT", "")
        direction = "+" if a["delta"] > 0 else "-"
        alerts.append({
            "key": f"whale_oi:{a['symbol']}:{a['exchange']}",
            "symbol": a["symbol"],
            "tier": "INFO",
            "cooldown_hours": 6,
            "title": f"\U0001f40b {short_sym} — {a['exchange'].upper()} OI {direction}{_fmt_usd(abs(a['delta']))}",
            "body": f"• {a['exchange'].title()} OI: {_fmt_usd(a['prev'])} → {_fmt_usd(a['current'])} ({a['change_pct']:+.1f}%)\n"
                    f"• Binance OI stable ({a['bn_change_pct']:+.1f}%)\n"
                    f"• Возможно крупная позиция открыта/закрыта на {a['exchange'].title()}",
        })

    # Store snapshot
    _store_snapshot(current)

    return alerts


# ── Regime transition ────────────────────────────────────────────────

def _check_regime_transition(current: dict[str, dict]) -> dict | None:
    prev = _get_prev_snapshot()
    if not prev:
        return None

    top10 = sorted(current.items(), key=lambda x: x[1].get("open_interest_usd", 0), reverse=True)[:10]
    top10_prev = sorted(prev.items(), key=lambda x: x[1].get("open_interest_usd", 0), reverse=True)[:10]

    def _avg_composite(items):
        vals = [(d.get("oi_z", 0) + d.get("funding_z", 0) + d.get("liq_z", 0)) / 3
                for _, d in items if isinstance(d, dict)]
        return sum(vals) / len(vals) if vals else 0

    cur_c = _avg_composite(top10)
    prev_c = _avg_composite(top10_prev)
    cur_label, _ = _regime_label(cur_c)
    prev_label, _ = _regime_label(prev_c)

    if cur_label != prev_label:
        extreme_keywords = ("Deep Oversold", "Extreme")
        if any(kw in cur_label or kw in prev_label for kw in extreme_keywords):
            extreme_syms = []
            for name, d in top10:
                c = (d.get("oi_z", 0) + d.get("funding_z", 0) + d.get("liq_z", 0)) / 3
                if abs(c) > 1.5:
                    extreme_syms.append(f"{name.replace('USDT', '')} ({c:+.1f})")

            tier = TIER_TRIGGER if any(kw in cur_label for kw in extreme_keywords) else TIER_SIGNAL

            return {
                "key": "regime_transition",
                "symbol": "GLOBAL",
                "price_change_pct": 0,
                "tier": tier,
                "confluence": 6,
                "entry_price": 0,
                "title": f"{TIER_EMOJI[tier]} {tier} | REGIME SHIFT: {prev_label} → {cur_label}",
                "body": _format_alert_body(
                    what_we_see=[
                        f"Composite Z: {prev_c:+.2f} → {cur_c:+.2f}",
                        f"Extreme symbols: {', '.join(extreme_syms[:5]) or 'none'}",
                    ],
                    indicators=[
                        f"{'Рынок ПЕРЕГРЕТ — метрики в красной зоне' if cur_c > 1.5 else 'Рынок ВЫМЫТ — метрики в зелёной зоне' if cur_c < -1.5 else 'Переход между зонами'}",
                        f"{'Сокращать позиции, не набирать новые' if cur_c > 1.5 else 'Наращивать позиции от поддержек' if cur_c < -1.5 else 'Ждать подтверждения'}",
                    ],
                    action=[
                        f"{'Не открывать новые лонги, искать шорт' if cur_c > 1.5 else 'Искать лонг от зон поддержки' if cur_c < -1.5 else 'Наблюдать за развитием'}",
                        "Проверить общую картину на дашборде",
                    ],
                    tier=tier,
                    confluence=6,
                ),
            }
    return None


# ── Vol regime alerts (BTC/ETH) ──────────────────────────────────────

async def _check_vol_regime(current: dict[str, dict]) -> list[dict]:
    alerts = []
    try:
        db = get_db()
        for sym in ("BTCUSDT", "ETHUSDT"):
            row = await db.execute_fetchall(
                """SELECT iv_30d, rv_30d, vrp, vrp_zscore, skew_25d_zscore
                   FROM daily_volatility
                   WHERE symbol = ? AND iv_30d IS NOT NULL
                   ORDER BY date DESC LIMIT 1""",
                (sym,),
            )
            if not row:
                continue
            r = row[0]
            iv = r["iv_30d"] or 0
            rv = r["rv_30d"] or 0
            vrp_z = r["vrp_zscore"] or 0
            skew_z = r["skew_25d_zscore"] or 0
            short_name = sym.replace("USDT", "")
            sym_data = current.get(sym, {})

            # Vol compression
            # Backtest: RV never drops below 47% in crypto. Use IV-only as
            # forward-looking compression indicator. IV < 37% = historically low
            # (BTC: 42 days out of 502 in backtest period).
            if iv > 0 and iv < 37:
                tier = TIER_SIGNAL
                alerts.append({
                    "key": f"vol_compression:{sym}",
                    "symbol": sym,
                    "price_change_pct": sym_data.get("price_change_24h_pct", 0),
                    "tier": tier,
                    "confluence": 4,
                    "entry_price": sym_data.get("price", 0),
                    "title": f"{TIER_EMOJI[tier]} {tier} | {short_name} VOL COMPRESSION — IV {iv:.0f}% + RV {rv:.0f}%",
                    "body": _format_alert_body(
                        what_we_see=[
                            f"IV: {iv:.0f}% | RV: {rv:.0f}% | VRP: {r['vrp']:+.0f}%" if r["vrp"] else f"IV: {iv:.0f}% | RV: {rv:.0f}%",
                            f"VRP_z: {vrp_z:+.1f} | Skew_z: {skew_z:+.1f}",
                            f"OI_z: {sym_data.get('oi_z', 0):+.1f} | Fund_z: {sym_data.get('funding_z', 0):+.1f}",
                        ],
                        indicators=[
                            f"IV {iv:.0f}% — ниже исторических норм",
                            "Резкое движение назревает — направление определят метрики",
                            f"{'Путы дороже коллов — рынок боится падения' if skew_z > 1 else 'Перекос нейтральный' if abs(skew_z) < 1 else 'Коллы дороже путов — рынок ждёт роста'}",
                        ],
                        action=[
                            "Покупать волатильность (стрэддл/стрэнгл)",
                            "Уменьшить размер позиций — резкое движение в любую сторону",
                        ],
                        tier=tier,
                        confluence=4,
                    ),
                })

            # VRP extreme + skew (lowered from 4/2 to 2.5/1.5)
            if abs(vrp_z) > 2.5 and abs(skew_z) > 1.5:
                if vrp_z > 2.5 and skew_z > 1.5:
                    tier = TIER_TRIGGER if abs(vrp_z) > 4 else TIER_SIGNAL
                    conf = 7 if tier == TIER_TRIGGER else 5
                    alerts.append({
                        "key": f"vol_panic:{sym}",
                        "symbol": sym,
                        "price_change_pct": sym_data.get("price_change_24h_pct", 0),
                        "tier": tier,
                        "confluence": conf,
                        "entry_price": sym_data.get("price", 0),
                        "title": f"{TIER_EMOJI[tier]} {tier} | {short_name} ПАНИКА — VRP_z {vrp_z:+.1f} + Skew_z {skew_z:+.1f}",
                        "body": _format_alert_body(
                            what_we_see=[
                                f"IV: {iv:.0f}% | RV: {rv:.0f}% | VRP_z: {vrp_z:+.1f}",
                                f"Skew_z: {skew_z:+.1f} (путы дорогие)",
                                f"OI_z: {sym_data.get('oi_z', 0):+.1f} | Fund_z: {sym_data.get('funding_z', 0):+.1f}",
                            ],
                            indicators=[
                                "Волатильность дорогая + путы переоценены = панический хедж",
                                "Исторически — возможность для лонга против толпы",
                            ],
                            action=[
                                "Искать лонг от зон поддержки (против толпы)",
                                "Продавать волатильность: путы / стрэнглы",
                            ],
                            tier=tier,
                            confluence=conf,
                        ),
                    })
                elif vrp_z < -2.5 and skew_z < -1.5:
                    tier = TIER_TRIGGER if abs(vrp_z) > 4 else TIER_SIGNAL
                    conf = 7 if tier == TIER_TRIGGER else 5
                    alerts.append({
                        "key": f"vol_euphoria:{sym}",
                        "symbol": sym,
                        "price_change_pct": sym_data.get("price_change_24h_pct", 0),
                        "tier": tier,
                        "confluence": conf,
                        "entry_price": sym_data.get("price", 0),
                        "title": f"{TIER_EMOJI[tier]} {tier} | {short_name} ЭЙФОРИЯ — VRP_z {vrp_z:+.1f} + Skew_z {skew_z:+.1f}",
                        "body": _format_alert_body(
                            what_we_see=[
                                f"IV: {iv:.0f}% | RV: {rv:.0f}% | VRP_z: {vrp_z:+.1f}",
                                f"Skew_z: {skew_z:+.1f} (коллы дорогие)",
                                f"OI_z: {sym_data.get('oi_z', 0):+.1f} | Fund_z: {sym_data.get('funding_z', 0):+.1f}",
                            ],
                            indicators=[
                                "Волатильность дешёвая + коллы переоценены = эйфория",
                                "Пробой или коррекция вероятны",
                            ],
                            action=[
                                "Искать шорт от сопротивления / верхней трендовой",
                                "Покупать волатильность: путы / стрэддлы",
                            ],
                            tier=tier,
                            confluence=conf,
                        ),
                    })
    except Exception as e:
        log.warning(f"Vol alert check error: {e}")

    return alerts


# ── Forward tracking ─────────────────────────────────────────────────

_EXPECTED_DIRECTION = {
    # Short signals
    "overheat": "down",
    "fund_spike": "down",
    "divergence_squeeze": "down",
    "div_squeeze_3d": "down",
    # "div_squeeze_5d": disabled
    "divergence_top": "down",
    "div_top_3d": "down",
    "distribution": "down",
    "overextension": "down",
    "oi_buildup_stall": "down",
    # Long signals
    "capitulation": "up",
    "liq_flush": "up",
    "liq_flush_3d": "up",
    "vol_divergence": None,  # depends on price direction
    "liq_long_flush": "down",  # flipped: cascading liq = bearish
    "liq_short_squeeze": "up",
    "fund_reversal": None,  # depends on direction
    "oi_flush_vol": "up",
    # Phase A new signals
    "momentum_divergence": None,  # depends on direction (bidirectional)
    "volume_spike": None,         # depends on direction (bidirectional)
    "liq_ratio_extreme": None,    # depends on direction (bidirectional)
    # Structural / macro
    "liq_proximity": None,
    "ob_divergence": None,
    "vol_anomaly": None,
    "regime_transition": None,
    "vol_compression": None,
    "vol_panic": "up",
    "vol_euphoria": "down",
}


def _expected_direction(alert: dict) -> str | None:
    """Infer expected price direction from alert type."""
    key = alert.get("key", "")
    alert_type = key.split(":")[0] if ":" in key else key
    direction = _EXPECTED_DIRECTION.get(alert_type)
    if direction is not None:
        return direction
    # liq_proximity: informational, no directional expectation
    # ob_divergence: price up + asks heavy → down, price down + bids heavy → up
    if alert_type == "ob_divergence":
        return "down" if alert.get("price_change_pct", 0) > 0 else "up"
    # vol_anomaly: follows price direction
    if alert_type == "vol_anomaly":
        return "up" if alert.get("price_change_pct", 0) > 0 else "down"
    # vol_divergence: long if price dropped (capitulation), short if price rose (distribution)
    if alert_type == "vol_divergence":
        return "up" if alert.get("price_change_pct", 0) < 0 else "down"
    # fund_reversal: direction is in the title suffix
    if alert_type == "fund_reversal":
        title = alert.get("title", "")
        return "down" if "вниз" in title else "up" if "вверх" in title else None
    # momentum_divergence / volume_spike / liq_ratio_extreme: bidirectional
    if alert_type in ("momentum_divergence", "volume_spike", "liq_ratio_extreme"):
        title = alert.get("title", "")
        if "SHORT" in title or "шорт" in title.lower() or "↓" in title:
            return "down"
        if "LONG" in title or "лонг" in title.lower() or "↑" in title:
            return "up"
        return "up" if alert.get("price_change_pct", 0) < 0 else "down"
    return None


async def record_alert(alert: dict) -> None:
    """Record a fired alert for forward tracking."""
    try:
        db = get_db()
        key = alert.get("key", "")
        alert_type = key.split(":")[0] if ":" in key else key
        await db.execute(
            """INSERT INTO alert_tracking
               (alert_key, alert_type, symbol, tier, confluence, fired_at, entry_price, expected_direction)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                key,
                alert_type,
                alert.get("symbol", ""),
                alert.get("tier", ""),
                alert.get("confluence", 0),
                datetime.now(timezone.utc).isoformat(),
                alert.get("entry_price", 0),
                _expected_direction(alert),
            ),
        )
        await db.commit()
    except Exception as e:
        log.warning(f"record_alert error: {e}")


async def update_forward_returns() -> None:
    """Fill in forward returns for tracked alerts using daily_derivatives prices."""
    try:
        db = get_db()
        rows = await db.execute_fetchall(
            """SELECT id, symbol, fired_at, entry_price
               FROM alert_tracking
               WHERE entry_price > 0
                 AND (return_1d IS NULL OR return_3d IS NULL OR return_7d IS NULL)""",
        )
        if not rows:
            return

        now = datetime.now(timezone.utc)
        updated = 0
        for row in rows:
            fired_at = datetime.fromisoformat(row["fired_at"])
            hours_since = (now - fired_at).total_seconds() / 3600
            entry = row["entry_price"]
            sym = row["symbol"]
            if entry <= 0 or sym == "GLOBAL":
                continue

            updates = {}
            # Check each horizon
            for days, price_col, return_col in [
                (1, "price_1d", "return_1d"),
                (3, "price_3d", "return_3d"),
                (7, "price_7d", "return_7d"),
            ]:
                if hours_since < days * 24:
                    continue
                # Check if already filled
                existing = await db.execute_fetchall(
                    f"SELECT {return_col} FROM alert_tracking WHERE id = ?",
                    (row["id"],),
                )
                if existing and existing[0][return_col] is not None:
                    continue
                # Get price from daily_derivatives
                price_row = await db.execute_fetchall(
                    """SELECT close_price FROM daily_derivatives
                       WHERE symbol = ? AND date >= date(?, '+' || ? || ' days')
                       AND close_price IS NOT NULL
                       ORDER BY date ASC LIMIT 1""",
                    (sym, fired_at.strftime("%Y-%m-%d"), days),
                )
                if price_row and price_row[0]["close_price"]:
                    p = price_row[0]["close_price"]
                    ret = (p - entry) / entry * 100
                    updates[price_col] = p
                    updates[return_col] = round(ret, 2)

            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                vals = list(updates.values()) + [row["id"]]
                await db.execute(
                    f"UPDATE alert_tracking SET {set_clause} WHERE id = ?",
                    vals,
                )
                updated += 1

        if updated:
            await db.commit()
            log.info(f"Updated forward returns for {updated} alerts")
    except Exception as e:
        log.warning(f"update_forward_returns error: {e}")


async def _build_performance_section() -> str:
    """Build alert performance section for digest (last 7 days)."""
    try:
        db = get_db()
        rows = await db.execute_fetchall(
            """SELECT alert_type, expected_direction, return_1d
               FROM alert_tracking
               WHERE fired_at >= datetime('now', '-7 days')
                 AND return_1d IS NOT NULL
                 AND expected_direction IS NOT NULL""",
        )
        if not rows or len(rows) < 3:
            return ""

        by_type: dict[str, list[float]] = {}
        for r in rows:
            atype = r["alert_type"]
            ret = r["return_1d"]
            expected = r["expected_direction"]
            # Normalize: positive return = correct direction
            normalized = ret if expected == "up" else -ret
            by_type.setdefault(atype, []).append(normalized)

        lines = ["📊 <b>РЕЗУЛЬТАТЫ АЛЕРТОВ (7 дней):</b>"]
        total_count = 0
        total_correct = 0
        for atype, returns in sorted(by_type.items(), key=lambda x: len(x[1]), reverse=True):
            count = len(returns)
            correct = sum(1 for r in returns if r > 0)
            accuracy = correct / count * 100 if count else 0
            avg_ret = sum(returns) / count if count else 0
            emoji = "✅" if accuracy >= 60 else "⚠️" if accuracy >= 45 else "❌"
            lines.append(f"• {emoji} {atype}: {count} алертов, точность {accuracy:.0f}%, avg {avg_ret:+.1f}%")
            total_count += count
            total_correct += correct

        if total_count:
            total_acc = total_correct / total_count * 100
            lines.append(f"Итого: {total_count} алертов, точность {total_acc:.0f}%")
        lines.append("")
        return "\n".join(lines)
    except Exception as e:
        log.warning(f"_build_performance_section error: {e}")
        return ""


# ── Daily Digest ─────────────────────────────────────────────────────

async def build_daily_digest() -> list[str]:
    """Build full HTML market digest. Returns list of messages (split if >4096)."""
    # Update forward returns before building digest
    await update_forward_returns()

    screener = await derivatives_service.get_screener(sort="oi_zscore", limit=30)
    if not screener:
        return ["⚠️ No screener data available for digest."]

    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1. Composite regime
    top10 = sorted(screener, key=lambda x: x.get("open_interest_usd", 0), reverse=True)[:10]
    composite_values = []
    for s in top10:
        cz = (s.get("oi_zscore", 0) + s.get("funding_zscore", 0) + s.get("liq_zscore", 0)) / 3
        composite_values.append(cz)
    composite_z = sum(composite_values) / len(composite_values) if composite_values else 0
    regime_lbl, regime_comment = _regime_label(composite_z)

    parts = []
    parts.append(f"📊 <b>DAILY MARKET DIGEST — {today}</b>\n")
    parts.append(f"🎯 <b>REGIME:</b> {regime_lbl} (Composite Z: {composite_z:+.2f})")
    parts.append(f"<i>{regime_comment}</i>\n")

    # 2. Top movers
    movers = sorted(screener, key=lambda x: abs(x.get("price_change_24h_pct", 0)), reverse=True)[:8]
    parts.append("📈 <b>TOP MOVERS (24h):</b>")
    for s in movers:
        sym = s["symbol"].replace("USDT", "")
        price_chg = s.get("price_change_24h_pct", 0)
        oi_z = s.get("oi_zscore", 0)
        fund_z = s.get("funding_zscore", 0)
        flags = _z_flag(oi_z) + _z_flag(fund_z)
        parts.append(f"• <b>{sym}</b> {_fmt_pct(price_chg)} | OI_z {oi_z:+.1f} | Fund_z {fund_z:+.1f}{flags}")
    parts.append("")

    # 3. Signals
    signals = _generate_signals(screener)
    if signals:
        parts.append("⚠️ <b>SIGNALS:</b>")
        for i, sig in enumerate(signals[:5], 1):
            parts.append(f"{i}. {sig}")
        parts.append("")

    # 4. Volatility
    vol_section = await _build_vol_section(db)
    if vol_section:
        parts.append(vol_section)

    # 5. Funding arb
    arb_section = await _build_funding_arb_section()
    if arb_section:
        parts.append(arb_section)

    # 6. Momentum
    mom_section = await _build_momentum_section(db)
    if mom_section:
        parts.append(mom_section)

    # 7. Z-Score Velocity (NEW)
    vel_section = _build_velocity_section(screener)
    if vel_section:
        parts.append(vel_section)

    # 7.5. Alert performance
    perf_section = await _build_performance_section()
    if perf_section:
        parts.append(perf_section)

    # 8. Liq Proximity (NEW)
    liq_section = await _build_liq_proximity_section(screener)
    if liq_section:
        parts.append(liq_section)

    # 9. OB Divergences (NEW)
    ob_section = _build_ob_section(screener)
    if ob_section:
        parts.append(ob_section)

    # 10. Watchlist
    watchlist = _build_watchlist(screener)
    if watchlist:
        parts.append("📋 <b>WATCHLIST на завтра:</b>")
        for item in watchlist[:5]:
            parts.append(f"• {item}")

    text = "\n".join(parts)
    return _split_message(text)


# ── Digest helper functions ──────────────────────────────────────────

def _generate_signals(screener: list[dict]) -> list[str]:
    signals = []
    for s in screener:
        sym = s["symbol"].replace("USDT", "")
        oi_z = s.get("oi_zscore", 0)
        fund_z = s.get("funding_zscore", 0)
        liq_z = s.get("liq_zscore", 0)
        oi_chg = s.get("oi_change_24h_pct", 0)
        price_chg = s.get("price_change_24h_pct", 0)

        if oi_chg > 5 and price_chg < -1:
            signals.append(
                f"<b>{sym} OI/Price Divergence</b> — OI растёт ({_fmt_pct(oi_chg)}), цена падает ({_fmt_pct(price_chg)})\n"
                f"   → Накопление шортов, потенциальный сквиз"
            )
        elif oi_chg < -5 and price_chg > 1:
            signals.append(
                f"<b>{sym} OI/Price Divergence</b> — OI падает ({_fmt_pct(oi_chg)}), цена растёт ({_fmt_pct(price_chg)})\n"
                f"   → Рост на закрытии шортов, топ близко"
            )

        if abs(oi_z) >= 2:
            direction = "перегрев" if oi_z > 0 else "вымытость"
            action = "НЕ открывать новые позиции" if oi_z > 0 else "искать лонг"
            signals.append(f"<b>{sym} OI Extreme</b> (z: {oi_z:+.1f}) — {direction}\n   → {action}")

        if abs(fund_z) >= 2:
            direction = "SHORT zone" if fund_z > 0 else "LONG zone"
            signals.append(f"<b>{sym} Funding Extreme</b> (z: {fund_z:+.1f}) — {direction}\n   → Mean reversion вероятен")

        if liq_z > 2:
            signals.append(f"<b>{sym} Liq Cascade</b> (z: {liq_z:+.1f})\n   → После каскада: ждать flush для entry long")

    return signals


async def _build_vol_section(db) -> str:
    lines = ["🌊 <b>VOLATILITY:</b>"]
    has_data = False
    for sym in ("BTCUSDT", "ETHUSDT"):
        row = await db.execute_fetchall(
            """SELECT iv_30d, rv_30d, vrp, vrp_zscore, skew_25d, skew_25d_zscore
               FROM daily_volatility
               WHERE symbol = ? AND iv_30d IS NOT NULL
               ORDER BY date DESC LIMIT 1""",
            (sym,),
        )
        if not row:
            continue
        r = row[0]
        iv, rv = r["iv_30d"], r["rv_30d"]
        vrp_z, skew_z = r["vrp_zscore"], r["skew_25d_zscore"]
        p = [f"<b>{sym.replace('USDT', '')}:</b>"]
        if iv is not None:
            p.append(f"IV: {iv:.0f}%")
        if rv is not None:
            p.append(f"RV: {rv:.0f}%")
        if r["vrp"] is not None:
            p.append(f"VRP: {r['vrp']:+.0f}%")
        if vrp_z is not None:
            p.append(f"VRP_z: {vrp_z:+.1f}{_z_flag(vrp_z)}")
        if skew_z is not None:
            p.append(f"Skew_z: {skew_z:+.1f}{_z_flag(skew_z)}")
        lines.append("• " + " | ".join(p))
        has_data = True
        if iv is not None and rv is not None and iv < 30 and rv < 30:
            lines.append("  ⚡ Vol compression — breakout imminent")
    if not has_data:
        return ""
    lines.append("")
    return "\n".join(lines)


async def _build_funding_arb_section() -> str:
    try:
        rates = await funding_service.fetch_all_rates()
    except Exception:
        return ""
    if not rates:
        return ""

    by_sym: dict[str, list[dict]] = {}
    for r in rates:
        settlement = r.get("settlement_hours", 8)
        rate = r["rate"] * 8 if settlement == 1 else r["rate"]
        by_sym.setdefault(r["symbol"], []).append({"exchange": r["exchange"], "rate": rate})

    spreads = []
    for sym, exchanges in by_sym.items():
        if len(exchanges) < 2:
            continue
        sorted_ex = sorted(exchanges, key=lambda x: x["rate"])
        spread = sorted_ex[-1]["rate"] - sorted_ex[0]["rate"]
        if spread > 0.0001:
            spreads.append({
                "symbol": sym,
                "long_at": sorted_ex[0]["exchange"],
                "short_at": sorted_ex[-1]["exchange"],
                "spread": spread,
                "apr": spread * 3 * 365 * 100,
            })

    spreads.sort(key=lambda x: x["spread"], reverse=True)
    top = spreads[:3]
    if not top:
        return ""

    lines = ["💰 <b>FUNDING ARB (top 3):</b>"]
    for s in top:
        sym = s["symbol"].replace("USDT", "")
        lines.append(f"• <b>{sym}</b>: Long@{s['long_at']} / Short@{s['short_at']} | Spread: {s['spread'] * 100:.3f}% | APR: {s['apr']:.0f}%")
    lines.append("")
    return "\n".join(lines)


async def _build_momentum_section(db) -> str:
    rows = await db.execute_fetchall(
        """SELECT symbol, momentum_value, directional_intensity, vol_regime
           FROM daily_momentum
           WHERE date = (SELECT MAX(date) FROM daily_momentum)
           ORDER BY ABS(momentum_value) DESC
           LIMIT 5""",
    )
    if not rows:
        return ""
    lines = ["📊 <b>MOMENTUM (top 5 by |score|):</b>"]
    for r in rows:
        sym = r["symbol"].replace("USDT", "")
        mv = r["momentum_value"] or 0
        di = r["directional_intensity"] or 0
        badge = "OB" if mv > 70 else "OS" if mv < -70 else "Bull" if mv > 10 else "Bear" if mv < -10 else "Neut"
        lines.append(f"• <b>{sym}</b>: {mv:+.0f} [{badge}] | DI: {di:+.2f}")
    lines.append("")
    return "\n".join(lines)


def _build_velocity_section(screener: list[dict]) -> str:
    """Build z-score velocity section for digest."""
    if len(_snapshot_history) < VELOCITY_LOOKBACK:
        return ""

    accelerating = []
    for s in screener:
        sym = s["symbol"]
        short_sym = sym.replace("USDT", "")
        for metric, label, screener_key in [
            ("oi_z", "OI", "oi_zscore"),
            ("funding_z", "Fund", "funding_zscore"),
            ("liq_z", "Liq", "liq_zscore"),
        ]:
            current_z = s.get(screener_key, 0)
            vel = _compute_velocity(sym, metric, current_z)
            if vel is not None and abs(vel) > VELOCITY_SIGNIFICANT * 0.3:
                z_4h_ago = current_z - (vel * 4)
                arrow = "↑" if vel > 0 else "↓"
                accelerating.append((
                    abs(vel),
                    f"<b>{short_sym}</b> {label}_z: {z_4h_ago:+.1f} → {current_z:+.1f} ({arrow}{abs(vel):.2f}/h)"
                ))

    if not accelerating:
        return ""
    accelerating.sort(key=lambda x: x[0], reverse=True)
    lines = ["🚀 <b>Z-SCORE VELOCITY (accelerating):</b>"]
    for _, text in accelerating[:5]:
        lines.append(f"• {text}")
    lines.append("")
    return "\n".join(lines)


async def _build_liq_proximity_section(screener: list[dict]) -> str:
    """Build liq proximity section for digest."""
    close_symbols = []
    for s in screener:
        sym = s["symbol"]
        price = s.get("price", 0)
        if price <= 0:
            continue
        clusters = await _check_liq_proximity(sym, price)
        for prox in clusters:
            short_sym = sym.replace("USDT", "")
            close_symbols.append((
                prox["distance_pct"],
                f"<b>{short_sym}</b>: {_fmt_price(prox['level_price'])} ({prox['direction']}s, -{prox['distance_pct']:.1f}%, {_fmt_usd(prox['volume_usd'])})"
            ))
    if not close_symbols:
        return ""
    close_symbols.sort(key=lambda x: x[0])
    lines = [f"💥 <b>LIQ PROXIMITY (< {LIQ_PROXIMITY_PCT:.0f}% от кластера, ≤25x):</b>"]
    for _, text in close_symbols[:8]:
        lines.append(f"• {text}")
    lines.append("")
    return "\n".join(lines)


def _build_ob_section(screener: list[dict]) -> str:
    """Build OB divergence section for digest."""
    divergences = []
    for s in screener:
        price_chg = s.get("price_change_24h_pct", 0)
        ob_skew = s.get("ob_skew", 0)
        ob_skew_z = s.get("ob_skew_zscore", 0)
        if _is_ob_divergence(price_chg, ob_skew, ob_skew_z):
            sym = s["symbol"].replace("USDT", "")
            direction = "asks heavy" if ob_skew < 0 else "bids heavy"
            trap = "bull trap risk" if price_chg > 0 else "bear trap / absorption"
            divergences.append(f"<b>{sym}</b>: price {_fmt_pct(price_chg)} но OB {direction} (z: {ob_skew_z:+.1f}) — {trap}")
    if not divergences:
        return ""
    lines = ["📖 <b>OB DIVERGENCES:</b>"]
    for text in divergences[:4]:
        lines.append(f"• {text}")
    lines.append("")
    return "\n".join(lines)


def _build_watchlist(screener: list[dict]) -> list[str]:
    items = []
    for s in screener:
        sym = s["symbol"].replace("USDT", "")
        oi_z = s.get("oi_zscore", 0)
        fund_z = s.get("funding_zscore", 0)
        liq_z = s.get("liq_zscore", 0)
        reasons = []
        if abs(oi_z) >= 1.5:
            reasons.append(f"OI_z {oi_z:+.1f}")
        if abs(fund_z) >= 1.5:
            reasons.append(f"Fund_z {fund_z:+.1f}")
        if liq_z > 1.5:
            reasons.append(f"Liq_z {liq_z:+.1f}")
        if reasons:
            items.append(f"<b>{sym}</b> — {', '.join(reasons)}")
    return items


# ── Utilities ────────────────────────────────────────────────────────

def _split_message(text: str, limit: int = 4096) -> list[str]:
    if len(text) <= limit:
        return [text]
    messages = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 > limit:
            if current:
                messages.append(current.rstrip())
            current = paragraph
        else:
            current = current + "\n\n" + paragraph if current else paragraph
    if current:
        messages.append(current.rstrip())
    return messages
