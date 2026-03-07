# TODO — On-Chain Radar

Фичи, которых нет в текущей реализации. Основано на анализе [TradingRiot Analytics](https://analytics.tradingriot.com/resources/platform-guide).

Приоритет: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10

---

## ~~1. VRP (Variance Risk Premium)~~ — DONE ✅

**Commit:** `354affb`

**Реализовано:**
- `options_service.py`: `_update_vrp()` — VRP = IV - RV, z-score 365d
- `schema.sql`: `vrp REAL, vrp_zscore REAL` в `daily_volatility`
- `MomentumTab.tsx`: VRP bar chart (зелёный/красный) + Rich Vol / Cheap Vol badge

---

## ~~2. Altcoin OI Dominance~~ — DONE ✅

**Commit:** `354affb`

**Реализовано:**
- `derivatives_service.py`: `alt_dom = (total - btc_oi) / total × 100` в `get_global_data()`
- `GlobalDashboard.tsx`: area chart с зонами 40% (risk-off) / 65% (risk-on)

---

## ~~3. Volatility Cone~~ — DONE ✅

**Commit:** `354affb`

**Реализовано:**
- `options_service.py`: `_compute_vol_cone()` — RV percentile bands (p10/p25/p50/p75/p90) на 7/14/30/60/90/180d
- `MomentumTab.tsx`: `VolatilityCone` компонент — stacked area bands + current RV точки

---

## ~~4. Spot Volume Delta~~ — DONE ✅ (уже было)

Taker buy/sell volume уже собирается через Binance `takerlongshortRatio` и хранится в `daily_derivatives` (`liquidations_long`/`liquidations_short`). Z-score рассчитывается как `liq_zscore`.

---

## ~~5. Composite Regime v2 (4 компонента + SMA)~~ — DONE ✅

**Commit:** `354affb`

**Реализовано:**
- `SymbolDetail.tsx`: SMA-5 smoothing на composite z-score
- `CompositeRegimeChart.tsx`: SMA-5 overlay line (жёлтая) поверх баров
- VRP z-score доступен из backend для будущего 4-компонентного расчёта BTC/ETH

**Примечание:** полный 4-компонентный composite (oi_z + funding_z + liq_z + vrp_z) / 4 для BTC/ETH можно добавить когда накопится достаточно VRP данных.

---

## ~~6. Momentum Indicator~~ — DONE ✅

**Commit:** `354affb`

**Реализовано:**
- `momentum_service.py`: полный сервис — 5 компонентов, hourly poll
  - Cross-Sectional Decile (ранг среди 30 peers)
  - Time-Series Decile (vs своя история)
  - Relative Decile (vs BTC)
  - Directional Intensity [-1, +1]
  - Volatility Regime (short vol vs long vol)
- `schema.sql`: `daily_momentum` таблица
- `main.py`: зарегистрирован, start/stop
- Формула: `decile_avg × 60 + DI × 30 + VR_signal × 10`, clamped [-100, +100]

---

## ~~7. DI/VR vs Forward Return Scatter Plots~~ — DONE ✅

**Commit:** `354affb`

**Реализовано:**
- `momentum_service.py`: `_build_scatter()` — scatter data с linear regression, R², avg_at_current
- `MomentumPage.tsx`: `ScatterCard` компонент — DI/VR vs forward return (10d/30d/60d toggle)

---

## ~~8. Momentum Page (per-symbol)~~ — DONE ✅

**Commit:** `354affb`

**Реализовано:**
- `routers/derivatives.py`: `GET /api/derivatives/{symbol}/momentum-page`
- `momentum_service.py`: `get_momentum_page()` — metrics, history, scatter, distribution, stats
- `MomentumPage.tsx`: полная страница по TradingRiot layout:
  - Header с regime badge
  - 4 metric cards (Cross-Sectional, Time Series, Relative Volume, 52W High Proximity)
  - Price + Momentum histogram (dual-axis)
  - DI time series + VR time series (2-col grid)
  - DI/VR scatter plots с R² и avg_at_current
  - Signal gauges (Momentum + Volatility Skew)
  - Price Distribution (implied vs adjusted)
- `DerivativesPanel.tsx`: новый таб "Momentum" (старый переименован в "IV/RV")
- `useMomentumPage.ts`: hook с типами

---

## ~~9. Price Distribution (Implied vs Momentum-Adjusted)~~ — DONE ✅

**Commit:** `354affb`

**Реализовано:**
- `momentum_service.py`: `_compute_price_distribution()` — BTC/ETH only
  - Implied: `price × (1 ± IV/100 × √(days/365))` для 1σ/2σ
  - Momentum-adjusted: drift + vol regime correction
  - Горизонты: 7d / 10d / 14d / 30d / 60d
- `MomentumPage.tsx`: `PriceDistributionCard` — implied vs adjusted ranges, horizon selector, range bars

---

## ~~10. Momentum/Skew Signal Gauges~~ — DONE ✅

**Commit:** `354affb`

**Реализовано:**
- `momentum_service.py`: `_compute_momentum_stats()`, `_get_skew_stats()` — score, z-score, avg, 30d change
- `MomentumPage.tsx`: `SignalGauge` компонент — горизонтальный gauge с зонами, индикатором, stats
  - Momentum: Oversold / Bearish / Neutral / Bullish / Overbought
  - Skew: Bearish / Neutral / Bullish

---

## Заметки

- Scatter plots и momentum_stats заполняются по мере накопления данных (нужно 10+ дней истории)
- Price Distribution и Skew gauge доступны только для BTC/ETH (требуют IV из Deribit)
- VRP z-score станет точнее через ~30 дней когда накопится история VRP значений
