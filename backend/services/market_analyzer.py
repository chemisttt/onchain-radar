"""Market analyzer — pure analytics, no Telegram dependency.

Two main functions:
- build_daily_digest() → list[str]  (HTML-formatted digest messages)
- check_alerts() → list[dict]       (triggered alert dicts)
"""

import logging
from datetime import datetime, timezone

from db import get_db
from services import derivatives_service, funding_service, orderbook_service
from services.derivatives_service import SYMBOLS

log = logging.getLogger("market_analyzer")

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


# ── Daily Digest ─────────────────────────────────────────────────────

async def build_daily_digest() -> list[str]:
    """Build full HTML market digest. Returns list of messages (split if >4096)."""
    screener = await derivatives_service.get_screener(sort="oi_zscore", limit=30)
    if not screener:
        return ["⚠️ No screener data available for digest."]

    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── 1. Composite regime (avg of top 10 by OI)
    top10 = sorted(screener, key=lambda x: x.get("open_interest_usd", 0), reverse=True)[:10]
    composite_values = []
    for s in top10:
        cz = (s.get("oi_zscore", 0) + s.get("funding_zscore", 0) + s.get("liq_zscore", 0)) / 3
        composite_values.append(cz)
    composite_z = sum(composite_values) / len(composite_values) if composite_values else 0
    regime_label, regime_comment = _regime_label(composite_z)

    parts = []
    parts.append(f"📊 <b>DAILY MARKET DIGEST — {today}</b>\n")
    parts.append(f"🎯 <b>REGIME:</b> {regime_label} (Composite Z: {composite_z:+.2f})")
    parts.append(f"<i>{regime_comment}</i>\n")

    # ── 2. Top movers (by abs price change)
    movers = sorted(screener, key=lambda x: abs(x.get("price_change_24h_pct", 0)), reverse=True)[:8]
    parts.append("📈 <b>TOP MOVERS (24h):</b>")
    for s in movers:
        sym = s["symbol"].replace("USDT", "")
        price_chg = s.get("price_change_24h_pct", 0)
        oi_z = s.get("oi_zscore", 0)
        fund_z = s.get("funding_zscore", 0)
        flags = _z_flag(oi_z) + _z_flag(fund_z)
        parts.append(
            f"• <b>{sym}</b> {_fmt_pct(price_chg)} | "
            f"OI_z {oi_z:+.1f} | Fund_z {fund_z:+.1f}{flags}"
        )
    parts.append("")

    # ── 3. Signals (divergences, extremes)
    signals = _generate_signals(screener)
    if signals:
        parts.append("⚠️ <b>SIGNALS:</b>")
        for i, sig in enumerate(signals[:5], 1):
            parts.append(f"{i}. {sig}")
        parts.append("")

    # ── 4. Volatility (BTC/ETH from daily_volatility)
    vol_section = await _build_vol_section(db)
    if vol_section:
        parts.append(vol_section)

    # ── 5. Funding arb (top 3 spreads)
    arb_section = await _build_funding_arb_section()
    if arb_section:
        parts.append(arb_section)

    # ── 6. Momentum (top movers)
    mom_section = await _build_momentum_section(db)
    if mom_section:
        parts.append(mom_section)

    # ── 7. Watchlist
    watchlist = _build_watchlist(screener)
    if watchlist:
        parts.append("📋 <b>WATCHLIST на завтра:</b>")
        for item in watchlist[:5]:
            parts.append(f"• {item}")

    text = "\n".join(parts)
    return _split_message(text)


def _generate_signals(screener: list[dict]) -> list[str]:
    """Generate strategic signals from current data."""
    signals = []

    for s in screener:
        sym = s["symbol"].replace("USDT", "")
        oi_z = s.get("oi_zscore", 0)
        fund_z = s.get("funding_zscore", 0)
        liq_z = s.get("liq_zscore", 0)
        oi_chg = s.get("oi_change_24h_pct", 0)
        price_chg = s.get("price_change_24h_pct", 0)

        # OI/Price divergence
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

        # OI extreme
        if abs(oi_z) >= 2:
            direction = "перегрев" if oi_z > 0 else "вымытость"
            action = "НЕ открывать новые позиции" if oi_z > 0 else "искать лонг на зонах интереса"
            signals.append(
                f"<b>{sym} OI Extreme</b> (z: {oi_z:+.1f}) — {direction}\n"
                f"   → {action}"
            )

        # Funding extreme
        if abs(fund_z) >= 2:
            direction = "SHORT zone" if fund_z > 0 else "LONG zone"
            signals.append(
                f"<b>{sym} Funding Extreme</b> (z: {fund_z:+.1f}) — {direction}\n"
                f"   → Mean reversion вероятен"
            )

        # Liq cascade
        if liq_z > 2:
            signals.append(
                f"<b>{sym} Liq Cascade</b> (z: {liq_z:+.1f})\n"
                f"   → После каскада: ждать flush для entry long"
            )

    return signals


async def _build_vol_section(db) -> str:
    """Build volatility section for BTC/ETH."""
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
        iv = r["iv_30d"]
        rv = r["rv_30d"]
        vrp = r["vrp"]
        vrp_z = r["vrp_zscore"]
        skew_z = r["skew_25d_zscore"]

        parts = [f"<b>{sym.replace('USDT', '')}:</b>"]
        if iv is not None:
            parts.append(f"IV: {iv:.0f}%")
        if rv is not None:
            parts.append(f"RV: {rv:.0f}%")
        if vrp is not None:
            parts.append(f"VRP: {vrp:+.0f}%")
        if vrp_z is not None:
            parts.append(f"VRP_z: {vrp_z:+.1f}{_z_flag(vrp_z)}")
        if skew_z is not None:
            parts.append(f"Skew_z: {skew_z:+.1f}{_z_flag(skew_z)}")

        lines.append("• " + " | ".join(parts))
        has_data = True

        # Vol compression warning
        if iv is not None and rv is not None and iv < 30 and rv < 30:
            lines.append("  ⚡ Vol compression — breakout imminent")

    if not has_data:
        return ""
    lines.append("")
    return "\n".join(lines)


async def _build_funding_arb_section() -> str:
    """Build funding arb section with top 3 spreads."""
    try:
        rates = await funding_service.fetch_all_rates()
    except Exception:
        return ""

    if not rates:
        return ""

    # Group by symbol, find max spread
    by_sym: dict[str, list[dict]] = {}
    for r in rates:
        sym = r["symbol"]
        settlement = r.get("settlement_hours", 8)
        rate = r["rate"]
        if settlement == 1:
            rate *= 8
        by_sym.setdefault(sym, []).append({"exchange": r["exchange"], "rate": rate})

    spreads = []
    for sym, exchanges in by_sym.items():
        if len(exchanges) < 2:
            continue
        sorted_ex = sorted(exchanges, key=lambda x: x["rate"])
        lowest = sorted_ex[0]
        highest = sorted_ex[-1]
        spread = highest["rate"] - lowest["rate"]
        if spread > 0.0001:
            apr = spread * 3 * 365 * 100
            spreads.append({
                "symbol": sym,
                "long_at": lowest["exchange"],
                "short_at": highest["exchange"],
                "spread": spread,
                "apr": apr,
            })

    spreads.sort(key=lambda x: x["spread"], reverse=True)
    top = spreads[:3]

    if not top:
        return ""

    lines = ["💰 <b>FUNDING ARB (top 3):</b>"]
    for s in top:
        sym = s["symbol"].replace("USDT", "")
        lines.append(
            f"• <b>{sym}</b>: Long@{s['long_at']} / Short@{s['short_at']} | "
            f"Spread: {s['spread'] * 100:.3f}% | APR: {s['apr']:.0f}%"
        )
    lines.append("")
    return "\n".join(lines)


async def _build_momentum_section(db) -> str:
    """Build momentum section from daily_momentum."""
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


def _build_watchlist(screener: list[dict]) -> list[str]:
    """Build watchlist of symbols to monitor tomorrow."""
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


# ── Real-time Composite Alerts ────────────────────────────────────────
# Only fire when MULTIPLE metrics align — no single-metric noise.
# Based on strategies from docs/metrics-guide.md

_prev_snapshot: dict[str, dict] = {}
_initialized = False


async def check_alerts() -> list[dict]:
    """Check composite alert conditions. Returns list of triggered alert dicts.
    Each alert requires 2+ metrics confirming the same thesis.
    """
    global _prev_snapshot, _initialized

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
        }

    # First run — populate without alerting
    if not _initialized:
        _prev_snapshot = current
        _initialized = True
        log.info("Alert snapshot initialized (no alerts on first run)")
        return []

    alerts = []

    for sym, cur in current.items():
        prev = _prev_snapshot.get(sym, {})
        short_sym = sym.replace("USDT", "")
        oi_z = cur["oi_z"]
        fund_z = cur["funding_z"]
        liq_z = cur["liq_z"]
        vol_z = cur["volume_z"]
        price_chg = cur["price_change_24h_pct"]
        oi_chg = cur["oi_change_24h_pct"]
        composite = (oi_z + fund_z + liq_z) / 3

        # ── 1. OVERHEAT: OI extreme + funding extreme same direction
        # metrics-guide §3 scenario 4 + §4: OI_z > +4 + Fund_z > +3 → cascade risk
        if oi_z > 4 and fund_z > 3:
            alerts.append({
                "key": f"overheat:{sym}",
                "title": f"🔴 {short_sym} ПЕРЕГРЕВ — OI + Funding extreme",
                "body": _format_alert_body(
                    what_we_see=[
                        f"OI_z: {oi_z:+.1f} | Fund_z: {fund_z:+.1f} | Liq_z: {liq_z:+.1f}",
                        f"OI: {_fmt_usd(cur['open_interest_usd'])} ({_fmt_pct(oi_chg)} 24h)",
                        f"Price: {_fmt_pct(price_chg)} 24h | Composite: {composite:+.1f}",
                    ],
                    indicators=[
                        "OI на экстремуме + лонги платят экстремальный фандинг",
                        "Каскадные ликвидации лонгов вероятны",
                        "Волна 5 по OI — дивергенция с ценой возможна",
                    ],
                    action=[
                        "НЕ открывать новые лонги",
                        "Готовить шорт на наклонной сверху",
                        "Ждать разгрузку OI (drop > 5%) для re-entry long",
                    ],
                ),
            })

        # ── 2. CAPITULATION: OI washed + funding negative + liq cascade
        # metrics-guide §3 scenario 1+3, §5: post-flush entry
        if oi_z < -3 and fund_z < -3:
            alerts.append({
                "key": f"capitulation:{sym}",
                "title": f"🟢 {short_sym} КАПИТУЛЯЦИЯ — вымытость + шорты платят",
                "body": _format_alert_body(
                    what_we_see=[
                        f"OI_z: {oi_z:+.1f} | Fund_z: {fund_z:+.1f} | Liq_z: {liq_z:+.1f}",
                        f"OI: {_fmt_usd(cur['open_interest_usd'])} ({_fmt_pct(oi_chg)} 24h)",
                        f"Price: {_fmt_pct(price_chg)} 24h",
                    ],
                    indicators=[
                        "OI вымыт + шорты платят фандинг = слабые руки вышли",
                        "Зона накопления — исторически high-probability long",
                        "Волна A/B завершается, ожидать reversal",
                    ],
                    action=[
                        "Искать лонг на зонах интереса / наклонных снизу",
                        "Подтверждение: OI начинает расти с текущих уровней",
                        "Stop под ближайший liq cluster",
                    ],
                ),
            })

        # ── 3. LIQUIDATION CASCADE + FLUSH
        # metrics-guide §5: liq_z > 2 + price drop + OI dropping = flush event
        if liq_z > 4 and price_chg < -6 and oi_chg < -4:
            alerts.append({
                "key": f"liq_flush:{sym}",
                "title": f"💥 {short_sym} LIQ FLUSH — каскад + OI сброс",
                "body": _format_alert_body(
                    what_we_see=[
                        f"Liq_z: {liq_z:+.1f} | Price: {_fmt_pct(price_chg)} | OI: {_fmt_pct(oi_chg)}",
                        f"OI_z: {oi_z:+.1f} | Fund_z: {fund_z:+.1f}",
                        f"OI: {_fmt_usd(cur['open_interest_usd'])}",
                    ],
                    indicators=[
                        "Каскадные ликвидации + слив OI = flush event",
                        "Слабые лонги ликвидированы, рынок очищается",
                        "После flush — bounce вероятен (mean reversion)",
                    ],
                    action=[
                        "НЕ шортить на лоях — flush уже произошёл",
                        "Ждать стабилизацию (1-2 свечи), затем лонг на зоне",
                        f"{'Funding уже отрицательный — подтверждает flush' if fund_z < 0 else 'Funding ещё положительный — flush может продолжиться'}",
                    ],
                ),
            })

        # ── 4. OI/PRICE DIVERGENCE (strong)
        # metrics-guide §3 scenario 2+3: significant OI vs price mismatch
        if oi_chg > 14 and price_chg < -4:
            # OI growing + price falling → short squeeze building
            alerts.append({
                "key": f"divergence_squeeze:{sym}",
                "title": f"⚡ {short_sym} ДИВЕРГЕНЦИЯ — OI↑ Price↓ (сквиз)",
                "body": _format_alert_body(
                    what_we_see=[
                        f"OI: {_fmt_pct(oi_chg)} 24h | Price: {_fmt_pct(price_chg)} 24h",
                        f"OI_z: {oi_z:+.1f} | Fund_z: {fund_z:+.1f} | Liq_z: {liq_z:+.1f}",
                    ],
                    indicators=[
                        "OI растёт при падении цены → агрессивное накопление шортов",
                        "Short squeeze вероятен при развороте",
                        f"{'Funding отрицательный — шорты перегружены' if fund_z < -0.5 else 'Funding нейтральный — давление шортов умеренное'}",
                    ],
                    action=[
                        "Готовить лонг на сквиз-уровнях (liq map clusters сверху)",
                        "Entry при первых признаках разворота + volume confirmation",
                    ],
                ),
            })
        elif oi_chg < -14 and price_chg > 6:
            # OI falling + price rising → short covering rally, top close
            alerts.append({
                "key": f"divergence_top:{sym}",
                "title": f"⚠️ {short_sym} ДИВЕРГЕНЦИЯ — OI↓ Price↑ (топ)",
                "body": _format_alert_body(
                    what_we_see=[
                        f"OI: {_fmt_pct(oi_chg)} 24h | Price: {_fmt_pct(price_chg)} 24h",
                        f"OI_z: {oi_z:+.1f} | Fund_z: {fund_z:+.1f}",
                    ],
                    indicators=[
                        "OI падает при росте цены → рост на закрытии шортов",
                        "Нет новых покупателей — топ близко",
                        "Волна 5 exhaustion pattern",
                    ],
                    action=[
                        "НЕ добавлять лонги на текущих уровнях",
                        "Искать шорт при касании наклонной сверху",
                    ],
                ),
            })

        # ── 5. VOLUME BREAKOUT + подтверждение
        # metrics-guide §6: vol_z > 2 + другой z > 1.5 = настоящий breakout
        if vol_z > 4 and (abs(oi_z) > 3 or abs(fund_z) > 3):
            direction = "LONG" if price_chg > 0 else "SHORT" if price_chg < 0 else "НЕОПР"
            alerts.append({
                "key": f"vol_breakout_confirmed:{sym}",
                "title": f"🔊 {short_sym} VOLUME BREAKOUT — подтверждён метриками",
                "body": _format_alert_body(
                    what_we_see=[
                        f"Vol_z: {vol_z:+.1f} | Price: {_fmt_pct(price_chg)} 24h",
                        f"OI_z: {oi_z:+.1f} | Fund_z: {fund_z:+.1f}",
                    ],
                    indicators=[
                        f"Volume extreme + {'OI' if abs(oi_z) > 3 else 'Funding'} подтверждает",
                        f"{'Breakout direction: {}'.format(direction)}",
                        f"{'Кульминация покупок — осторожно' if price_chg > 5 and fund_z > 1.5 else 'Breakout может продолжиться' if abs(price_chg) > 2 else 'Volume без цены — накопление/распределение'}",
                    ],
                    action=[
                        f"Торговать {'лонг' if price_chg > 0 else 'шорт' if price_chg < 0 else 'по направлению'} при пробое уровня",
                        "Проверить liq map для TP уровней",
                    ],
                ),
            })

    # ── 6. REGIME TRANSITION (market-wide)
    # metrics-guide §7: only extreme transitions matter
    top10 = sorted(
        current.items(),
        key=lambda x: x[1].get("open_interest_usd", 0),
        reverse=True,
    )[:10]
    top10_prev = sorted(
        _prev_snapshot.items(),
        key=lambda x: x[1].get("open_interest_usd", 0),
        reverse=True,
    )[:10]

    def _avg_composite(items):
        vals = [(d.get("oi_z", 0) + d.get("funding_z", 0) + d.get("liq_z", 0)) / 3
                for _, d in items if isinstance(d, dict)]
        return sum(vals) / len(vals) if vals else 0

    cur_composite = _avg_composite(top10)
    prev_composite = _avg_composite(top10_prev)
    cur_label, _ = _regime_label(cur_composite)
    prev_label, _ = _regime_label(prev_composite)

    if cur_label != prev_label:
        extreme_keywords = ("Deep Oversold", "Extreme")
        if any(kw in cur_label or kw in prev_label for kw in extreme_keywords):
            # Gather supporting data for the regime alert
            extreme_syms = []
            for sym_name, d in top10:
                c = (d.get("oi_z", 0) + d.get("funding_z", 0) + d.get("liq_z", 0)) / 3
                if abs(c) > 1.5:
                    extreme_syms.append(f"{sym_name.replace('USDT', '')} ({c:+.1f})")

            alerts.append({
                "key": "regime_transition",
                "title": f"🔄 REGIME SHIFT: {prev_label} → {cur_label}",
                "body": _format_alert_body(
                    what_we_see=[
                        f"Composite Z: {prev_composite:+.2f} → {cur_composite:+.2f}",
                        f"Extreme symbols: {', '.join(extreme_syms[:5]) or 'none'}",
                    ],
                    indicators=[
                        f"{'Рынок ПЕРЕГРЕТ — множество метрик в красной зоне' if cur_composite > 1.5 else 'Рынок ВЫМЫТ — множество метрик в зелёной зоне' if cur_composite < -1.5 else 'Переход между зонами'}",
                        f"{'Risk-off: сокращать экспозицию' if cur_composite > 1.5 else 'Risk-on: наращивать экспозицию' if cur_composite < -1.5 else 'Ждать подтверждения'}",
                    ],
                    action=[
                        f"{'Не открывать новые лонги, искать шорт-сетапы' if cur_composite > 1.5 else 'Искать лонг-сетапы на зонах интереса' if cur_composite < -1.5 else 'Наблюдать за развитием'}",
                        "Проверить Global Dashboard для полной картины",
                    ],
                ),
            })

    # ── 7. VOL REGIME SHIFT (BTC/ETH) — compression/expansion + VRP
    # metrics-guide §10-12: vol compression → breakout, VRP extreme + skew
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

            # Vol compression: IV & RV both < 30% = breakout imminent
            if iv > 0 and rv > 0 and iv < 30 and rv < 30:
                alerts.append({
                    "key": f"vol_compression:{sym}",
                    "title": f"🌊 {short_name} VOL COMPRESSION — IV {iv:.0f}% + RV {rv:.0f}%",
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
                    ),
                })

            # VRP extreme + skew alignment = panic or euphoria
            if abs(vrp_z) > 4 and abs(skew_z) > 2:
                if vrp_z > 4 and skew_z > 2:
                    # Panic: puts expensive + vol rich = panic hedging
                    alerts.append({
                        "key": f"vol_panic:{sym}",
                        "title": f"😱 {short_name} ПАНИКА — VRP_z {vrp_z:+.1f} + Skew_z {skew_z:+.1f}",
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
                        ),
                    })
                elif vrp_z < -4 and skew_z < -2:
                    # Euphoria: calls expensive + vol cheap = complacency
                    alerts.append({
                        "key": f"vol_euphoria:{sym}",
                        "title": f"🎪 {short_name} ЭЙФОРИЯ — VRP_z {vrp_z:+.1f} + Skew_z {skew_z:+.1f}",
                        "body": _format_alert_body(
                            what_we_see=[
                                f"IV: {iv:.0f}% | RV: {rv:.0f}% | VRP_z: {vrp_z:+.1f}",
                                f"Skew_z: {skew_z:+.1f} (коллы дорогие)",
                                f"OI_z: {sym_data.get('oi_z', 0):+.1f} | Fund_z: {sym_data.get('funding_z', 0):+.1f}",
                            ],
                            indicators=[
                                "Cheap Vol + коллы переоценены = рыночная эйфория",
                                "Buy vol: breakout/коррекция вероятны",
                            ],
                            action=[
                                "Искать шорт на наклонной сверху",
                                "Buy vol: покупать путы / straddles",
                            ],
                        ),
                    })
    except Exception as e:
        log.warning(f"Vol alert check error: {e}")

    # Update snapshot
    _prev_snapshot = current

    return alerts


def _format_alert_body(what_we_see: list[str], indicators: list[str], action: list[str]) -> str:
    """Format alert body with three sections."""
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

    return "\n".join(lines)


# ── Utilities ────────────────────────────────────────────────────────

def _split_message(text: str, limit: int = 4096) -> list[str]:
    """Split message at paragraph boundaries if over limit."""
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
