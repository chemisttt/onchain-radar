# TODO — On-Chain Radar

Фичи, которых нет в текущей реализации. Основано на анализе [TradingRiot Analytics](https://analytics.tradingriot.com/resources/platform-guide).

Приоритет: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10

---

## 1. VRP (Variance Risk Premium) — Low

**Данные есть:** IV и RV уже собираются для BTC/ETH в `options_service.py`.

**Backend:**
- Рассчитать `VRP = IV_30d - RV_30d` и `VRP_z = z-score(VRP, 365d)`
- `schema.sql`: добавить `vrp REAL, vrp_zscore REAL` в `daily_volatility`
- Включить VRP в ответ momentum endpoint

**Frontend:**
- `MomentumTab.tsx`: VRP area на графике IV/RV (зелёный > 0, красный < 0)
- VRP z-score badge

**Пороги:** VRP_z > +2 = "rich vol" (продавать), VRP_z < -2 = "cheap vol" (покупать)

**Ref:** docs/metrics-guide.md §13

---

## 2. Altcoin OI Dominance — Low

**Данные есть:** OI всех 30 символов в `daily_derivatives`.

**Backend:**
- `btc_dom = btc_oi / Σ(all_oi) × 100`, `alt_dom = 100 - btc_dom`
- Compute on-the-fly в `get_global_data()`, без новой таблицы

**Frontend:**
- `GlobalDashboard.tsx`: area chart Alt OI Dominance
- Зоны: > 65% risk-on (красный), < 40% risk-off (зелёный)

**Пороги:** alt_dom > 65% + funding_z > +1.5 = альты перегреты. alt_dom < 40% = вымыты.

**Ref:** docs/metrics-guide.md §16. TradingRiot использует как primary Risk Appetite.

---

## 3. Volatility Cone — Medium

**Данные есть:** price history в `daily_derivatives`.

**Backend:**
- Новая функция в `options_service.py`: `_compute_vol_cone(symbol)`
- RV на 7/14/30/60/90/180d lookback
- Percentile bands (10th, 25th, 50th, 75th, 90th) из 2+ лет истории
- Return: `{period: {p10, p25, p50, p75, p90, current}}`

**Frontend:**
- Новый компонент `VolatilityCone.tsx` в `derivatives/`
- Area chart с gradient opacity bands, current RV как точки
- Добавить на MomentumTab (BTC/ETH only)

**Ref:** docs/metrics-guide.md §14

---

## 4. Spot Volume Delta — Medium

**Новый источник данных:** Binance `takerlongshortRatio` или taker buy/sell volume.

**Backend:**
- Новый polling в `derivatives_service.py`: fetch taker volume
- `delta = taker_buy_vol - taker_sell_vol`, z-score 365d
- Хранить в `daily_derivatives` (добавить колонки) или новая таблица

**Frontend:**
- Новый бар-чарт на SymbolDetail: зелёный = buy dominant, красный = sell
- Z-score badge
- Совмещать с OI для дивергенций

**Ключевой паттерн:** positive delta + OI rising = organic demand. Negative delta + price rising = дивергенция.

**Ref:** docs/metrics-guide.md §15

---

## 5. Composite Regime v2 (4 компонента + SMA) — Medium

**Blocked by:** #1 (VRP)

**Backend:**
- BTC/ETH: `composite = (oi_z + funding_z + liq_z + vrp_z) / 4`
- Остальные: оставить 3-компонентный
- SMA-5 smoothing поверх raw composite
- Хранить smoothed value рядом с raw

**Frontend:**
- `CompositeRegimeChart.tsx`: SMA overlay line
- `SymbolDetail.tsx`: обновить расчёт для BTC/ETH

**Ref:** docs/metrics-guide.md §7. TradingRiot: OI + Funding + Skew + VRP с SMA-5.

---

## 6. Momentum Indicator — High

Самый сложный компонент. Новый сервис.

**Backend:**
- Новый сервис: `momentum_service.py`
- Требует 300+ дней price history (уже есть в `daily_derivatives`)
- Компоненты:
  1. **Cross-Sectional Decile** — ранг 1M return среди 30 peers
  2. **Time-Series Decile** — ранг 1M return vs своя история
  3. **Relative Decile** — performance vs BTC
  4. **Directional Intensity** [-1,+1] — консистентность направления
  5. **Volatility Regime** — short-term vol vs smoothed trend
- Новая таблица: `daily_momentum (symbol, date, value, cs_decile, ts_decile, rel_decile, di, vol_regime)`
- Poll: daily, после derivatives backfill

**Frontend:**
- Histogram: green (+10..+70), blue (>+70), red (-10..-70), yellow (<-70)
- MA-13 overlay, crossover signals
- Screener filter: bullish (all deciles ≥ 7, momentum > +10)

**Пороги:** ±10 bull/bear, ±70 overbought/oversold.

**Ref:** docs/metrics-guide.md §17

---

## 7. DI/VR vs Forward Return Scatter Plots — Medium

**Blocked by:** #6 (Momentum)

**Backend:**
- Extend momentum endpoint: DI и VR history с ценами
- Forward return calculation — reuse existing logic

**Frontend:**
- Reuse `ZScatterCard` или создать DI/VR вариант
- Scatter: DI vs Forward Return (10d/30d/60d)
- Scatter: Vol Regime vs Forward Return (10d/30d/60d)
- Regression line + R² + "avg at current"
- Добавить на Momentum tab

**Ref:** docs/metrics-guide.md §17

---

## 8. Momentum Page (per-symbol) — High

**Blocked by:** #6 (Momentum), #7 (DI/VR Scatter), #1 (VRP)

Полноценная Momentum-страница для каждого символа. Ref: [TradingRiot ETH Momentum](https://analytics.tradingriot.com/markets/crypto/eth/momentum).

**Layout (по TradingRiot):**

```
┌─ Header ──────────────────────────────────────────┐
│ ETH $2,072  Regime: Bullish  Momentum: +11.9      │
├─ Metrics cards (1M / 3M / 6M toggle) ─────────────┤
│ Cross-Sectional Momentum   Pos/Neg/Neut  decile   │
│ Time Series Momentum       Pos/Neg/Neut  % return  │
│ Relative Volume            Pos/Neg/Neut  Nx        │
│ Proximity to 52W High      Pos/Neg/Neut  % away    │
├─ Charts ──────────────────────────────────────────┤
│ 1. Price + Momentum histogram (dual-axis)          │
│ 2. Directional Intensity [-1, +1] time series      │
│ 3. DI vs Forward Return scatter (10d/30d/60d)      │
│ 4. Volatility Regime time series                   │
│ 5. VR vs Forward Return scatter (10d/30d/60d)      │
├─ Signal Analysis ─────────────────────────────────┤
│ 6. Price Distribution (implied vs momentum-adjusted)│
│ 7. Momentum Indicator gauge                        │
│ 8. Volatility Skew gauge                           │
└───────────────────────────────────────────────────┘
```

**Backend:**
- Новый endpoint: `GET /api/derivatives/{symbol}/momentum-page`
- Return: momentum metrics, DI series, VR series, price history, scatter data, distribution

**Frontend:**
- Новый компонент: `MomentumPage.tsx` в `derivatives/`
- Или расширить существующий `MomentumTab.tsx` до full-page layout
- Метрики: 4 карточки с цветовой индикацией (Pos=green, Neut=grey, Neg=red)
- Toggle 1M/3M/6M для метрик

**Metric cards:**

| Метрика | Pos | Neut | Neg |
|---------|-----|------|-----|
| Cross-Sectional | decile ≥ 7 | 4-6 | ≤ 3 |
| Time Series | return > 0 | ~0 | < 0 |
| Relative Volume | > 1.5x | 0.8-1.5x | < 0.8x |
| 52W High Proximity | < 5% away | 5-20% | > 20% |

**Реальные данные ETH (март 2026):**
- Momentum: +11.9, Z-Score: 0.37, Historical Avg: -7.9
- Cross-Sectional: Pos (decile 7)
- Time Series: Pos (+6.5%)
- Relative Volume: Neut (0.9x)
- 52W High Proximity: Neg (+42.9% away)
- DI scatter R²: 0.006 (n=1798)
- VR scatter R²: 0.006 (n=1770)

---

## 9. Price Distribution (Implied vs Momentum-Adjusted) — Medium

**Blocked by:** #6 (Momentum), #1 (VRP)

Сравнение implied distribution (из IV) с momentum-adjusted distribution.

**Ref: TradingRiot ETH данные:**
```
Horizons: 7d / 10d / 14d / 30d / 60d

Implied ±20.9%:  1σ $1,640-$2,505,  2σ $1,207-$2,937
TR-adj  ±22.4%:  1σ $1,623-$2,562,  2σ $1,153-$3,032
```

**Backend:**
- Новая функция в `options_service.py` или `momentum_service.py`
- Implied: `1σ = price × (1 ± IV/100 × √(days/365))`
- Momentum-adjusted: коррекция mean на основе momentum score + DI
- Return: `{horizon: {implied: {low1, high1, low2, high2}, adjusted: {...}}}`

**Формулы:**
```
1σ_low  = price × (1 - IV/100 × √(days/365))
1σ_high = price × (1 + IV/100 × √(days/365))
2σ      = аналогично с множителем 2

Momentum adjustment:
drift           = momentum_score / 100 × avg_daily_return × days
adjusted_center = price × (1 + drift)
adjusted_vol    = IV × (1 + vol_regime_factor)
```

**Frontend:**
- Новый компонент: `PriceDistribution.tsx`
- Bell curve / bar chart: implied (полупрозрачный) vs adjusted (solid)
- Horizon selector: 7d / 10d / 14d / 30d / 60d
- Текст: "Implied ±X%, Adjusted ±Y%, 1σ $A-$B, 2σ $C-$D"
- BTC/ETH only (нужен IV)

---

## 10. Momentum/Skew Signal Gauges — Low

**Blocked by:** #6 (Momentum)

Визуальные gauge-индикаторы для быстрой оценки.

**Ref: TradingRiot ETH:**
```
TradingRiot Indicator:
  [Negative ──── Neutral ──── Positive]
  Score: +11.9 | Z-Score: 0.37 | Avg: -7.9 | 30d Δ: +97.0

Volatility Skew:
  [Bearish ──── Neutral ──── Bullish]
  Score: 65.6% | Skew: 7.72 | Z-Score: 0.93 | Avg: 4.04 | 30d Δ: -3.66
```

**Frontend:**
- Новый компонент: `SignalGauge.tsx` — reusable horizontal gauge
- Props: `{label, value, min, max, zones: [{from, to, color}], stats: {score, zScore, avg, change30d}}`
- Momentum gauge: зоны Negative/Neutral/Positive, тики на ±10 и ±70
- Skew gauge: зоны Bearish/Neutral/Bullish

**Backend:**
- Данные уже будут из momentum_service (#6) и options_service
- Skew: текущий skew_25d, skew_z, historical avg, 30d change — всё уже в daily_volatility
