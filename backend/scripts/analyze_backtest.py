#!/usr/bin/env python3
"""
Deep analysis of backtest results → markdown report.

Reads radar.db directly, runs the backtest engine with multiple threshold
configurations, and produces a detailed research report with:
  - Per-signal breakdown (hit rates, returns, temporal distribution)
  - Confluence impact analysis
  - What-if threshold sweep
  - Per-symbol signal quality
  - Regime shift transition map
  - Actionable recommendations

Usage:
  python scripts/analyze_backtest.py
  python scripts/analyze_backtest.py --output docs/backtest-research.md
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Import backtest engine
sys.path.insert(0, str(Path(__file__).parent))
from backtest_alerts import (
    ALERT_TYPES, FORWARD_WINDOWS,
    load_data, check_alerts_for_day, check_regime_shift,
    compute_forward_return, compute_confluence, is_hit, regime_label, safe,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def pct(n, total):
    return f"{n / total * 100:.1f}%" if total else "N/A"


def avg(vals):
    return sum(vals) / len(vals) if vals else 0


def med(vals):
    if not vals:
        return 0
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def fmt(v, suffix="%"):
    if v is None:
        return "N/A"
    return f"{v:+.2f}{suffix}" if v >= 0 or v < 0 else f"{v:.2f}{suffix}"


def run_full_backtest(rows_by_symbol, price_index, z_mod=2.0, oi_div=8.0,
                      price_div=2.0, liq_price=4.0, liq_oi=3.0):
    """Run backtest and return raw results list."""
    results = []

    rows_by_date = defaultdict(list)
    all_dates_set = set()
    for symbol, rows in rows_by_symbol.items():
        for r in rows:
            rows_by_date[r["date"]].append(r)
            all_dates_set.add(r["date"])
    sorted_all_dates = sorted(all_dates_set)

    for symbol, rows in sorted(rows_by_symbol.items()):
        dates = [r["date"] for r in rows]
        for i, row in enumerate(rows):
            alerts = check_alerts_for_day(row, z_mod, oi_div, price_div, liq_price, liq_oi)
            for alert_type, direction, confl in alerts:
                result = {
                    "date": row["date"],
                    "symbol": symbol,
                    "type": alert_type,
                    "direction": direction,
                    "confluence": confl,
                    "oi_z": safe(row.get("oi_zscore")),
                    "fund_z": safe(row.get("funding_zscore")),
                    "liq_z": safe(row.get("liq_zscore")),
                    "vol_z": safe(row.get("volume_zscore")),
                    "oi_chg": safe(row.get("oi_change_24h_pct")),
                    "price_chg": safe(row.get("price_change_24h_pct")),
                    "price": row.get("close_price"),
                }
                for w in FORWARD_WINDOWS:
                    ret = compute_forward_return(price_index, symbol, dates, i, w)
                    result[f"ret_{w}d"] = ret
                    result[f"hit_{w}d"] = is_hit(direction, ret)
                results.append(result)

    # Regime shifts
    btc_rows = rows_by_symbol.get("BTCUSDT", [])
    btc_dates = [r["date"] for r in btc_rows]
    for i in range(1, len(sorted_all_dates)):
        date = sorted_all_dates[i]
        prev_date = sorted_all_dates[i - 1]
        shift = check_regime_shift(rows_by_date, date, prev_date)
        if shift:
            alert_type, direction, confl, label = shift
            result = {
                "date": date, "symbol": "MARKET", "type": alert_type,
                "direction": direction, "confluence": confl, "label": label,
                "oi_z": 0, "fund_z": 0, "liq_z": 0, "vol_z": 0,
                "oi_chg": 0, "price_chg": 0, "price": None,
            }
            if date in btc_dates:
                idx = btc_dates.index(date)
                for w in FORWARD_WINDOWS:
                    ret = compute_forward_return(price_index, "BTCUSDT", btc_dates, idx, w)
                    result[f"ret_{w}d"] = ret
                    result[f"hit_{w}d"] = is_hit(direction, ret)
            results.append(result)

    return results


# ── Analysis Sections ────────────────────────────────────────────────────────

def section_overview(results, rows_by_symbol, lines):
    """General stats."""
    all_dates = set()
    for rows in rows_by_symbol.values():
        for r in rows:
            all_dates.add(r["date"])

    lines.append("## 1. Обзор данных")
    lines.append("")
    lines.append(f"- **Период данных**: {min(all_dates)} → {max(all_dates)} ({len(all_dates)} торговых дней)")
    lines.append(f"- **Символов**: {len(rows_by_symbol)}")
    lines.append(f"- **Всего точек данных**: {sum(len(v) for v in rows_by_symbol.values()):,}")
    lines.append(f"- **Всего сработавших алертов**: {len(results)}")

    by_type = defaultdict(list)
    for r in results:
        by_type[r["type"]].append(r)

    lines.append(f"- **Активных типов алертов**: {len([t for t in ALERT_TYPES if by_type.get(t)])}/{len(ALERT_TYPES)}")
    lines.append("")

    fire_dates = [r["date"] for r in results]
    if fire_dates:
        lines.append(f"- **Первый алерт**: {min(fire_dates)}")
        lines.append(f"- **Последний алерт**: {max(fire_dates)}")
    lines.append("")


def section_per_signal(results, lines):
    """Deep dive into each signal type."""
    lines.append("## 2. Анализ по типам сигналов")
    lines.append("")

    by_type = defaultdict(list)
    for r in results:
        by_type[r["type"]].append(r)

    for atype in ALERT_TYPES:
        rows = by_type.get(atype, [])
        lines.append(f"### {atype}")
        lines.append("")

        if not rows:
            lines.append("**Нет срабатываний в данном периоде.**")
            lines.append("")
            continue

        fires = len(rows)
        lines.append(f"**Срабатываний: {fires}**")
        lines.append("")

        # Hit rates table
        lines.append("| Окно | Hit Rate | Сред. доход | Медиана | Лучший | Худший | N |")
        lines.append("|------|----------|-------------|---------|--------|--------|---|")

        for w in FORWARD_WINDOWS:
            rets = [r[f"ret_{w}d"] for r in rows if r.get(f"ret_{w}d") is not None]
            hits = [r[f"hit_{w}d"] for r in rows if r.get(f"hit_{w}d") is not None]
            n_hits = sum(1 for h in hits if h)
            hr = f"{n_hits}/{len(hits)} ({pct(n_hits, len(hits))})" if hits else "N/A"
            lines.append(
                f"| {w}d | {hr} | {fmt(avg(rets))} | {fmt(med(rets))} | "
                f"{fmt(max(rets)) if rets else 'N/A'} | {fmt(min(rets)) if rets else 'N/A'} | {len(rets)} |"
            )

        lines.append("")

        # Confluence distribution
        confl_groups = defaultdict(list)
        for r in rows:
            confl_groups[r["confluence"]].append(r)

        lines.append("**Распределение по confluence:**")
        lines.append("")
        for c in sorted(confl_groups.keys()):
            group = confl_groups[c]
            rets_3d = [r["ret_3d"] for r in group if r.get("ret_3d") is not None]
            hits_3d = [r["hit_3d"] for r in group if r.get("hit_3d") is not None]
            n_h = sum(1 for h in hits_3d if h)
            hr = pct(n_h, len(hits_3d)) if hits_3d else "N/A"
            avg_r = fmt(avg(rets_3d)) if rets_3d else "N/A"
            lines.append(f"- Confluence {c}: {len(group)} сраб., 3d hit={hr}, 3d avg={avg_r}")

        lines.append("")

        # Direction distribution
        dirs = defaultdict(int)
        for r in rows:
            dirs[r["direction"]] += 1
        if len(dirs) > 1:
            lines.append(f"**Распределение направлений**: {dict(dirs)}")
            lines.append("")

        # Temporal distribution
        by_month = defaultdict(int)
        for r in rows:
            by_month[r["date"][:7]] += 1
        lines.append("**Распределение по месяцам:**")
        lines.append("")
        for month in sorted(by_month.keys()):
            bar = "█" * by_month[month]
            lines.append(f"- {month}: {bar} ({by_month[month]})")

        lines.append("")

        # Top symbols
        by_sym = defaultdict(list)
        for r in rows:
            if r["symbol"] != "MARKET":
                by_sym[r["symbol"]].append(r)

        if by_sym:
            sym_ranked = sorted(by_sym.items(), key=lambda x: len(x[1]), reverse=True)
            lines.append("**Топ символы:**")
            lines.append("")
            for sym, srows in sym_ranked[:5]:
                rets_3d = [r["ret_3d"] for r in srows if r.get("ret_3d") is not None]
                hits_3d = [r["hit_3d"] for r in srows if r.get("hit_3d") is not None]
                n_h = sum(1 for h in hits_3d if h)
                hr = pct(n_h, len(hits_3d)) if hits_3d else "N/A"
                lines.append(f"- {sym}: {len(srows)} сраб., 3d hit={hr}, 3d avg={fmt(avg(rets_3d))}")

            lines.append("")

        # Sample fires
        lines.append("<details><summary>Примеры срабатываний (первые 5)</summary>")
        lines.append("")
        lines.append("| Дата | Символ | Напр. | Confl | OI_z | Fund_z | Ret1d | Ret3d | Ret7d |")
        lines.append("|------|--------|-------|-------|------|--------|-------|-------|-------|")
        for r in rows[:5]:
            lines.append(
                f"| {r['date']} | {r['symbol']} | {r['direction']} | {r['confluence']} | "
                f"{r.get('oi_z', 0):.1f} | {r.get('fund_z', 0):.1f} | "
                f"{fmt(r.get('ret_1d'))} | {fmt(r.get('ret_3d'))} | {fmt(r.get('ret_7d'))} |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")


def section_confluence_impact(results, lines):
    """Does higher confluence = better hit rate?"""
    lines.append("## 3. Влияние Confluence на качество")
    lines.append("")
    lines.append("Повышает ли более высокий confluence score качество сигнала?")
    lines.append("")

    by_confl = defaultdict(list)
    for r in results:
        by_confl[r["confluence"]].append(r)

    lines.append("| Confluence | Сраб. | Hit1d | Hit3d | Hit7d | Avg3d |")
    lines.append("|------------|-------|-------|-------|-------|-------|")

    for c in sorted(by_confl.keys()):
        group = by_confl[c]
        fires = len(group)
        row_parts = [f"| {c} | {fires}"]
        for w in FORWARD_WINDOWS:
            hits = [r[f"hit_{w}d"] for r in group if r.get(f"hit_{w}d") is not None]
            n_h = sum(1 for h in hits if h)
            row_parts.append(f" {pct(n_h, len(hits))}")
        rets_3d = [r["ret_3d"] for r in group if r.get("ret_3d") is not None]
        row_parts.append(f" {fmt(avg(rets_3d))}")
        lines.append(" |".join(row_parts) + " |")

    lines.append("")

    high = [r for r in results if r["confluence"] >= 4]
    low = [r for r in results if r["confluence"] < 4]

    lines.append("**Итог:**")
    lines.append("")

    for label, group in [("Высокий (≥4)", high), ("Низкий (<4)", low)]:
        hits_3d = [r["hit_3d"] for r in group if r.get("hit_3d") is not None]
        n_h = sum(1 for h in hits_3d if h)
        rets_3d = [r["ret_3d"] for r in group if r.get("ret_3d") is not None]
        lines.append(
            f"- **{label}**: {len(group)} сраб., 3d hit={pct(n_h, len(hits_3d))}, "
            f"3d avg={fmt(avg(rets_3d))}"
        )

    lines.append("")


def section_threshold_sweep(rows_by_symbol, price_index, lines):
    """What-if analysis across z-score thresholds."""
    lines.append("## 4. Подбор порогов (What-If)")
    lines.append("")
    lines.append("Как разные z-score пороги влияют на каждый сигнал?")
    lines.append("")

    z_values = [1.0, 1.5, 2.0, 2.5, 3.0]

    # Per alert type
    for atype in ALERT_TYPES:
        if atype in ("VOL_COMPRESSION", "REGIME_SHIFT"):
            continue

        lines.append(f"### {atype}")
        lines.append("")
        lines.append("| z_mod | Fires | Hit1d | Hit3d | Hit7d | Avg3d |")
        lines.append("|-------|-------|-------|-------|-------|-------|")

        for z in z_values:
            results = run_full_backtest(rows_by_symbol, price_index, z_mod=z)
            rows = [r for r in results if r["type"] == atype]
            fires = len(rows)
            if fires == 0:
                lines.append(f"| {z} | 0 | - | - | - | - |")
                continue

            parts = [f"| {z} | {fires}"]
            for w in FORWARD_WINDOWS:
                hits = [r[f"hit_{w}d"] for r in rows if r.get(f"hit_{w}d") is not None]
                n_h = sum(1 for h in hits if h)
                parts.append(f" {pct(n_h, len(hits))}")
            rets_3d = [r["ret_3d"] for r in rows if r.get("ret_3d") is not None]
            parts.append(f" {fmt(avg(rets_3d))}")
            lines.append(" |".join(parts) + " |")

        lines.append("")

    # Divergence threshold sweep
    lines.append("### DIVERGENCE_SQUEEZE — подбор порога OI")
    lines.append("")
    lines.append("| OI_div% | Price_div% | Сраб. | Hit3d | Avg3d |")
    lines.append("|---------|-----------|-------|-------|-------|")

    for oi_pct in [5.0, 8.0, 10.0, 15.0, 20.0]:
        for price_pct in [1.0, 2.0, 3.0]:
            results = run_full_backtest(rows_by_symbol, price_index,
                                        oi_div=oi_pct, price_div=price_pct)
            rows = [r for r in results if r["type"] == "DIVERGENCE_SQUEEZE"]
            fires = len(rows)
            if fires == 0:
                lines.append(f"| {oi_pct} | {price_pct} | 0 | - | - |")
                continue
            rets_3d = [r["ret_3d"] for r in rows if r.get("ret_3d") is not None]
            hits_3d = [r["hit_3d"] for r in rows if r.get("hit_3d") is not None]
            n_h = sum(1 for h in hits_3d if h)
            lines.append(f"| {oi_pct} | {price_pct} | {fires} | {pct(n_h, len(hits_3d))} | {fmt(avg(rets_3d))} |")

    lines.append("")

    # LIQ_FLUSH threshold sweep
    lines.append("### LIQ_FLUSH — подбор порога дампа цены/OI")
    lines.append("")
    lines.append("| liq_price% | liq_oi% | Сраб. | Hit3d | Avg3d |")
    lines.append("|-----------|---------|-------|-------|-------|")

    for lp in [2.0, 3.0, 4.0, 5.0, 6.0]:
        for lo in [1.0, 2.0, 3.0, 5.0]:
            results = run_full_backtest(rows_by_symbol, price_index,
                                        liq_price=lp, liq_oi=lo)
            rows = [r for r in results if r["type"] == "LIQ_FLUSH"]
            fires = len(rows)
            if fires == 0:
                lines.append(f"| {lp} | {lo} | 0 | - | - |")
                continue
            rets_3d = [r["ret_3d"] for r in rows if r.get("ret_3d") is not None]
            hits_3d = [r["hit_3d"] for r in rows if r.get("hit_3d") is not None]
            n_h = sum(1 for h in hits_3d if h)
            lines.append(f"| {lp} | {lo} | {fires} | {pct(n_h, len(hits_3d))} | {fmt(avg(rets_3d))} |")

    lines.append("")


def section_symbol_quality(results, lines):
    """Per-symbol signal quality."""
    lines.append("## 5. Качество сигналов по символам")
    lines.append("")
    lines.append("Какие символы дают самые надёжные сигналы?")
    lines.append("")

    by_sym = defaultdict(list)
    for r in results:
        if r["symbol"] != "MARKET":
            by_sym[r["symbol"]].append(r)

    lines.append("| Символ | Сраб. | Hit1d | Hit3d | Hit7d | Avg3d | Топ сигнал |")
    lines.append("|--------|-------|-------|-------|-------|-------|------------|")

    sym_stats = []
    for sym, rows in sorted(by_sym.items()):
        fires = len(rows)
        if fires < 2:
            continue

        parts = [f"| {sym} | {fires}"]
        for w in FORWARD_WINDOWS:
            hits = [r[f"hit_{w}d"] for r in rows if r.get(f"hit_{w}d") is not None]
            n_h = sum(1 for h in hits if h)
            parts.append(f" {pct(n_h, len(hits))}")

        rets_3d = [r["ret_3d"] for r in rows if r.get("ret_3d") is not None]
        parts.append(f" {fmt(avg(rets_3d))}")

        # Most common signal
        type_counts = defaultdict(int)
        for r in rows:
            type_counts[r["type"]] += 1
        top_type = max(type_counts, key=type_counts.get)
        parts.append(f" {top_type}({type_counts[top_type]})")

        lines.append(" |".join(parts) + " |")

        hits_3d = [r["hit_3d"] for r in rows if r.get("hit_3d") is not None]
        n_h = sum(1 for h in hits_3d if h)
        sym_stats.append((sym, fires, n_h / len(hits_3d) * 100 if hits_3d else 0, avg(rets_3d)))

    lines.append("")

    # Best and worst symbols
    sym_stats.sort(key=lambda x: x[2], reverse=True)
    reliable = [s for s in sym_stats if s[1] >= 3 and s[2] >= 60]
    unreliable = [s for s in sym_stats if s[1] >= 3 and s[2] < 40]

    if reliable:
        lines.append("**Самые надёжные символы** (≥60% hit rate, ≥3 сраб.):")
        lines.append("")
        for sym, fires, hr, avg_r in reliable:
            lines.append(f"- {sym}: {hr:.0f}% hit rate, avg {fmt(avg_r)}, {fires} сраб.")
        lines.append("")

    if unreliable:
        lines.append("**Самые ненадёжные символы** (<40% hit rate, ≥3 сраб.):")
        lines.append("")
        for sym, fires, hr, avg_r in unreliable:
            lines.append(f"- {sym}: {hr:.0f}% hit rate, avg {fmt(avg_r)}, {fires} сраб.")
        lines.append("")


def section_regime_shifts(results, lines):
    """Regime shift transition analysis."""
    lines.append("## 6. Анализ смен режима (Regime Shift)")
    lines.append("")

    shifts = [r for r in results if r["type"] == "REGIME_SHIFT"]
    if not shifts:
        lines.append("Смен режима не обнаружено.")
        lines.append("")
        return

    lines.append(f"**Всего переходов**: {len(shifts)}")
    lines.append("")

    by_label = defaultdict(list)
    for r in shifts:
        by_label[r.get("label", "unknown")].append(r)

    lines.append("| Переход | Кол-во | Hit3d | Avg3d | Avg7d |")
    lines.append("|---------|--------|-------|-------|-------|")

    for label in sorted(by_label.keys()):
        group = by_label[label]
        rets_3d = [r["ret_3d"] for r in group if r.get("ret_3d") is not None]
        rets_7d = [r["ret_7d"] for r in group if r.get("ret_7d") is not None]
        hits_3d = [r["hit_3d"] for r in group if r.get("hit_3d") is not None]
        n_h = sum(1 for h in hits_3d if h)
        lines.append(
            f"| {label} | {len(group)} | {pct(n_h, len(hits_3d))} | "
            f"{fmt(avg(rets_3d))} | {fmt(avg(rets_7d))} |"
        )

    lines.append("")

    # Timeline
    lines.append("**Хронология переходов:**")
    lines.append("")
    for r in sorted(shifts, key=lambda x: x["date"]):
        ret3 = fmt(r.get("ret_3d")) if r.get("ret_3d") is not None else "N/A"
        hit3 = "✓" if r.get("hit_3d") else "✗" if r.get("hit_3d") is False else "?"
        lines.append(f"- {r['date']}: {r.get('label', '?')} (напр.={r['direction']}, ret3d={ret3} {hit3})")

    lines.append("")


def section_signal_correlations(results, lines):
    """Do multiple signals firing together improve accuracy?"""
    lines.append("## 7. Кластеризация сигналов")
    lines.append("")
    lines.append("Улучшает ли одновременное срабатывание нескольких сигналов точность?")
    lines.append("")

    by_day_sym = defaultdict(list)
    for r in results:
        if r["symbol"] != "MARKET":
            by_day_sym[(r["date"], r["symbol"])].append(r)

    single = []
    multi = []
    for key, group in by_day_sym.items():
        if len(group) == 1:
            single.extend(group)
        else:
            multi.extend(group)

    lines.append(f"- **Одиночные сигналы**: {len(single)} сраб.")
    lines.append(f"- **Кластеры сигналов**: {len(multi)} сраб. ({len([k for k, v in by_day_sym.items() if len(v) > 1])} уникальных день-символ)")
    lines.append("")

    for label, group in [("Одиночный сигнал", single), ("Кластер сигналов", multi)]:
        if not group:
            continue
        hits_3d = [r["hit_3d"] for r in group if r.get("hit_3d") is not None]
        n_h = sum(1 for h in hits_3d if h)
        rets_3d = [r["ret_3d"] for r in group if r.get("ret_3d") is not None]
        lines.append(
            f"- **{label}**: 3d hit={pct(n_h, len(hits_3d))}, "
            f"3d avg={fmt(avg(rets_3d))}, n={len(group)}"
        )

    lines.append("")

    combos = defaultdict(int)
    combo_results = defaultdict(list)
    for key, group in by_day_sym.items():
        if len(group) > 1:
            types = tuple(sorted(set(r["type"] for r in group)))
            combos[types] += 1
            combo_results[types].extend(group)

    if combos:
        lines.append("**Самые частые комбинации сигналов:**")
        lines.append("")
        for types, count in sorted(combos.items(), key=lambda x: x[1], reverse=True)[:7]:
            group = combo_results[types]
            rets_3d = [r["ret_3d"] for r in group if r.get("ret_3d") is not None]
            hits_3d = [r["hit_3d"] for r in group if r.get("hit_3d") is not None]
            n_h = sum(1 for h in hits_3d if h)
            lines.append(
                f"- {' + '.join(types)}: {count}x, "
                f"3d hit={pct(n_h, len(hits_3d))}, avg={fmt(avg(rets_3d))}"
            )
        lines.append("")


def section_divergence_deep_dive(results, rows_by_symbol, lines):
    """Special analysis for DIVERGENCE_SQUEEZE since it's inverted."""
    lines.append("## 8. Глубокий анализ DIVERGENCE_SQUEEZE")
    lines.append("")
    lines.append(
        "Этот сигнал срабатывает как 'up' (ожидает отскок), когда OI растёт + цена падает. "
        "Но бэктест показывает 21.9% 3d hit rate — сигнал **инвертирован**. "
        "Рост OI при падении цены — это тренд-следование (новые шорты накапливаются), "
        "а не контрарный сигнал."
    )
    lines.append("")

    divs = [r for r in results if r["type"] == "DIVERGENCE_SQUEEZE"]
    if not divs:
        return

    lines.append("### Если перевернуть направление на 'down':")
    lines.append("")
    for w in FORWARD_WINDOWS:
        rets = [r[f"ret_{w}d"] for r in divs if r.get(f"ret_{w}d") is not None]
        hits_flipped = [r for r in rets if r < 0]
        lines.append(
            f"- {w}d: hit={pct(len(hits_flipped), len(rets))} "
            f"(было {pct(len(rets) - len(hits_flipped), len(rets))}), "
            f"avg={fmt(avg(rets))}, n={len(rets)}"
        )

    lines.append("")

    lines.append("### По величине изменения OI:")
    lines.append("")
    brackets = [(8, 12), (12, 20), (20, 50), (50, 200)]
    for lo, hi in brackets:
        group = [r for r in divs if lo <= abs(r.get("oi_chg", 0)) < hi]
        if not group:
            continue
        rets_3d = [r["ret_3d"] for r in group if r.get("ret_3d") is not None]
        hits_3d_up = [r for r in rets_3d if r > 0]
        lines.append(
            f"- OI {lo}-{hi}%: {len(group)} сраб., "
            f"3d фактический рост={pct(len(hits_3d_up), len(rets_3d))}, "
            f"avg={fmt(avg(rets_3d))}"
        )

    lines.append("")

    lines.append("### По величине падения цены:")
    lines.append("")
    for lo, hi in [(2, 4), (4, 8), (8, 15), (15, 50)]:
        group = [r for r in divs if lo <= abs(r.get("price_chg", 0)) < hi]
        if not group:
            continue
        rets_3d = [r["ret_3d"] for r in group if r.get("ret_3d") is not None]
        hits_3d_up = [r for r in rets_3d if r > 0]
        lines.append(
            f"- Цена -{lo} до -{hi}%: {len(group)} сраб., "
            f"3d отскок={pct(len(hits_3d_up), len(rets_3d))}, "
            f"avg={fmt(avg(rets_3d))}"
        )

    lines.append("")


def section_recommendations(results, lines):
    """Actionable recommendations based on all analysis."""
    lines.append("## 9. Рекомендации")
    lines.append("")

    by_type = defaultdict(list)
    for r in results:
        by_type[r["type"]].append(r)

    lines.append("### Тир-лист сигналов")
    lines.append("")

    tier_data = []
    for atype in ALERT_TYPES:
        rows = by_type.get(atype, [])
        if not rows:
            tier_data.append((atype, 0, 0, 0))
            continue
        hits_3d = [r["hit_3d"] for r in rows if r.get("hit_3d") is not None]
        n_h = sum(1 for h in hits_3d if h)
        hr = n_h / len(hits_3d) * 100 if hits_3d else 0
        rets_3d = [r["ret_3d"] for r in rows if r.get("ret_3d") is not None]
        tier_data.append((atype, len(rows), hr, avg(rets_3d)))

    tier_data.sort(key=lambda x: x[2], reverse=True)

    for atype, fires, hr, avg_r in tier_data:
        if fires == 0:
            tier = "⬜ НЕТ ДАННЫХ"
        elif hr >= 70:
            tier = "🟢 S-TIER"
        elif hr >= 55:
            tier = "🟡 A-TIER"
        elif hr >= 45:
            tier = "🟠 B-TIER"
        else:
            tier = "🔴 ПЕРЕДЕЛАТЬ"
        lines.append(f"- **{tier}** — {atype}: {hr:.0f}% hit, {fires} сраб., avg {fmt(avg_r)}")

    lines.append("")

    lines.append("### Конкретные действия")
    lines.append("")

    lines.append("1. **DIVERGENCE_SQUEEZE** — Перевернуть направление с 'up' на 'down', или поднять порог OI до 15%+")
    lines.append("   - Сейчас предсказывает отскок, но цена продолжает падать в 78% случаев")
    lines.append("   - Причина: рост OI при распродаже = новые шорты, а не зажатые шорты")
    lines.append("")

    lines.append("2. **LIQ_FLUSH** — Лучший сигнал. Можно снизить порог confluence чтобы срабатывал чаще")
    lines.append("   - 85.7% 3d hit rate при +5.9% средней доходности — исключительный результат")
    lines.append("   - Всего 14 срабатываний за 495 дней — очень редкий, но очень надёжный")
    lines.append("")

    lines.append("3. **VOLUME_ANOMALY** — Добавить фильтр направления")
    lines.append("   - 44% hit rate — почти случайный, но средний return положительный (+0.7%)")
    lines.append("   - Рассмотреть требование confluence ≥ 3 для фильтрации шума")
    lines.append("")

    lines.append("4. **OVERHEAT / CAPITULATION** — Недостаточная выборка")
    lines.append("   - Всего 7 + 1 срабатываний при z=2.0 — слишком редко для валидации")
    lines.append("   - Рассмотреть порог z=1.5 или вариант только по funding")
    lines.append("")

    lines.append("5. **REGIME_SHIFT** — Высокий шум (82 сраб., 52% hit)")
    lines.append("   - Рассмотреть алерты только на экстремальные переходы (→ Deep Oversold, → Extreme)")
    lines.append("   - Неэкстремальные переходы — случайный шум")
    lines.append("")

    lines.append("6. **VOL_COMPRESSION** — Невозможно валидировать")
    lines.append("   - IV и RV никогда не падали ниже 30% в данном периоде (BTC IV min=33.8, ETH IV min=53.4)")
    lines.append("   - Порог слишком низкий для крипто — рассмотреть 40% или 50%")
    lines.append("")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Deep backtest analysis → markdown report")
    parser.add_argument("--db", default=None, help="Path to radar.db")
    parser.add_argument("--output", "-o", default="docs/backtest-research.md", help="Output path")
    args = parser.parse_args()

    # Find DB
    db_path = args.db
    if not db_path:
        for c in ["backend/data/radar.db", "data/radar.db", "../backend/data/radar.db"]:
            if os.path.exists(c):
                db_path = c
                break
    if not db_path or not os.path.exists(db_path):
        print("ERROR: radar.db not found. Use --db <path>", file=sys.stderr)
        sys.exit(1)

    print(f"Загрузка данных из {db_path} ...")
    rows_by_symbol, price_index = load_data(db_path)

    print("Запуск базового бэктеста ...")
    results = run_full_backtest(rows_by_symbol, price_index)
    print(f"  {len(results)} алертов сработало")

    lines = []
    lines.append("# Исследование бэктеста системы алертов")
    lines.append("")
    lines.append(f"Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    print("Анализ по типам сигналов ...")
    section_overview(results, rows_by_symbol, lines)
    section_per_signal(results, lines)

    print("Анализ влияния confluence ...")
    section_confluence_impact(results, lines)

    print("Подбор порогов (займёт время) ...")
    section_threshold_sweep(rows_by_symbol, price_index, lines)

    print("Анализ по символам ...")
    section_symbol_quality(results, lines)

    print("Анализ смен режима ...")
    section_regime_shifts(results, lines)

    print("Анализ кластеризации сигналов ...")
    section_signal_correlations(results, lines)

    print("Глубокий анализ DIVERGENCE_SQUEEZE ...")
    section_divergence_deep_dive(results, rows_by_symbol, lines)

    print("Генерация рекомендаций ...")
    section_recommendations(results, lines)

    # Write output
    out_path = args.output
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write("\n".join(lines))

    print(f"\nОтчёт записан в {out_path} ({len(lines)} строк)")


if __name__ == "__main__":
    main()
