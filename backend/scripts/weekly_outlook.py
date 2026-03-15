"""Weekly Outlook — Sunday 13:00 MSK market report to Telegram.

Run: cd backend && python3 scripts/weekly_outlook.py
Cron: 0 10 * * 0 (10:00 UTC = 13:00 MSK)
"""

import asyncio
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings

DB_PATH = Path(__file__).parent.parent / "data" / "radar.db"

REGIME_LABELS = [
    (-2.0, "🟢 Deep Oversold"),
    (-1.0, "🔵 Oversold"),
    (0.0, "🟡 Neutral Cool"),
    (1.0, "🟠 Neutral Hot"),
    (2.0, "🟠 Overbought"),
    (999, "🔴 Extreme"),
]

TOP_OI = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
          "DOGEUSDT", "TRXUSDT", "UNIUSDT", "SUIUSDT", "ADAUSDT")


def regime_label(z: float) -> str:
    for threshold, label in REGIME_LABELS:
        if z <= threshold:
            return label
    return REGIME_LABELS[-1][1]


def query(db: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    cur = db.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_report(db: sqlite3.Connection) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    # Latest date in DB
    latest = query(db, "SELECT MAX(date) as d FROM derivatives_zscores")[0]["d"]

    # --- Composite regime (latest) ---
    top10_ph = ",".join(f"'{s}'" for s in TOP_OI)
    regime_rows = query(db, f"""
        SELECT ROUND(AVG((oi_zscore + funding_zscore + liq_zscore) / 3.0), 2) as cz
        FROM derivatives_zscores
        WHERE symbol IN ({top10_ph}) AND date = ?
    """, (latest,))
    cz = regime_rows[0]["cz"] if regime_rows else 0
    regime = regime_label(cz)

    # --- Composite 7d trend ---
    regime_7d = query(db, f"""
        SELECT date, ROUND(AVG((oi_zscore + funding_zscore + liq_zscore) / 3.0), 2) as cz
        FROM derivatives_zscores
        WHERE symbol IN ({top10_ph}) AND date >= ?
        GROUP BY date ORDER BY date
    """, (week_ago,))
    cz_start = regime_7d[0]["cz"] if regime_7d else cz
    cz_delta = cz - cz_start

    # --- BTC/ETH snapshot ---
    btc = query(db, """
        SELECT d.close_price, z.oi_zscore, z.funding_zscore, z.liq_zscore,
               z.oi_change_24h_pct, z.price_change_24h_pct
        FROM daily_derivatives d
        JOIN derivatives_zscores z ON d.symbol = z.symbol AND d.date = z.date
        WHERE d.symbol = 'BTCUSDT' AND d.date = ?
    """, (latest,))
    eth = query(db, """
        SELECT d.close_price, z.oi_zscore, z.funding_zscore, z.liq_zscore
        FROM daily_derivatives d
        JOIN derivatives_zscores z ON d.symbol = z.symbol AND d.date = z.date
        WHERE d.symbol = 'ETHUSDT' AND d.date = ?
    """, (latest,))

    # BTC 7d price change
    btc_7d = query(db, """
        SELECT close_price FROM daily_derivatives
        WHERE symbol = 'BTCUSDT' AND date >= ? ORDER BY date LIMIT 1
    """, (week_ago,))
    btc_price = btc[0]["close_price"] if btc else 0
    btc_7d_chg = ((btc_price / btc_7d[0]["close_price"] - 1) * 100) if btc_7d and btc_7d[0]["close_price"] else 0

    eth_price = eth[0]["close_price"] if eth else 0
    eth_7d = query(db, """
        SELECT close_price FROM daily_derivatives
        WHERE symbol = 'ETHUSDT' AND date >= ? ORDER BY date LIMIT 1
    """, (week_ago,))
    eth_7d_chg = ((eth_price / eth_7d[0]["close_price"] - 1) * 100) if eth_7d and eth_7d[0]["close_price"] else 0

    # --- IV/VRP (BTC/ETH) ---
    vol_data = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        rows = query(db, """
            SELECT iv_30d, rv_30d, vrp, vrp_zscore, skew_25d
            FROM daily_volatility
            WHERE symbol = ? AND iv_30d IS NOT NULL
            ORDER BY date DESC LIMIT 1
        """, (sym,))
        if rows:
            vol_data[sym] = rows[0]

    # --- Funding extremes ---
    fund_rows = query(db, """
        SELECT d.symbol, d.funding_rate, z.funding_zscore
        FROM daily_derivatives d
        JOIN derivatives_zscores z ON d.symbol = z.symbol AND d.date = z.date
        WHERE d.date = ?
        ORDER BY z.funding_zscore ASC LIMIT 5
    """, (latest,))
    fund_pos = query(db, """
        SELECT d.symbol, z.funding_zscore
        FROM daily_derivatives d
        JOIN derivatives_zscores z ON d.symbol = z.symbol AND d.date = z.date
        WHERE d.date = ? AND z.funding_zscore > 1.5
        ORDER BY z.funding_zscore DESC LIMIT 5
    """, (latest,))

    # --- OI 7d changes ---
    oi_changes = query(db, """
        SELECT a.symbol,
               ROUND(a.open_interest_usd/1e6, 1) as oi_m,
               ROUND((a.open_interest_usd - b.open_interest_usd) / NULLIF(b.open_interest_usd,0) * 100, 1) as chg
        FROM daily_derivatives a
        JOIN daily_derivatives b ON a.symbol = b.symbol
        WHERE a.date = ? AND b.date = ?
        ORDER BY chg DESC
    """, (latest, week_ago))
    oi_top = [r for r in oi_changes if r["chg"] and r["chg"] > 2][:5]
    oi_bot = [r for r in oi_changes if r["chg"] and r["chg"] < -2][-5:]

    # --- Heat map ---
    heat = query(db, """
        SELECT symbol,
               ROUND((oi_percentile + funding_percentile + liq_percentile + volume_percentile) / 4, 0) as heat
        FROM derivatives_zscores
        WHERE date = ?
        ORDER BY heat DESC LIMIT 5
    """, (latest,))

    # --- Momentum ---
    mom_top = query(db, """
        SELECT symbol, ROUND(momentum_value, 0) as mom
        FROM daily_momentum WHERE date = (SELECT MAX(date) FROM daily_momentum)
        ORDER BY momentum_value DESC LIMIT 5
    """)
    mom_bot = query(db, """
        SELECT symbol, ROUND(momentum_value, 0) as mom
        FROM daily_momentum WHERE date = (SELECT MAX(date) FROM daily_momentum)
        ORDER BY momentum_value ASC LIMIT 5
    """)

    # --- Signals this week ---
    signals = query(db, """
        SELECT alert_type, expected_direction, COUNT(*) as cnt,
               ROUND(AVG(confluence), 1) as avg_conf
        FROM alert_tracking
        WHERE fired_at >= ?
        GROUP BY alert_type, expected_direction
        ORDER BY cnt DESC
    """, (week_ago,))
    total_signals = sum(s["cnt"] for s in signals)

    # --- Trading this week ---
    trades = query(db, """
        SELECT COUNT(*) as n,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
               ROUND(AVG(pnl_pct), 2) as avg_pnl,
               ROUND(SUM(pnl_usd), 2) as total_pnl
        FROM trades
        WHERE closed_at >= ? AND status = 'closed'
    """, (week_ago,))
    t = trades[0] if trades else {}

    # --- Open positions ---
    open_pos = query(db, """
        SELECT symbol, direction, ROUND(pnl_pct, 2) as pnl, signal_type
        FROM trades WHERE status = 'open'
    """)

    # ========= BUILD MESSAGE =========
    SEP = "─────────────────────────"
    now_msk = datetime.now(timezone(timedelta(hours=3)))
    next_week = now_msk + timedelta(days=7)
    header = f"{now_msk.strftime('%d %b')} — {next_week.strftime('%d %b %Y')}"

    # --- helpers for human-readable z-score labels ---
    def _z_label(z: float) -> str:
        az = abs(z)
        if az < 1:
            return "норма"
        elif az < 2:
            return "повышен" if z > 0 else "понижен"
        else:
            return "экстремально высокий" if z > 0 else "экстремально низкий"

    def _fund_label(z: float) -> str:
        if z < -2:
            return "шорты платят"
        elif z < -1:
            return "уклон шорт"
        elif z > 2:
            return "лонги платят"
        elif z > 1:
            return "уклон лонг"
        return "нейтральный"

    def _vrp_label(vrp: float) -> str:
        if vrp > 5:
            return "опционы дорогие, страх завышен"
        elif vrp < -5:
            return "опционы дёшево, рынок недооценивает риск"
        return "норма"

    p = []

    # --- 1. Header + one-liner summary ---
    p.append(f"📊 <b>WEEKLY OUTLOOK | {header}</b>")
    trend = "↑" if cz_delta > 0.3 else "↓" if cz_delta < -0.3 else "→"

    # Build one-liner
    btc_dir = "рост" if btc_7d_chg > 1 else "падение" if btc_7d_chg < -1 else "боковик"
    # Count funding skew
    all_fund = query(db, """
        SELECT z.funding_zscore FROM derivatives_zscores z WHERE z.date = ?
        AND z.funding_zscore IS NOT NULL
    """, (latest,))
    neg_fund_cnt = sum(1 for r in all_fund if r["funding_zscore"] < -2)
    pos_fund_cnt = sum(1 for r in all_fund if r["funding_zscore"] > 2)

    if neg_fund_cnt > pos_fund_cnt and neg_fund_cnt >= 3:
        fund_summary = "шорты набраны"
    elif pos_fund_cnt > neg_fund_cnt and pos_fund_cnt >= 3:
        fund_summary = "лонги перегружены"
    else:
        fund_summary = "фандинг нейтральный"

    p.append(f"\n<i>Неделя: {btc_dir} BTC {btc_7d_chg:+.1f}%, {fund_summary}.</i>")

    # --- 2. Regime ---
    p.append(f"\n{SEP}")
    p.append(f"🌡 <b>{regime}</b> (z: {cz:+.1f} {trend})")
    # regime interpretation
    if cz > 2:
        p.append("Экстремальный перегрев. Каскадные ликвидации вероятны.")
    elif cz > 1:
        p.append("Рынок разогрет. Не лучшее время для новых лонгов.")
    elif cz > 0:
        p.append("Нейтральная зона с уклоном в тепло. Тренд развивается.")
    elif cz > -1:
        p.append("Нейтральная зона с уклоном в холод. Давление снято.")
    elif cz > -2:
        p.append("Перепроданность. Начало восстановления.")
    else:
        p.append("Рынок вымыт. Слабые руки вышли, ищем лонг.")

    # --- 3. BTC / ETH — interpretations, not raw z-scores ---
    p.append(f"\n{SEP}")
    b = btc[0] if btc else {}
    b_oi_z = b.get('oi_zscore', 0)
    b_fund_z = b.get('funding_zscore', 0)
    p.append(f"<b>BTC ${btc_price:,.0f}</b> ({btc_7d_chg:+.1f}% 7д)")
    p.append(f"  OI {_z_label(b_oi_z)} | {_fund_label(b_fund_z)}")
    if "BTCUSDT" in vol_data:
        v = vol_data["BTCUSDT"]
        p.append(f"  IV {v['iv_30d']:.0f}% vs RV {v['rv_30d']:.0f}% → {_vrp_label(v['vrp'])}")

    e = eth[0] if eth else {}
    e_oi_z = e.get('oi_zscore', 0)
    e_fund_z = e.get('funding_zscore', 0)
    p.append(f"\n<b>ETH ${eth_price:,.0f}</b> ({eth_7d_chg:+.1f}% 7д)")
    p.append(f"  OI {_z_label(e_oi_z)} | {_fund_label(e_fund_z)}")
    if "ETHUSDT" in vol_data:
        v = vol_data["ETHUSDT"]
        p.append(f"  IV {v['iv_30d']:.0f}% vs RV {v['rv_30d']:.0f}% → {_vrp_label(v['vrp'])}")

    # --- 4. Funding — top 3 + interpretation ---
    p.append(f"\n{SEP}")
    p.append("⚡ <b>Funding</b>")
    if fund_rows:
        neg = [f for f in fund_rows if f["funding_zscore"] and f["funding_zscore"] < -1.5][:3]
        if neg:
            items = ", ".join(f"{r['symbol'].replace('USDT','')} {r['funding_zscore']:+.1f}" for r in neg)
            p.append(f"  Шорты платят: {items}")
    if fund_pos:
        items = ", ".join(f"{r['symbol'].replace('USDT','')} {r['funding_zscore']:+.1f}" for r in (fund_pos or [])[:3])
        p.append(f"  Лонги платят: {items}")
    # interpretation
    if neg_fund_cnt >= 5:
        p.append(f"  → {neg_fund_cnt} из {len(all_fund)} с негативным фандингом — массовый шорт, сквиз вероятен")
    elif pos_fund_cnt >= 5:
        p.append(f"  → {pos_fund_cnt} из {len(all_fund)} с позитивным фандингом — рынок перегрет")
    elif neg_fund_cnt >= 3 or pos_fund_cnt >= 3:
        p.append(f"  → Умеренный перекос ({neg_fund_cnt} шорт / {pos_fund_cnt} лонг)")

    # --- 5. Потоки + Моментум — объединённый блок ---
    p.append(f"\n{SEP}")
    p.append("📊 <b>Деньги и моментум</b>")
    if oi_top:
        items = ", ".join(f"{r['symbol'].replace('USDT','')} {r['chg']:+.0f}%" for r in oi_top[:3])
        p.append(f"  Приток OI: {items}")
    if oi_bot:
        items = ", ".join(f"{r['symbol'].replace('USDT','')} {r['chg']:+.0f}%" for r in oi_bot[:3])
        p.append(f"  Отток OI: {items}")
    if mom_top:
        top_items = ", ".join(f"{r['symbol'].replace('USDT','')} {int(r['mom']):+d}" for r in mom_top[:3])
        bot_items = ", ".join(f"{r['symbol'].replace('USDT','')} {int(r['mom']):+d}" for r in mom_bot[:3])
        p.append(f"  Моментум ▲ {top_items}")
        p.append(f"  Моментум ▼ {bot_items}")

    # --- 6. Signals & Trading — compact ---
    p.append(f"\n{SEP}")
    if total_signals > 0:
        up_sigs = sum(s["cnt"] for s in signals if s["expected_direction"] == "up")
        down_sigs = sum(s["cnt"] for s in signals if s["expected_direction"] == "down")
        p.append(f"🎯 <b>Сигналы (7д):</b> {total_signals} ({up_sigs}↑ / {down_sigs}↓)")
        # top 3 signal types
        for s in signals[:3]:
            direction = f"{'↑' if s['expected_direction'] == 'up' else '↓' if s['expected_direction'] == 'down' else '•'}" if s['expected_direction'] else "•"
            p.append(f"  {direction} {s['alert_type']} ×{s['cnt']}")
    else:
        p.append("🎯 <b>Сигналы (7д):</b> тишина")

    if t and t.get("n") and t["n"] > 0:
        wr = (t["wins"] / t["n"] * 100) if t["n"] else 0
        p.append(f"💰 {t['n']} trades | WR {wr:.0f}% | PnL ${t['total_pnl']:.0f}")
    if open_pos:
        pos_items = ", ".join(f"{o['symbol'].replace('USDT','')} {o['direction']} ({(o['pnl'] or 0):+.1f}%)" for o in open_pos)
        p.append(f"📂 Open: {pos_items}")

    # --- 7. OUTLOOK ---
    p.append(f"\n{SEP}")
    p.append("💡 <b>Outlook</b>\n")

    # Determine bias
    bull_points = []
    bear_points = []

    # Funding skew (reuse counts from above)
    if neg_fund_cnt > pos_fund_cnt and neg_fund_cnt >= 3:
        bull_points.append(f"Шорты перегружены ({neg_fund_cnt}/{len(all_fund)}) — сквиз вероятен")
    elif pos_fund_cnt > neg_fund_cnt and pos_fund_cnt >= 3:
        bear_points.append(f"Лонги перегружены ({pos_fund_cnt}/{len(all_fund)})")

    # Regime
    if cz > 2:
        bear_points.append(f"Composite z = {cz:+.1f} — экстремальный перегрев, откат вероятен")
    elif cz > 1:
        bear_points.append(f"Composite z = {cz:+.1f} — зона перекупленности")
    elif cz < -1:
        bull_points.append(f"Composite z = {cz:+.1f} — зона перепроданности, разворот вероятен")
    elif cz < -2:
        bull_points.append(f"Composite z = {cz:+.1f} — вымытость, сильный лонг-сетап")

    # VRP
    for sym_key in ("BTCUSDT", "ETHUSDT"):
        if sym_key in vol_data and vol_data[sym_key]["vrp"] is not None:
            if vol_data[sym_key]["vrp"] < -5:
                bear_points.append(f"{sym_key[:3]} VRP {vol_data[sym_key]['vrp']:+.0f} — рынок недооценивает риск")
            elif vol_data[sym_key]["vrp"] > 5:
                bull_points.append(f"{sym_key[:3]} VRP {vol_data[sym_key]['vrp']:+.0f} — опционы дорогие, страх завышен")

    # BTC trend
    if btc_7d_chg > 3:
        bull_points.append(f"BTC {btc_7d_chg:+.1f}% за неделю — моментум вверх")
    elif btc_7d_chg < -3:
        bear_points.append(f"BTC {btc_7d_chg:+.1f}% за неделю — моментум вниз")

    # Regime trend
    if cz_delta > 1:
        bear_points.append("Regime ускоряется вверх — перегрев нарастает")
    elif cz_delta < -1:
        bull_points.append("Regime охлаждается — давление снимается")

    # Signal skew — what did the system actually detect this week
    if total_signals >= 5:
        up_sigs = sum(s["cnt"] for s in signals if s["expected_direction"] == "up")
        down_sigs = sum(s["cnt"] for s in signals if s["expected_direction"] == "down")
        if up_sigs > down_sigs * 2:
            bull_points.append(f"Сигналы бычьи ({up_sigs}↑ vs {down_sigs}↓ за неделю)")
        elif down_sigs > up_sigs * 2:
            bear_points.append(f"Сигналы медвежьи ({down_sigs}↓ vs {up_sigs}↑ за неделю)")
        elif up_sigs > 0 and down_sigs > 0:
            if up_sigs > down_sigs:
                bull_points.append(f"Сигналы смешанные, уклон бычий ({up_sigs}↑ vs {down_sigs}↓)")
            else:
                bear_points.append(f"Сигналы смешанные, уклон медвежий ({down_sigs}↓ vs {up_sigs}↑)")

    bull_n = len(bull_points)
    bear_n = len(bear_points)
    total = bull_n + bear_n or 1
    bull_pct = int(bull_n / total * 100)

    if bull_pct >= 65:
        bias = "🟢 Лонг"
    elif bull_pct >= 55:
        bias = "🟢 Осторожный лонг"
    elif bull_pct >= 45:
        bias = "🟡 Нейтральный"
    elif bull_pct >= 35:
        bias = "🔴 Осторожный шорт"
    else:
        bias = "🔴 Шорт"

    p.append(f"<b>Bias: {bias}</b> ({bull_pct}/{100-bull_pct})\n")

    if bull_points:
        p.append("<b>За рост:</b>")
        for pt in bull_points:
            p.append(f"  + {pt}")
    if bear_points:
        p.append("<b>За падение:</b>")
        for pt in bear_points:
            p.append(f"  − {pt}")

    # Watchlist — actionable
    p.append(f"\n👀 <b>Watch:</b>")
    watch_items = []
    # Squeeze candidates
    squeeze = [f for f in fund_rows if f["funding_zscore"] and f["funding_zscore"] < -5]
    for sq in squeeze[:2]:
        sym = sq['symbol'].replace('USDT', '')
        watch_items.append(f"  • <b>{sym}</b> — фандинг {sq['funding_zscore']:+.1f}, кандидат на сквиз")
    # OI leader
    if oi_top and oi_top[0]["chg"] and oi_top[0]["chg"] > 10:
        sym = oi_top[0]['symbol'].replace('USDT', '')
        watch_items.append(f"  • <b>{sym}</b> — OI {oi_top[0]['chg']:+.0f}%, активный приток денег")
    # Momentum leader
    if mom_top and abs(int(mom_top[0]['mom'])) > 50:
        sym = mom_top[0]['symbol'].replace('USDT', '')
        mom_val = int(mom_top[0]['mom'])
        watch_items.append(f"  • <b>{sym}</b> — моментум {mom_val:+d}, {'тренд силён' if mom_val > 0 else 'слабость'}")

    if watch_items:
        p.extend(watch_items)
    else:
        p.append("  Нет выраженных сетапов")

    return "\n".join(p)


async def send_report(text: str) -> bool:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    thread_id = 188  # Weekly outlook topic

    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id

    async with aiohttp.ClientSession() as session:
        # Split if needed
        if len(text) <= 4096:
            parts = [text]
        else:
            parts = []
            current = ""
            for paragraph in text.split("\n\n"):
                if len(current) + len(paragraph) + 2 > 4096:
                    if current:
                        parts.append(current.rstrip())
                    current = paragraph
                else:
                    current = current + "\n\n" + paragraph if current else paragraph
            if current:
                parts.append(current.rstrip())

        for part in parts:
            payload["text"] = part
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"Telegram error ({resp.status}): {body[:300]}")
                    return False
        return True


async def main():
    db = sqlite3.connect(str(DB_PATH))
    try:
        report = build_report(db)
        print(report)
        print(f"\n--- Length: {len(report)} / 4096 ---\n")

        if "--dry-run" not in sys.argv:
            ok = await send_report(report)
            print("Sent!" if ok else "FAILED to send")
        else:
            print("[dry-run] Not sending to Telegram")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
