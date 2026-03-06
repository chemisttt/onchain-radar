# TODO — On-Chain Radar

Фичи, которых нет в текущей реализации. Основано на анализе [TradingRiot Analytics](https://analytics.tradingriot.com/resources/platform-guide).

Приоритет: 1 → 2 → 3 → 4 → 5 → 6 → 7

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
