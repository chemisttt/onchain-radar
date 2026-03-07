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
from services import derivatives_service, funding_service
from services.derivatives_service import SYMBOLS

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

# Snapshot ring buffer: store every 5th call (=5min), keep 144 (=12h)
SNAPSHOT_INTERVAL = 5
SNAPSHOT_HISTORY_SIZE = 144
VELOCITY_LOOKBACK = 48  # 48 * 5min = 4h

VELOCITY_SIGNIFICANT = 0.5  # per hour

LIQ_PROXIMITY_PCT = 3.0
LIQ_MIN_WEIGHT = 0.20

OB_PRICE_DIVERGENCE_PCT = 2.0
OB_SKEW_DIVERGENCE_THRESHOLD = 0.15
OB_SKEW_Z_CONFIRMATION = 1.5

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
    """Convert confluence score to tier. Returns None if below minimum."""
    if score >= CONFLUENCE_TRIGGER:
        return TIER_TRIGGER
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

async def _check_liq_proximity(sym: str, current_price: float) -> dict | None:
    """Check if price is near a high-weight liquidation cluster."""
    from services.liquidation_service import (
        LEVERAGE_TIERS, LEVERAGE_WEIGHTS, _compute_theoretical_levels,
    )
    levels = await _compute_theoretical_levels(sym)
    if not levels or current_price <= 0:
        return None

    closest = None
    closest_distance = float("inf")

    for level in levels:
        lev_price = level["price"]
        leverage = level["leverage"]
        tier_idx = LEVERAGE_TIERS.index(leverage) if leverage in LEVERAGE_TIERS else -1
        if tier_idx < 0:
            continue
        weight = LEVERAGE_WEIGHTS[tier_idx]
        if weight < LIQ_MIN_WEIGHT:
            continue

        distance_pct = abs(current_price - lev_price) / current_price * 100
        if distance_pct < closest_distance:
            closest_distance = distance_pct
            is_long_liq = level["long_vol"] > 0
            closest = {
                "level_price": lev_price,
                "distance_pct": round(distance_pct, 2),
                "direction": "long" if is_long_liq else "short",
                "leverage": leverage,
                "volume_usd": level["long_vol"] if is_long_liq else level["short_vol"],
            }

    if closest and closest["distance_pct"] < LIQ_PROXIMITY_PCT:
        return closest
    return None


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

    indicators.append(f"Confluence: {confluence} ({', '.join(factors[:3])})")

    return {
        "key": f"{key}:{sym}",
        "tier": tier,
        "confluence": confluence,
        "title": f"{TIER_EMOJI[tier]} {tier} | {short_sym} {title_suffix}",
        "body": _format_alert_body(what_we_see, indicators, action, tier, confluence),
    }


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

        velocities = _compute_all_velocities(sym, cur)

        liq_prox = await _check_liq_proximity(sym, price)

        # Determine directional bias for confluence
        is_bullish = None
        if oi_z > 1 and fund_z > 0.5:
            is_bullish = True
        elif oi_z < -1 and fund_z < -0.5:
            is_bullish = False

        confluence, factors = _compute_confluence(cur, velocities, liq_prox, is_bullish)

        # ── DIRECTIONAL ALERTS ───────────────────────────────

        # 1. OVERHEAT
        if oi_z > Z_MODERATE and fund_z > Z_MODERATE:
            tier = _score_to_tier(confluence)
            if tier:
                alerts.append(_build_directional_alert(
                    "overheat", sym, short_sym, "ПЕРЕГРЕВ — OI + Funding extreme",
                    cur, velocities, confluence, tier, factors,
                    indicators=[
                        "OI на экстремуме + лонги платят экстремальный фандинг",
                        "Каскадные ликвидации лонгов вероятны",
                    ],
                    action=[
                        "НЕ открывать новые лонги",
                        "Готовить шорт на наклонной сверху",
                        "Ждать разгрузку OI (drop > 5%) для re-entry long",
                    ],
                ))

        # 2. CAPITULATION
        if oi_z < -Z_MODERATE and fund_z < -Z_MODERATE:
            tier = _score_to_tier(confluence)
            if tier:
                alerts.append(_build_directional_alert(
                    "capitulation", sym, short_sym, "КАПИТУЛЯЦИЯ — вымытость + шорты платят",
                    cur, velocities, confluence, tier, factors,
                    indicators=[
                        "OI вымыт + шорты платят фандинг = слабые руки вышли",
                        "Зона накопления — high-probability long",
                    ],
                    action=[
                        "Искать лонг на зонах интереса / наклонных снизу",
                        "Подтверждение: OI начинает расти с текущих",
                        "Stop под ближайший liq cluster",
                    ],
                ))

        # 3. DIVERGENCE — OI vs Price
        if oi_chg > 8 and price_chg < -2:
            tier = _score_to_tier(confluence)
            if tier:
                alerts.append(_build_directional_alert(
                    "divergence_squeeze", sym, short_sym, "ДИВЕРГЕНЦИЯ — OI↑ Price↓ (сквиз)",
                    cur, velocities, confluence, tier, factors,
                    indicators=[
                        "OI растёт при падении цены → накопление шортов",
                        "Short squeeze вероятен при развороте",
                        f"{'Funding отрицательный — шорты перегружены' if fund_z < -0.5 else 'Funding нейтральный — давление умеренное'}",
                    ],
                    action=[
                        "Готовить лонг на сквиз-уровнях",
                        "Entry при первых признаках разворота + volume",
                    ],
                ))
        elif oi_chg < -8 and price_chg > 4:
            tier = _score_to_tier(confluence)
            if tier:
                alerts.append(_build_directional_alert(
                    "divergence_top", sym, short_sym, "ДИВЕРГЕНЦИЯ — OI↓ Price↑ (топ)",
                    cur, velocities, confluence, tier, factors,
                    indicators=[
                        "OI падает при росте цены → рост на закрытии шортов",
                        "Нет новых покупателей — топ близко",
                    ],
                    action=[
                        "НЕ добавлять лонги на текущих уровнях",
                        "Искать шорт при касании наклонной сверху",
                    ],
                ))

        # 4. LIQ FLUSH
        if liq_z > Z_MODERATE and price_chg < -4 and oi_chg < -3:
            tier = _score_to_tier(confluence)
            if tier:
                alerts.append(_build_directional_alert(
                    "liq_flush", sym, short_sym, "LIQ FLUSH — каскад + OI сброс",
                    cur, velocities, confluence, tier, factors,
                    indicators=[
                        "Каскадные ликвидации + слив OI = flush event",
                        "Слабые лонги ликвидированы, рынок очищается",
                        "После flush — bounce вероятен (mean reversion)",
                    ],
                    action=[
                        "НЕ шортить на лоях — flush уже произошёл",
                        "Ждать стабилизацию (1-2 свечи), затем лонг",
                        f"{'Funding отрицательный — подтверждает flush' if fund_z < 0 else 'Funding ещё положительный — flush может продолжиться'}",
                    ],
                ))

        # ── STRUCTURAL ALERTS ────────────────────────────────

        # 5. LIQ PROXIMITY
        if liq_prox:
            prox_confluence = max(confluence, CONFLUENCE_SETUP)
            tier = _score_to_tier(prox_confluence)
            if tier:
                direction = liq_prox["direction"]
                dist = liq_prox["distance_pct"]
                lev_price = liq_prox["level_price"]
                leverage = liq_prox["leverage"]
                vol = liq_prox["volume_usd"]

                what_we_see = [
                    f"Nearest liq: ${lev_price:,.0f} ({leverage}x {direction}s, -{dist:.1f}%)",
                    f"Estimated volume at level: {_fmt_usd(vol)}",
                    f"Price: ${price:,.2f} | OI_z: {oi_z:+.1f} | Fund_z: {fund_z:+.1f}",
                ]
                what_we_see.extend(_velocity_context_lines(velocities))

                alerts.append({
                    "key": f"liq_proximity:{sym}:{direction}",
                    "tier": tier,
                    "confluence": prox_confluence,
                    "title": f"{TIER_EMOJI[tier]} {tier} | {short_sym} LIQ PROXIMITY — {direction} cluster ${lev_price:,.0f}",
                    "body": _format_alert_body(
                        what_we_see,
                        indicators=[
                            "Цена тяготеет к ликвидационным кластерам (magnetic effect)",
                            f"{'Каскадные ликвидации лонгов ниже этого уровня' if direction == 'long' else 'Short squeeze выше этого уровня'}",
                            f"Confluence: {prox_confluence} ({', '.join(factors[:3])})",
                        ],
                        action=[
                            f"{'Ждать bounce у/рядом ${:,.0f}'.format(lev_price) if direction == 'long' else 'Ждать rejection у/рядом ${:,.0f}'.format(lev_price)}",
                            f"{'Ставить биды чуть ниже liq level' if direction == 'long' else 'Тайтить стопы выше liq level'}",
                            "Мониторить real-time liqs для подтверждения каскада",
                        ],
                        tier=tier,
                        confluence=prox_confluence,
                    ),
                })

        # 6. OB DIVERGENCE
        if _is_ob_divergence(price_chg, ob_skew, ob_skew_z):
            ob_confluence = max(confluence, CONFLUENCE_SETUP)
            tier = _score_to_tier(ob_confluence)
            if tier:
                if price_chg > 0 and ob_skew < 0:
                    trap_type = "BULL TRAP risk"
                    detail = "Цена растёт, но asks доминируют в OB"
                    action_text = "Сокращать лонги, готовить шорт"
                else:
                    trap_type = "BEAR TRAP / absorption"
                    detail = "Цена падает, но bids доминируют в OB"
                    action_text = "Ждать bounce, не добавлять шорты"

                alerts.append({
                    "key": f"ob_divergence:{sym}",
                    "tier": tier,
                    "confluence": ob_confluence,
                    "title": f"{TIER_EMOJI[tier]} {tier} | {short_sym} OB DIVERGENCE — {trap_type}",
                    "body": _format_alert_body(
                        what_we_see=[
                            f"Price: {_fmt_pct(price_chg)} 24h",
                            f"OB: {'asks heavy' if ob_skew < 0 else 'bids heavy'} (skew {ob_skew:+.2f}, z: {ob_skew_z:+.1f})",
                            detail,
                            f"OI_z: {oi_z:+.1f} | Fund_z: {fund_z:+.1f}",
                        ],
                        indicators=[
                            f"{'Asks доминируют при росте — продавцы абсорбируют' if price_chg > 0 else 'Bids доминируют при падении — покупатели абсорбируют'}",
                            "OB divergence ловит фейковые движения до разворота",
                            f"Confluence: {ob_confluence} ({', '.join(factors[:3])})",
                        ],
                        action=[
                            action_text,
                            "Ждать выравнивания OB skew с ценой для подтверждения тренда",
                        ],
                        tier=tier,
                        confluence=ob_confluence,
                    ),
                })

        # 7. VOLUME ANOMALY
        if vol_z > Z_MODERATE and (abs(oi_z) > 1.5 or abs(fund_z) > 1.5):
            tier = _score_to_tier(confluence)
            if tier:
                direction = "LONG" if price_chg > 0 else "SHORT" if price_chg < 0 else "—"
                alerts.append(_build_directional_alert(
                    "vol_anomaly", sym, short_sym, f"VOLUME ANOMALY — breakout {direction}",
                    cur, velocities, confluence, tier, factors,
                    indicators=[
                        f"Volume extreme (z: {vol_z:+.1f}) + {'OI' if abs(oi_z) > 1.5 else 'Funding'} подтверждает",
                        f"Breakout direction: {direction}",
                        f"{'Кульминация покупок — осторожно' if price_chg > 5 and fund_z > 1.5 else 'Breakout может продолжиться' if abs(price_chg) > 2 else 'Volume без цены — накопление/распределение'}",
                    ],
                    action=[
                        f"Торговать {'лонг' if price_chg > 0 else 'шорт' if price_chg < 0 else 'по направлению'} при пробое уровня",
                        "Проверить liq map для TP уровней",
                    ],
                ))

    # ── MACRO ALERTS ─────────────────────────────────────────

    # 8. REGIME SHIFT
    regime_alert = _check_regime_transition(current)
    if regime_alert:
        alerts.append(regime_alert)

    # 9. VOL REGIME (BTC/ETH)
    vol_alerts = await _check_vol_regime(current)
    alerts.extend(vol_alerts)

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
                "tier": tier,
                "confluence": 6,
                "title": f"{TIER_EMOJI[tier]} {tier} | REGIME SHIFT: {prev_label} → {cur_label}",
                "body": _format_alert_body(
                    what_we_see=[
                        f"Composite Z: {prev_c:+.2f} → {cur_c:+.2f}",
                        f"Extreme symbols: {', '.join(extreme_syms[:5]) or 'none'}",
                    ],
                    indicators=[
                        f"{'Рынок ПЕРЕГРЕТ — метрики в красной зоне' if cur_c > 1.5 else 'Рынок ВЫМЫТ — метрики в зелёной зоне' if cur_c < -1.5 else 'Переход между зонами'}",
                        f"{'Risk-off: сокращать экспозицию' if cur_c > 1.5 else 'Risk-on: наращивать экспозицию' if cur_c < -1.5 else 'Ждать подтверждения'}",
                    ],
                    action=[
                        f"{'Не открывать новые лонги, искать шорт-сетапы' if cur_c > 1.5 else 'Искать лонг-сетапы на зонах интереса' if cur_c < -1.5 else 'Наблюдать за развитием'}",
                        "Проверить Global Dashboard для полной картины",
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
            if iv > 0 and rv > 0 and iv < 30 and rv < 30:
                tier = TIER_SIGNAL
                alerts.append({
                    "key": f"vol_compression:{sym}",
                    "tier": tier,
                    "confluence": 4,
                    "title": f"{TIER_EMOJI[tier]} {tier} | {short_name} VOL COMPRESSION — IV {iv:.0f}% + RV {rv:.0f}%",
                    "body": _format_alert_body(
                        what_we_see=[
                            f"IV: {iv:.0f}% | RV: {rv:.0f}% | VRP: {r['vrp']:+.0f}%" if r["vrp"] else f"IV: {iv:.0f}% | RV: {rv:.0f}%",
                            f"VRP_z: {vrp_z:+.1f} | Skew_z: {skew_z:+.1f}",
                            f"OI_z: {sym_data.get('oi_z', 0):+.1f} | Fund_z: {sym_data.get('funding_z', 0):+.1f}",
                        ],
                        indicators=[
                            "IV и RV оба ниже 30% — историческое сжатие",
                            "Breakout imminent — направление определят метрики",
                            f"{'Skew puts > calls — рынок боится падения' if skew_z > 1 else 'Skew neutral' if abs(skew_z) < 1 else 'Skew calls > puts — рынок ждёт роста'}",
                        ],
                        action=[
                            "Покупать волатильность (long straddle/strangle)",
                            "Уменьшить размер позиций — breakout в любую сторону",
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
                        "tier": tier,
                        "confluence": conf,
                        "title": f"{TIER_EMOJI[tier]} {tier} | {short_name} ПАНИКА — VRP_z {vrp_z:+.1f} + Skew_z {skew_z:+.1f}",
                        "body": _format_alert_body(
                            what_we_see=[
                                f"IV: {iv:.0f}% | RV: {rv:.0f}% | VRP_z: {vrp_z:+.1f}",
                                f"Skew_z: {skew_z:+.1f} (путы дорогие)",
                                f"OI_z: {sym_data.get('oi_z', 0):+.1f} | Fund_z: {sym_data.get('funding_z', 0):+.1f}",
                            ],
                            indicators=[
                                "Rich Vol + путы переоценены = панический хедж",
                                "Исторически — contrarian long opportunity",
                            ],
                            action=[
                                "Искать лонг на зоне интереса (contrarian)",
                                "Sell vol: продавать путы / strangles",
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
                        "tier": tier,
                        "confluence": conf,
                        "title": f"{TIER_EMOJI[tier]} {tier} | {short_name} ЭЙФОРИЯ — VRP_z {vrp_z:+.1f} + Skew_z {skew_z:+.1f}",
                        "body": _format_alert_body(
                            what_we_see=[
                                f"IV: {iv:.0f}% | RV: {rv:.0f}% | VRP_z: {vrp_z:+.1f}",
                                f"Skew_z: {skew_z:+.1f} (коллы дорогие)",
                                f"OI_z: {sym_data.get('oi_z', 0):+.1f} | Fund_z: {sym_data.get('funding_z', 0):+.1f}",
                            ],
                            indicators=[
                                "Cheap Vol + коллы переоценены = эйфория",
                                "Buy vol: breakout/коррекция вероятны",
                            ],
                            action=[
                                "Искать шорт на наклонной сверху",
                                "Buy vol: покупать путы / straddles",
                            ],
                            tier=tier,
                            confluence=conf,
                        ),
                    })
    except Exception as e:
        log.warning(f"Vol alert check error: {e}")

    return alerts


# ── Daily Digest ─────────────────────────────────────────────────────

async def build_daily_digest() -> list[str]:
    """Build full HTML market digest. Returns list of messages (split if >4096)."""
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
        prox = await _check_liq_proximity(sym, price)
        if prox:
            short_sym = sym.replace("USDT", "")
            close_symbols.append((
                prox["distance_pct"],
                f"<b>{short_sym}</b>: ${prox['level_price']:,.0f} ({prox['leverage']}x {prox['direction']}s, -{prox['distance_pct']:.1f}%)"
            ))
    if not close_symbols:
        return ""
    close_symbols.sort(key=lambda x: x[0])
    lines = ["💥 <b>LIQ PROXIMITY (< 3% от кластера):</b>"]
    for _, text in close_symbols[:5]:
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
