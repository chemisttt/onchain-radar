# PLAN: Adaptive Setup System

**Status:** Phase A ✅ Phase B ✅ → Phase C next
**Created:** 2026-03-09
**Prereqs:** [setup-backtest-results.md](setup-backtest-results.md), [signal-backtest-results.md](signal-backtest-results.md)

---

## Problem

Сигнальная система даёт 164 сигнала, WR 50%, EV +1.00% с фиксированным TP/SL 5%/3%. Setup backtest показал, что:
- Fixed TP/SL — worst EV (+0.57%), режет винеры
- Trailing ATR — best EV (+3.96%), PF 3.8x, но не для всех типов
- Z-Score MR — dominates на mean-reversion сигналах (overextension +15.45%)
- Один exit для всех — suboptimal. Нужен **адаптивный exit per signal type**

Сейчас: signal fires → Telegram message с generic "готовить шорт" → ручной трейд без чётких уровней.
Цель: signal fires → автоматический exit plan (конкретные уровни, strategy type) → трекинг исхода.

---

## Phase A: Adaptive Exit в бэктесте ✅ DONE (2026-03-09)

**Цель:** Доказать что адаптивный exit > любой одиночной стратегии.

### Результат

| Strategy | WR | EV | PF | Trades |
|---|---|---|---|---|
| A: Fixed 5/3 | 57.1% | +0.88% | 1.5x | 77 |
| D: Trail ATR (best single) | 50.6% | +4.69% | 4.6x | 77 |
| **F: Adaptive** | **54.5%** | **+7.06%** | **5.7x** | **77** |

Adaptive beats best single strategy by **+50% EV** and **+24% PF**.

### Что сделано

1. **Strategy F: Adaptive** добавлена в `setup_backtest.py` — маппинг signal_type → exit strategy
2. **`strategy_trail_counter()`** — новая комбо-стратегия: Trail ATR + counter-signal overlay
3. **div_squeeze_5d отключён** в 3 файлах:
   - `backtest_service.py` — daily (line ~348) + 4h (line ~792)
   - `market_analyzer.py` — signal generation + `_directional_types` + `_EXPECTED_DIRECTION`
4. **Результаты сохранены** в `docs/setup-backtest-results.md`

### Adaptive Mapping (финальный)

```
Momentum → Trail ATR:     liq_short_squeeze, div_top_1d/3d, fund_reversal, overheat
Mean-reversion → Z-Score: overextension (+15.4%), div_squeeze_3d (+10.5%)
Flush → Counter-Sig:      liq_flush (+6.0%, WR 75%)
Mixed → Trail+Counter:    vol_divergence, distribution, oi_buildup_stall, fund_spike
```

### Key Insight
Разные типы сигналов = разная market dynamics. Momentum нужен room to run (trail), mean-reversion нужна z-score normalization. Один exit для всех — suboptimal.

### Deliverables Phase A:
- [x] Strategy F в `setup_backtest.py`
- [x] Бэктест с результатами F vs остальных
- [x] div_squeeze_5d закомментирован в обоих файлах + market_analyzer
- [x] Обновлённый `docs/setup-backtest-results.md`

---

## Phase B: 4h Exit Simulation ✅ DONE (2026-03-09)

**Цель:** 6x granularity для exit simulation. Вариант 3 (гибрид): signals on daily, exits on 4h.

### Результат

| Strategy | WR | EV | PF | Trades |
|---|---|---|---|---|
| A: Fixed 5/3 (baseline) | 41.6% | +0.21% | 1.1x | 77 |
| B: Z-Score MR (best single) | 51.9% | +1.67% | 1.7x | 77 |
| **F: Adaptive** | **50.6%** | **+3.12%** | **2.8x** | **77** |

### Что сделано

1. **`scripts/backfill_ohlcv_4h.py`** — бэкфил от Binance: 500 → 3515 candles/symbol (101k rows)
2. **`setup_backtest.py --4h`** — гибрид: daily signal detection + 4h OHLCV exit simulation
3. **Trail stop fix** — order of operations: check stop FIRST, then update trail for next bar. WR: 14% → 27%
4. **Updated ADAPTIVE_EXIT** — momentum → hybrid, reversal → counter/zscore, quick → fixed
5. **Results** — `docs/setup-backtest-results.md` updated

### Key Insight: Phase A results were inflated
Before backfill: 80% of bars had high=low=close (no intra-day range) → stops rarely triggered.
With full 4h data: proper H/L → realistic stop behavior. Trail ATR much weaker than thought.
Robust strategies: Z-Score MR (+13.1% on overextension) and Counter-Sig (+7.0% on liq_flush).

### Deliverables Phase B:
- [x] `scripts/backfill_ohlcv_4h.py` — 101k rows (29/30 symbols × 3515 candles)
- [x] `setup_backtest.py --4h` — 4h exit mode
- [x] Trail stop order-of-operations fix
- [x] Updated adaptive mapping (hybrid, counter, zscore, fixed dispatch)
- [x] `docs/setup-backtest-results.md` updated with Phase B results

### Phase B.5: Equity Simulation ✅ DONE (2026-03-09)

**Goal:** Validate whether WR 50.6% / EV +3.12% translates to real PnL with compounding.

**Result:** Yes — edge is in R:R (AvgWin +9.51% vs AvgLoss -3.43% = 2.8:1).

| Config | $1000 → | ROI | MaxDD | Liqs |
|---|---|---|---|---|
| 1x 20% max5 | $1,587 | +59% | 6% | 0 |
| **3x 15% max5** | **$2,613** | **+161%** | **13%** | **0** |
| 3x 20% max3 | $3,229 | +223% | 18% | 0 |
| 5x 15% max5 | $4,363 | +336% | 21% | 0 |

- Max MAE = 12.35% → zero liquidations up to 7x
- Best months: Sep/Oct (overextension + div_squeeze_3d)
- Dead: Dec 2025 (no signals), worst: Feb 2026 (0 wins)
- Full details: `docs/equity-simulation.md`

---

## Phase C: Продовый Setup System (2-3 дня)

**Цель:** Каждый сигнал в Telegram приходит с конкретным exit планом + система отслеживает исход.

### C1. DB Schema: расширить `alert_tracking`

```sql
ALTER TABLE alert_tracking ADD COLUMN exit_strategy TEXT;     -- "trail_atr", "zscore_mr", etc.
ALTER TABLE alert_tracking ADD COLUMN stop_loss REAL;         -- computed SL level
ALTER TABLE alert_tracking ADD COLUMN tp1 REAL;               -- TP level 1
ALTER TABLE alert_tracking ADD COLUMN tp2 REAL;               -- TP level 2 (optional)
ALTER TABLE alert_tracking ADD COLUMN atr_at_entry REAL;      -- ATR(14) at signal time
ALTER TABLE alert_tracking ADD COLUMN primary_zscore TEXT;     -- "oi_z", "fund_z", etc.
ALTER TABLE alert_tracking ADD COLUMN entry_zscore_val REAL;  -- z-score value at entry
ALTER TABLE alert_tracking ADD COLUMN status TEXT DEFAULT 'open';  -- open / closed
ALTER TABLE alert_tracking ADD COLUMN exit_reason TEXT;        -- tp, sl, trail, zscore_tp, counter, timeout
ALTER TABLE alert_tracking ADD COLUMN exit_price REAL;
ALTER TABLE alert_tracking ADD COLUMN exit_at TEXT;            -- ISO datetime
ALTER TABLE alert_tracking ADD COLUMN realized_pnl REAL;      -- final P&L %
ALTER TABLE alert_tracking ADD COLUMN mfe REAL;               -- max favorable excursion %
ALTER TABLE alert_tracking ADD COLUMN mae REAL;               -- max adverse excursion %
```

### C2. `market_analyzer.py`: генерация exit плана при fire

В `_build_directional_alert()` после вычисления confluence — добавить:

```python
def _compute_exit_plan(symbol, alert_type, direction, entry_price, zscores, atr):
    """Return exit strategy params based on adaptive mapping."""
    strategy = ADAPTIVE_EXIT.get(alert_type, ADAPTIVE_EXIT["_default"])

    plan = {
        "exit_strategy": strategy,
        "hard_stop": entry_price * (0.92 if direction == "up" else 1.08),  # 8%
    }

    if strategy == "trail_atr":
        trail_dist = atr * 1.5
        plan["stop_loss"] = entry_price - trail_dist if direction == "up" else entry_price + trail_dist
        plan["tp1"] = None  # no fixed TP — trail handles it
        plan["atr_at_entry"] = atr

    elif strategy == "zscore_mr":
        primary_z = SIGNAL_PRIMARY_Z[alert_type]
        plan["primary_zscore"] = primary_z
        plan["entry_zscore_val"] = zscores[primary_z]
        plan["stop_loss"] = entry_price * (0.92 if direction == "up" else 1.08)  # hard stop only
        plan["tp1"] = None  # exit when z normalizes

    elif strategy == "counter_sig":
        plan["stop_loss"] = entry_price * (0.92 if direction == "up" else 1.08)
        plan["tp1"] = None  # exit when counter fires

    elif strategy == "trail_counter":
        trail_dist = atr * 1.5
        plan["stop_loss"] = entry_price - trail_dist if direction == "up" else entry_price + trail_dist
        plan["atr_at_entry"] = atr
        plan["tp1"] = None

    return plan
```

Сохранять plan в `alert_tracking` через `record_alert()`.

### C3. `telegram_service.py`: exit plan в сообщение

Расширить `_format_trade_setup()`:

```
📐 EXIT PLAN:
• Strategy: Trailing ATR (1.5× ATR)
• Initial Stop: $89,500
• Trail triggers at: +2% → breakeven
• Hard Stop: $84,800 (-8%)
• Max Hold: 30 days

🔄 Exit signals to watch:
• Counter-signals: capitulation, liq_flush
```

Для Z-Score MR:
```
📐 EXIT PLAN:
• Strategy: Z-Score Mean Reversion
• Primary metric: OI z-score (entry: +2.8)
• Exit when: |OI_z| < 0.5
• Hard Stop: $84,800 (-8%)
• Max Hold: 30 days
```

### C4. Background exit tracker service

Новый сервис: `services/exit_tracker.py`

```python
class ExitTracker:
    """Polls open trades every 5 min, checks exit conditions, sends close alerts."""

    async def check_open_trades(self):
        """For each alert with status='open':
        1. Get current price, z-scores, ATR
        2. Check exit conditions based on exit_strategy
        3. If exit triggered → update alert_tracking, send Telegram notification
        4. Track MFE/MAE continuously
        """

    async def _check_trail_atr(self, alert, current_price, current_atr):
        """Update trailing stop, check if hit."""

    async def _check_zscore_mr(self, alert, current_zscores):
        """Check if primary z-score normalized."""

    async def _check_counter_signal(self, alert, current_signals):
        """Check if counter-signal fired."""

    async def _check_timeout(self, alert):
        """Close if max_hold exceeded (30 days)."""
```

Регистрация в `main.py` как periodic task (5 min interval).

### C5. Telegram close notification

```
✅ TRADE CLOSED: BTC ПЕРЕГРЕВ
• Exit: Trailing ATR triggered
• Entry: $92,150 → Exit: $88,300
• P&L: +4.2% (short)
• Held: 5.3 days
• MFE: +6.8% | MAE: -1.2%
```

### C6. Frontend: Active Trades panel

Новый компонент в DerivativesPanel:

```
┌──────────────────────────────────────────────┐
│  ACTIVE TRADES (3)                           │
├──────────────────────────────────────────────┤
│  BTC short  │ -1.2% │ Trail ATR │ Stop: 93k │
│  ETH long   │ +3.4% │ Z-Score   │ OI_z: 1.2 │
│  SOL short  │ +0.8% │ Counter   │ Watching   │
└──────────────────────────────────────────────┘
```

Endpoint: `GET /api/derivatives/active-trades` → все alerts с `status='open'`.

### C7. Dashboard: исторический performance

```
┌──────────────────────────────────────────────┐
│  TRADE HISTORY (last 30 days)                │
├──────────────────────────────────────────────┤
│  WR: 58% │ EV: +3.2% │ PF: 2.8x │ 19 trades │
│                                               │
│  By Strategy:                                 │
│  Trail ATR:    62% WR, +4.1% EV (8 trades)   │
│  Z-Score MR:   75% WR, +8.3% EV (4 trades)   │
│  Counter-Sig:  50% WR, +2.1% EV (7 trades)   │
└──────────────────────────────────────────────┘
```

### Deliverables Phase C:
- [ ] DB migration: новые колонки в `alert_tracking`
- [ ] `_compute_exit_plan()` в `market_analyzer.py`
- [ ] `record_alert()` сохраняет exit plan
- [ ] Telegram: exit plan в сообщении
- [ ] `services/exit_tracker.py` — фоновый трекер
- [ ] Telegram: close notification
- [ ] Frontend: Active Trades panel
- [ ] Frontend: Trade History dashboard
- [ ] `main.py`: регистрация exit_tracker

---

## Phase D: Parameter Optimization (опционально, 1 день)

После накопления 50+ closed trades в проде:

### D1. Grid search по параметрам exit стратегий

```python
# setup_backtest.py — parameter sweep
ATR_MULTS = [1.0, 1.5, 2.0, 2.5]
BREAKEVEN_PCTS = [1.5, 2.0, 3.0]
ZSCORE_TP_THRESHOLDS = {
    "oi_z": [0.3, 0.5, 0.8, 1.0],
    "fund_z": [0.2, 0.3, 0.5],
    "liq_z": [0.5, 1.0, 1.5],
}
MAX_HOLDS = [14, 21, 30, 45]
```

### D2. Walk-forward validation

Не overfit на одном периоде:
1. Train: первые 9 месяцев → оптимальные параметры
2. Test: последние 3 месяца → валидация
3. Если test EV > 70% от train EV → параметры стабильны

### D3. Адаптивное обновление маппинга

Раз в месяц (или по 30 closed trades):
1. Перезапуск setup_backtest с новыми данными
2. Пересчёт best strategy per signal type
3. Если маппинг изменился → обновить `ADAPTIVE_EXIT` в проде

---

## Phase E: Signal Quality Scoring (опционально, 2 дня)

### E1. Confidence score per signal

Вместо binary fire/no-fire — continuous score 0-100:
```python
confidence = (
    confluence_score * 30      # 0-10 → 0-30
    + trend_alignment * 20     # -1/0/+1 → -20/0/+20
    + zscore_extremity * 20    # how far from threshold → 0-20
    + historical_wr * 30       # signal type WR → 0-30
)
```

### E2. Position sizing suggestion

```
confidence < 40  → skip or paper trade
confidence 40-60 → 0.5x size
confidence 60-80 → 1x size
confidence > 80  → 1.5x size
```

### E3. Telegram integration

```
🟢 HIGH CONFIDENCE (78/100)
📐 Suggested size: 1x
```

---

## Execution Order

```
Phase A (1-2ч)  ←── НАЧАТЬ ЗДЕСЬ
    │
    ▼
Phase B (полдня) ←── если A подтвердит гипотезу
    │
    ▼
Phase C (2-3д)   ←── основная работа: прод интеграция
    │
    ▼
Phase D (1д)     ←── после 50+ closed trades
    │
    ▼
Phase E (2д)     ←── cherry on top
```

**Критерий перехода A → B:** ✅ Adaptive > best single (met: +7.06% > +4.69%)
**Критерий перехода B → C:** ✅ 4h confirms adaptive advantage (met: F +3.12% > B +1.67%), N=77 (same signals, better exit resolution). Note: Phase A EVs were inflated by missing H/L data.
**Критерий перехода C → D:** 50+ closed trades в проде, live WR > 45%.

---

## Risk & Open Questions

1. **Sample size.** 86 signals (daily) — малая выборка. Overextension = 7, liq_flush = 4. Выводы по ним на грани noise. Phase B критична для валидации.

2. **Regime dependency.** Бэктест покрывает Oct 2024 – Mar 2026. Если рыночный режим сменится (prolonged bear) — exit оптимумы могут shift. Walk-forward (Phase D) частично решает.

3. **Execution gap.** Setup backtest assumes entry at close price. Live entry будет с slippage. ATR trailing absorbs это лучше fixed TP/SL.

4. **Counter-signal latency.** В бэктесте counter-signal проверяется at each bar close. В проде — каждые 5 мин. Разница minimal для daily bars, но важна если перейдём на 4h.

5. **Overfit risk.** Маппинг signal→strategy подобран на тех же данных что и оценён. Walk-forward validation (Phase D) — must have перед тем как доверять параметрам.

6. **OHLCV coverage.** ohlcv_4h покрывает только ~83 дня (Dec 2024 – Mar 2026). Для баров без intra-day data (до Dec) — high=low=close, что делает ATR trailing и TP/SL detection консервативными. Phase B решает.

---

## Files Affected

| Phase | File | Change |
|-------|------|--------|
| A | `backend/scripts/setup_backtest.py` | Add Strategy F: Adaptive |
| A | `backend/services/backtest_service.py` | Comment out div_squeeze_5d |
| A | `backend/services/market_analyzer.py` | Comment out div_squeeze_5d |
| B | `backend/scripts/backfill_4h.py` | NEW — derivatives_4h backfill |
| B | `backend/scripts/setup_backtest.py` | Switch to 4h mode |
| C | `backend/schema.sql` | New columns in alert_tracking |
| C | `backend/services/market_analyzer.py` | _compute_exit_plan() |
| C | `backend/services/telegram_service.py` | Exit plan formatting |
| C | `backend/services/exit_tracker.py` | NEW — background exit tracker |
| C | `backend/main.py` | Register exit_tracker |
| C | `backend/routers/derivatives.py` | /active-trades, /trade-history |
| C | `frontend/src/components/derivatives/ActiveTrades.tsx` | NEW — active trades panel |
| C | `frontend/src/components/derivatives/TradeHistory.tsx` | NEW — history dashboard |
| D | `backend/scripts/setup_backtest.py` | Parameter sweep + walk-forward |
| E | `backend/services/market_analyzer.py` | Confidence scoring |
