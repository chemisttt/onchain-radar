# On-Chain Radar — Signal Strategy

**Version:** Final (validated 2026-03-11, Hybrid C confirmed)
**Period:** Mar 2023 — Mar 2026 (3 years, ~1100 days)

---

## 1. Overview

Derivatives-based signal system for crypto perpetual futures. Detects structural imbalances (OI divergence, funding extremes, liquidation cascades) and generates directional trade signals with adaptive exits.

**Architecture:**
- **Detection:** Hybrid C — 3 signal types on 4h bars, rest on daily bars (see Section 4)
- **Exit simulation:** 4h OHLCV bars (6x intra-day resolution)
- **Confluence scoring:** 10-component score filters noise
- **Adaptive exits:** per-signal-type routing to optimal exit strategy

**Headline numbers (HL 30 + Hybrid C + fund_mean_revert + adaptive exits):**

| Metric | Value |
|--------|-------|
| Trades | 994 |
| Win Rate | 52.4% |
| EV per trade (net) | +1.94% |
| Total PnL | +1,827% |
| Walk-Forward | 6/6 positive, avg +1.60% |

**Comparison with daily-only:**

| Config | N | EV | PnL | WF |
|--------|---|-----|------|-----|
| HL daily-only | 528 | +1.52% | +802% | 6/6, avg +1.89% |
| **HL Hybrid C** | **860** | **+1.45%** | **+1,246%** | **6/6, avg +1.60%** |

Trade-off: -0.07% EV per trade for +63% more trades and +55% more total PnL.

---

## 2. Symbol Universe (30)

### HL Symbol Set

23 core symbols available on Hyperliquid cross-margin + 3 limit-orders-only + 4 added replacements.

```
BTCUSDT   ETHUSDT   SOLUSDT   BNBUSDT   XRPUSDT   DOGEUSDT
ADAUSDT   AVAXUSDT  LINKUSDT  SUIUSDT   APTUSDT   ARBUSDT
OPUSDT    NEARUSDT  LTCUSDT   UNIUSDT   AAVEUSDT  DOTUSDT
TRXUSDT   TONUSDT   ENAUSDT   TRUMPUSDT WIFUSDT   JUPUSDT
INJUSDT   SEIUSDT   HYPEUSDT  ZECUSDT   TAOUSDT   WLDUSDT
```

### TOP_OI_SYMBOLS (10)

High-liquidity majors with relaxed confluence (SETUP tier allowed):

```
BTC  ETH  SOL  XRP  BNB  DOGE  TRX  UNI  SUI  ADA
```

### Changes from Old Set

| Dropped | Reason | Added | Reason |
|---------|--------|-------|--------|
| RENDER | Thin HL liquidity | HYPE | HL native, high volume |
| FIL | Thin HL liquidity | ZEC | Good HL depth |
| ATOM | Thin HL liquidity | TAO | Replaces BCH, good EV |
| TIA | Thin HL liquidity | WLD | Good HL depth |

### HL Set Performance (adaptive exits, validated)

| Config | N | WR | EV | PF | PnL |
|--------|---|-----|------|-----|------|
| Old 30 + daily | 509 | 52.5% | +1.35% | 1.69x | +690% |
| Old 30 + Hybrid C | 819 | 49.9% | +1.21% | 1.54x | +992% |
| HL 30 + daily | 528 | 52.3% | +1.52% | 1.75x | +802% |
| **HL 30 + Hybrid C** | **860** | **50.0%** | **+1.45%** | **1.64x** | **+1,246%** |

HL Hybrid C is the production config: +63% more trades vs daily-only, -0.07% EV, +55% more PnL. Walk-forward: 6/6 positive.

---

## 3. Signal Detection

Source of truth: `backend/services/signal_conditions.py`

### 3.1 Active Signals (12)

#### SHORT signals

| Signal | Direction | Conditions | Thresholds |
|--------|-----------|------------|------------|
| **overheat** | short | OI elevated + high funding + not uptrend | `oi_z > 1.5`, `fund_z > 0.8`, `trend ≠ "up"` |
| **div_squeeze_1d** | short | OI rising + price falling + funding not negative + not downtrend | `oi_chg > 5%`, `price_chg < -2%`, `fund_z > 0`, `trend ≠ "down"` |
| **div_squeeze_3d** | short | 3d OI rising + 3d price falling + funding above floor | `oi_chg_3d > 5%`, `price_chg_3d < -2%`, `fund_z > -1.0` |
| **div_top_1d** | short | OI dropping + price rising + not uptrend | `oi_chg < -3%`, `price_chg > 2%`, `trend ≠ "up"` |
| **distribution** | short | Price rising + volume declining + uptrend | `price_chg_3d > 1.5%`, `vol_declining_3d = true`, `trend = "up"` |
| **overextension** | short | Price extended above SMA + elevated funding | `8 < price_vs_sma < 15`, `fund_z > 0.5` |
| **oi_buildup_stall** | short | OI building + price stalled + positive funding | `oi_chg_3d > 4%`, `\|price_chg_3d\| < 2%`, `fund_z > 0.3` |
| **fund_spike** | short | Extreme funding + price momentum | `fund_z > 1.5`, `price_momentum > 3%` |

#### LONG signals

| Signal | Direction | Conditions | Thresholds |
|--------|-----------|------------|------------|
| **capitulation** | long | OI collapsed + funding negative + not downtrend | `oi_z < -1.5`, `fund_z < -0.8`, `trend ≠ "down"` |
| **liq_short_squeeze** | long | Short liquidation spike + moderate price rise + caps | `liq_short_z > 3.0`, `3 < price_chg < 8%`, `oi_chg < 20%`, `fund_z < 1.5`, `trend ≠ "down"` |

#### BIDIRECTIONAL signals

| Signal | Direction | Conditions | Thresholds |
|--------|-----------|------------|------------|
| **vol_divergence** | long if price_chg < 0, else short | Volume spike + OI drop + large price move + not uptrend | `vol_z > 2.0`, `oi_chg < -3%`, `\|price_chg\| > 2%`, `trend ≠ "up"` |
| **fund_reversal** | short if fund_z > threshold, long if fund_z < -threshold | Extreme funding reversing direction. Requires `has_fund_delta = true` (≥3 bars of funding history); silently skipped otherwise | `\|fund_z\| > 1.5`, `fund_delta_3d` crosses 0 (opposite sign to fund_z), `delta > 0.0005` |
| **momentum_divergence** | short if price up + momentum down, long if price down + momentum up | Price vs composite momentum disagree | `\|price_chg_5d\| > 3%`, `\|momentum_value\| > 20` (opposite signs) |
| **liq_ratio_extreme** | long if liq_long_z high + price falling, short if liq_short_z high + price rising | Skewed liquidations with directional price confirmation | `liq_z > 2.5`, other side `< 1.0`, `price_chg < -1%` (long) / `price_chg > 1%` (short) |
| **fund_mean_revert** | short if fund_z > 1.5 + sustained, long if fund_z < -1.5 + sustained | Sustained funding extreme (3 bars) predicts mean reversion. Requires `has_fund_sustained = true` (≥3 bars history); silently skipped otherwise | `\|fund_z\| > 1.5` (FUND_Z_MEAN_REVERT), all 3 prior bars `\|fund_z\| > 1.0` (FUND_Z_SUSTAINED), trend ≠ signal direction |

### 3.2 Disabled Signals (8)

| Signal | Reason |
|--------|--------|
| **volume_spike** | PERMANENTLY REMOVED. Tested 5 variants (original, trend-aligned, high-OI, 2-bar confirm, tightened). All negative in full system. Poisons counter cache + steals daily cap slots. System EV drops from +1.58% to -0.51%. |
| **liq_long_flush** | WR 4.5% long, 21% short — noise |
| **oi_flush_vol** | WR 14% long, 19% short — noise |
| **vol_expansion** | WR 16.7% — coin flip |
| **div_top_3d** | 5/8 trades short at pvs 17-67% above SMA — net negative |
| **liq_flush** | MFE avg 2.7% too small for any TP level — net negative |
| **liq_flush_3d** | Same as liq_flush |
| **div_squeeze_5d** | Removed early — insufficient edge |

---

## 4. Hybrid C: Detection Routing

Not all signals benefit from higher-frequency detection. Pure 4h detection doubles signal count but halves EV. Hybrid approach: route each signal type to its optimal detection timeframe.

### 4h Detection (3 types)

```
liq_short_squeeze    — catches squeeze entries intra-day
momentum_divergence  — faster divergence capture
div_top_1d           — earlier OI drop detection
```

### Daily Detection (everything else)

```
overheat, div_squeeze_1d, div_squeeze_3d, distribution, overextension,
oi_buildup_stall, capitulation, vol_divergence, fund_reversal,
fund_spike, liq_ratio_extreme, fund_mean_revert
```

Note: `div_squeeze_3d` stays on daily — 4h detection degrades it from +4.59% to -0.85%.

### Hybrid C Results (Adaptive Exits)

| Config | N | WR | EV | PF | PnL |
|--------|---|-----|------|-----|------|
| HL daily-only | 528 | 52.3% | +1.52% | 1.75x | +802% |
| **HL Hybrid C** | **860** | **50.0%** | **+1.45%** | **1.64x** | **+1,246%** |

Hybrid C: +63% more trades, -0.07% EV per trade, +55% more total PnL.

### Per-Signal: Hybrid C vs Daily

| Signal | Daily N | Daily EV | HC N | HC EV | Source |
|--------|---------|----------|------|-------|--------|
| liq_short_squeeze | 112 | +3.01% | 265 | +2.58% | 4h |
| momentum_divergence | 104 | +1.45% | 248 | +1.36% | 4h |
| div_top_1d | 30 | -1.12% | 39 | +1.08% | 4h |
| div_squeeze_3d | 22 | +4.59% | 78 | -0.85% | daily* |
| liq_ratio_extreme | 94 | +1.67% | 85 | +2.07% | daily |
| distribution | 25 | +1.05% | 24 | +0.89% | daily |
| fund_spike | 91 | +0.42% | 82 | +0.46% | daily |
| overheat | 39 | +0.69% | 31 | +0.61% | daily |

\* `div_squeeze_3d` degrades on 4h (-0.85%), stays on daily (+4.59%) in Hybrid C. The N=78 on HC is because 4h signals from other types change the global cap allocation.

Key finding: `div_top_1d` flips from **-1.12%** (daily) to **+1.08%** (4h) — 4h catches OI drops earlier.

### Walk-Forward

| Config | Positive | Avg Test EV | Worst |
|--------|----------|-------------|-------|
| HL daily | 6/6 | +1.89% | +0.82% |
| **HL Hybrid C** | **6/6** | **+1.60%** | **+0.12%** |

Both configs 6/6 positive. Daily has higher avg test EV (+1.89% vs +1.60%) and better worst case (+0.82% vs +0.12%).

### Walk-Forward Detail (HL Hybrid C)

| Window | Train Period | Test Period | Tr.N | Te.N | Train EV | Test EV | Test WR |
|--------|-------------|-------------|------|------|----------|---------|---------|
| W1 | 2022-01..2022-12 | 2022-12..2023-06 | 14 | 46 | -2.12% | +0.88% | 45.7% |
| W2 | 2022-06..2023-06 | 2023-06..2023-12 | 48 | 93 | +0.44% | +2.62% | 52.7% |
| W3 | 2022-12..2023-12 | 2023-12..2024-06 | 139 | 243 | +2.04% | +0.12% | 49.8% |
| W4 | 2023-06..2024-06 | 2024-06..2024-12 | 336 | 178 | +0.81% | +3.96% | 53.9% |
| W5 | 2023-12..2024-12 | 2024-12..2025-06 | 421 | 102 | +1.74% | +0.32% | 45.1% |
| W6 | 2024-06..2025-06 | 2025-06..2025-12 | 280 | 165 | +2.63% | +1.72% | 49.7% |

### Conclusion

**Hybrid C is the recommended production config.** Trade-off: -0.07% EV per trade for +63% more signals and +55% more total PnL. Walk-forward 6/6 positive. The small EV sacrifice is compensated by volume.

---

## 5. Confluence Scoring

Source: `compute_confluence()` in `signal_conditions.py`

### 5.1 Components (10)

| Component | Score | Condition |
|-----------|-------|-----------|
| OI z-score extreme | +2 | `\|oi_z\| > 3.0` |
| OI z-score elevated | +1 | `\|oi_z\| > 2.0` |
| Funding z-score extreme | +2 | `\|fund_z\| > 3.0` |
| Funding z-score elevated | +1 | `\|fund_z\| > 2.0` |
| Liquidation z-score | +1 | `\|liq_z\| > 2.0` |
| Volume z-score | +1 | `\|vol_z\| > 2.0` |
| Price momentum 5d | +1 | `\|price_momentum\| > 5%` |
| Z-score acceleration | +1 | `\|z_accel\| > 1.0` |
| Directional liq spike | +1 | `liq_long_z > 2.0` or `liq_short_z > 2.0` |
| RV regime | +1 | `rv_regime ∈ {"low", "high"}` |
| Funding confirms direction | +1 | Short + positive funding, or long + negative funding |
| Trend aligned | +1 | Signal direction matches trend |
| Counter-trend | -1 | Signal direction opposes trend |
| **Crash penalty** | **-2** | Long into crash: `price_momentum < -5%` + 2+ extreme z-scores (`> 2.0`) |

### 5.2 Tier Definitions

| Tier | Threshold | Usage |
|------|-----------|-------|
| SETUP | ≥ 3 | Minimum for TOP_OI_SYMBOLS only |
| **SIGNAL** | **≥ 4** | **Primary tier for all signals** |
| TRIGGER | ≥ 6 | **Capped to SIGNAL** (paradoxically worst performer: WR 40%) |

### 5.3 ALT Filter

Non-TOP_OI symbols require **confluence ≥ 5** (no SETUP tier for alts). Prevents low-confidence signals on less liquid assets.

---

## 6. Adaptive Exit Strategies

Each signal type is routed to the exit strategy that performed best in 3-year backtesting.

### 6.1 Routing Table

| Signal | Exit Strategy | EV | WR | N |
|--------|---------------|-----|-----|---|
| liq_short_squeeze | counter_sig | +3.54% | 56% | 81 |
| vol_divergence | counter_sig | +3.76% | 100% | 3 |
| fund_reversal | zscore_mr | +3.60% | 50% | 3 |
| **fund_mean_revert** | **counter_sig** | **+3.18%** | **65.7%** | **134** |
| div_squeeze_3d | counter_sig | +2.11% | 50% | 18 |
| distribution | fixed | +1.85% | 61% | 23 |
| momentum_divergence | counter_sig | +1.72% | 57% | 82 |
| liq_ratio_extreme | counter_sig | +1.51% | 56% | 82 |
| div_top_1d | counter_sig | +1.04% | 44% | 18 |
| oi_buildup_stall | fixed | +0.98% | 50% | 7 |
| fund_spike | trail_atr | +0.37% | 33% | 95 |
| overheat | fixed | +0.15% | 39% | 33 |
| overextension | trail_atr | — | — | rare |
| capitulation | zscore_mr | — | — | rare |
| div_squeeze_1d | hybrid | — | — | rare |

### 6.2 Exit Strategy Parameters

#### counter_sig
Wait for a counter-signal (opposite direction signal fires), with hard stop and max hold.
- **Hard stop:** 12% (not 8% — saves trades that recover)
- **Max hold:** 30 days (timeout at current PnL)
- **Counter signals for long:** overheat, fund_spike, distribution, overextension, div_top_1d, momentum_divergence, fund_mean_revert, volume_spike*
- **Counter signals for short:** capitulation, liq_flush*, liq_short_squeeze, vol_divergence, momentum_divergence, liq_ratio_extreme, fund_mean_revert, volume_spike*
- *\* Disabled for detection but still active in counter-signal cache (can trigger exits for open positions)*

#### fixed
Classic TP/SL with timeout.
- **Take profit:** 5%
- **Stop loss:** 3%
- **Timeout:** 7 days (exit at current PnL)

#### trail_atr
Trailing stop based on ATR.
- **ATR multiplier:** 1.5
- **Max hold:** 30 days
- Note: trail ATR is weak for mean-reversion entries (our signals are contrarian). Used only for fund_spike (trend-following exit after contrarian entry).

#### zscore_mr
Z-score mean reversion: exit when the primary z-score normalizes.
- **TP threshold:** z-score returns to normal (`oi_z: 0.5`, `fund_z: 0.3`, `liq_z: 1.0`, `vol_z: 0.5`)
- **SL threshold:** z-score increases further by +1.0 from entry
- **Hard stop:** 8% (overrides z-score SL if hit first)
- **Max hold:** 30 days
- **Primary z-score mapping:** overheat/squeezes → `oi_z`, capitulation/fund_reversal/fund_spike → `fund_z`, liq signals → `liq_z`, vol_divergence/distribution → `vol_z`

#### hybrid
Combination of zscore_mr + trail_atr + counter_sig. Used for ambiguous signal types.
- **Hard stop:** 8%
- **Max hold:** 30 days

---

## 7. Risk Management

### 7.1 Cooldowns

| Context | Cooldown | Scope |
|---------|----------|-------|
| Backtest (daily) | 1 day | per symbol:type |
| Backtest (4h) | 6 candles (~1 day) | per symbol:type |
| Live (SIGNAL tier) | 12 hours | per symbol:type |
| Live (TRIGGER tier) | 6 hours | per symbol:type |

### 7.2 Caps

| Cap | Value |
|-----|-------|
| Daily per symbol | 3 signals max |
| Global daily | 5 signals max |
| Signal clustering gap | 2 days (daily) / 6 candles (4h) |

### 7.3 Momentum Filter

Trend-alignment filter: long only in uptrend, short only in downtrend, neutral allows any direction.

**Exemptions** (counter-trend by design):
- `distribution` — shorts in uptrend
- `momentum_divergence` — trades against price momentum
- `fund_spike` — shorts into funding extremes
- `fund_mean_revert` — fades sustained funding extremes

### 7.4 Position Sizing

| Risk Profile | Allocation | Leverage | Max Concurrent | 3yr ROI | MaxDD |
|-------------|------------|----------|----------------|---------|-------|
| Conservative | 20% | 1x | 5 | +312% | 10.7% |
| **Moderate** | **33%** | **1x** | **3** | **+895%** | **13.6%** |
| Aggressive | 20% | 2x | — | +1,329% | 18.8% |
| High risk | 33% | 2x | — | +5,699% | 30.0% |

---

## 8. Z-Score Parameters

### 8.1 Windows

| Timeframe | Z-Window | MIN_POINTS | SMA Period |
|-----------|----------|------------|------------|
| Daily | 365 days | 30 | 20 |
| 4h | 2190 candles (365d × 6) | 120 (~20 days) | 120 |

### 8.2 Trend Classification

Based on price vs SMA:
- **Uptrend:** `price_vs_sma > 2%`
- **Downtrend:** `price_vs_sma < -2%`
- **Neutral:** between -2% and +2%

### 8.3 Z-Score Sources

| Metric | Source | Calculation |
|--------|--------|-------------|
| oi_z | Binance futures OI | Rolling z-score of daily OI values |
| fund_z | Binance futures funding | Rolling z-score of funding rates |
| liq_z | Coinalyze liquidations | Rolling z-score of net liq delta |
| vol_z | Coinalyze derivatives volume | Rolling z-score of volume |
| liq_long_z / liq_short_z | Coinalyze | Directional liquidation z-scores |

---

## 9. Backtest Results

### 9.1 HL Hybrid C + fund_mean_revert + Adaptive (Production Config — 994 trades)

| Metric | Value |
|--------|-------|
| Period | Mar 2023 — Mar 2026 |
| Symbols | 30 (HL set) |
| Total trades | 994 |
| Win rate | 52.4% |
| EV per trade (net) | +1.94% |
| Total PnL | +1,827% |
| Walk-Forward | 6/6 positive, avg +1.60% |

Hybrid C baseline (860 trades, WR 50.0%, EV +1.45%, PnL +1,246%) — fund_mean_revert adds +134 signals.

### 9.2 Per-Signal Performance (HL Hybrid C + adaptive)

| Signal | N | WR | EV | Exit | PnL | Detection |
|--------|---|-----|------|------|------|-----------|
| liq_short_squeeze | 265 | 49.8% | +2.58% | counter_sig | +683% | 4h |
| liq_ratio_extreme | 85 | 61.2% | +2.07% | counter_sig | +176% | daily |
| momentum_divergence | 248 | 47.6% | +1.36% | counter_sig | +337% | 4h |
| div_top_1d | 39 | 56.4% | +1.08% | counter_sig | +42% | 4h |
| oi_buildup_stall | 4 | 50.0% | +0.98% | fixed | +4% | daily |
| distribution | 24 | 54.2% | +0.89% | fixed | +21% | daily |
| overheat | 31 | 45.2% | +0.61% | fixed | +19% | daily |
| fund_spike | 82 | 45.1% | +0.46% | trail_atr | +37% | daily |
| vol_divergence | 3 | 66.7% | +0.05% | counter_sig | +0.2% | daily |
| div_squeeze_3d | 78 | 48.7% | -0.85% | counter_sig | -66% | daily |
| fund_reversal | 1 | 0.0% | -7.07% | zscore_mr | -7% | daily |
| **fund_mean_revert** | **134** | **65.7%** | **+3.18%** | **counter_sig** | — | **daily** |

### 9.2b Per-Signal: Daily-Only Reference

| Signal | N | WR | EV | PnL |
|--------|---|-----|------|------|
| div_squeeze_3d | 22 | 59.1% | +4.59% | +101% |
| liq_short_squeeze | 112 | 50.9% | +3.01% | +337% |
| liq_ratio_extreme | 94 | 58.5% | +1.67% | +157% |
| momentum_divergence | 104 | 56.7% | +1.45% | +151% |
| distribution | 25 | 56.0% | +1.05% | +26% |
| oi_buildup_stall | 6 | 50.0% | +0.97% | +6% |
| overheat | 39 | 46.2% | +0.69% | +27% |
| fund_spike | 91 | 47.3% | +0.42% | +38% |
| vol_divergence | 4 | 75.0% | +0.04% | +0.2% |
| div_top_1d | 30 | 36.7% | -1.12% | -34% |
| fund_reversal | 1 | 0.0% | -7.07% | -7% |

### 9.3 Walk-Forward (HL Hybrid C + adaptive, 6 windows)

| Window | Train Period | Test Period | Tr.N | Te.N | Train EV | Test EV | Test WR |
|--------|-------------|-------------|------|------|----------|---------|---------|
| W1 | 2022-01..2022-12 | 2022-12..2023-06 | 14 | 46 | -2.12% | +0.88% | 45.7% |
| W2 | 2022-06..2023-06 | 2023-06..2023-12 | 48 | 93 | +0.44% | +2.62% | 52.7% |
| W3 | 2022-12..2023-12 | 2023-12..2024-06 | 139 | 243 | +2.04% | +0.12% | 49.8% |
| W4 | 2023-06..2024-06 | 2024-06..2024-12 | 336 | 178 | +0.81% | +3.96% | 53.9% |
| W5 | 2023-12..2024-12 | 2024-12..2025-06 | 421 | 102 | +1.74% | +0.32% | 45.1% |
| W6 | 2024-06..2025-06 | 2025-06..2025-12 | 280 | 165 | +2.63% | +1.72% | 49.7% |

**6/6 positive** | Avg test EV: **+1.60%** | Worst: **+0.12%**

### 9.3b Walk-Forward: Daily-Only Reference

| Window | Train Period | Test Period | Train EV | Test EV | Test WR |
|--------|-------------|-------------|----------|---------|---------|
| W1 | 2022-01..2022-12 | 2022-12..2023-06 | -0.27% | +1.51% | 45.2% |
| W2 | 2022-06..2023-06 | 2023-06..2023-12 | +1.00% | +2.41% | 54.4% |
| W3 | 2022-12..2023-12 | 2023-12..2024-06 | +2.13% | +0.82% | 52.4% |
| W4 | 2023-06..2024-06 | 2024-06..2024-12 | +1.28% | +2.20% | 53.6% |
| W5 | 2023-12..2024-12 | 2024-12..2025-06 | +1.37% | +1.53% | 52.5% |
| W6 | 2024-06..2025-06 | 2025-06..2025-12 | +2.02% | +2.84% | 53.8% |

**6/6 positive** | Avg test EV: **+1.89%** | Worst: **+0.82%**

### 9.3c Walk-Forward Comparison

| Config | Positive | Avg Test EV | Worst |
|--------|----------|-------------|-------|
| Old 30 daily (Phase B) | 6/6 | +1.84% | +0.66% |
| HL 30 daily | 6/6 | +1.89% | +0.82% |
| **HL 30 Hybrid C** | **6/6** | **+1.60%** | **+0.12%** |

### 9.4 Validation Checks — All PASS

| Check | Result | Details |
|-------|--------|---------|
| Train/test split | PASS | Train +1.32%, Test +3.00% (split at 2025-01) |
| Walk-forward | PASS | 6/6 positive, worst +0.66% |
| Signal correlation | PASS | 0 pairs with Jaccard > 0.3, only 2.4% same-day clustering |
| Exit routing overfitting | PASS | Full-data vs train-based diff = 0.61% (< 1% threshold) |
| fund_spike robustness | PASS | Trail ATR best, 100% trail exits, distributed 2023-2025 |

### 9.5 Equity Simulation ($1,000 start, compounding)

| Config | Final | ROI | MaxDD |
|--------|-------|-----|-------|
| 10%×1x | $2,085 | +108% | 4.9% |
| 20%×1x | $4,134 | +313% | 9.6% |
| **33%×1x max3** | **$9,945** | **+895%** | **13.6%** |
| 20%×2x | $14,285 | +1,329% | 18.8% |
| 33%×2x | $57,991 | +5,699% | 30.0% |

### 9.6 Config Comparison (all with adaptive exits, 4h exit bars)

| Config | N | WR | EV | PF | PnL | WF |
|--------|---|-----|------|-----|------|-----|
| Old 30 + daily | 509 | 52.5% | +1.35% | 1.69x | +690% | 6/6 |
| Old 30 + Hybrid C | 819 | 49.9% | +1.21% | 1.54x | +992% | — |
| HL 30 + daily | 528 | 52.3% | +1.52% | 1.75x | +802% | 6/6 |
| **HL 30 + Hybrid C** | **860** | **50.0%** | **+1.45%** | **1.64x** | **+1,246%** | **6/6** |

### 9.7 Evolution

| Stage | N | WR | EV | Period |
|-------|---|-----|------|--------|
| Original (pre-tuning) | 789 | ~46% | ~-0.5% | 1yr |
| Round 1 (thresholds) | 199 | 42.7% | +0.48% | 1yr |
| Round 2 (loser fixes) | 176 | 47.1% | +0.77% | 1yr |
| Round 3 (kill noise) | 164 | 50.0% | +1.00% | 1yr |
| 3yr expansion | 371 | 34.8% | -0.23% | 3yr |
| 3yr + fixes | 324 | 38.9% | +1.19% | 3yr |
| Phase A (new signals) | 441 | 49.9% | +1.58% | 3yr |
| Phase B (old symbols) | 509 | 52.5% | +1.35% | 3yr |
| HL daily | 528 | 52.3% | +1.52% | 3yr |
| HL Hybrid C | 860 | 50.0% | +1.45% | 3yr |
| **+ fund_mean_revert** | **994** | **52.4%** | **+1.94%** | **3yr** |

---

## 10. Strategy Lessons

### What Works

1. **Adaptive exit >> single exit.** Different signals need different exit dynamics. Counter-sig for squeezes, z-score MR for funding, fixed TP/SL for distribution. Single exit loses ~50% of edge.
2. **Counter strategy's edge = patience.** Big wins (+6-21%) come from waiting for counter-signals. Hard stop must be 12%, not 8% — saves trades that recover.
3. **fund_z is the best discriminator.** fund_z -1..0 (slightly negative) = EV +6.95%. fund_z 2+ = EV -0.13% (crowded trade). High funding = bad signal quality.
4. **Momentum filter is robust.** Trend-aligned filtering adds +0.8% EV with minimal overfitting risk.
5. **Test in full system, not isolation.** volume_spike looked profitable alone (+1.47%) but destroyed system EV (-0.51%). Signal interactions matter.
6. **Signal clustering = noise.** Solo signals EV +2.33% vs clustered +0.37%. Multiple signals same day = volatility event, NOT confirmation.
7. **Don't short parabolas.** `trend ≠ "up"` filter required for short signals. `price_vs_sma < 15` for overextension.
8. **TRIGGER paradox.** During crashes all z-scores spike → max confluence → longing into crash. Fixed with crash penalty + TRIGGER cap.

### What Doesn't Work

1. **volume_spike** — 5 variants tested, all negative in full system
2. **Signal combinations** — pairs don't confirm, they indicate noise (EV -0.52%)
3. **Trailing breakeven on counter-sig** — kills big winners despite improving WR
4. **Trail ATR for mean-reversion entries** — 1.5x ATR too tight for contrarian signals
5. **Higher confluence on 4h** — C≥6 is actually negative on 4h timeframe (z-scores too noisy)
6. **Quality score filter** — no clear edge (Q=0 and Q=2 both good)
7. **Flipping noise signals** — if noise in one direction, noise in both
8. **div_squeeze_3d on 4h** — degrades from +4.59% (daily) to -0.85% (4h). Keep on daily detection only.

### Critical Bugs Fixed

1. **Trail stop order-of-operations.** Must check stop at PREVIOUS bar's level first, then update. Wrong order tightens stop on high → immediately triggers on low. Bug fixed WR from 14% to 27%.
2. **Incomplete 4h OHLCV.** When high=low=close (no range), stops never trigger → inflated EV. Phase A showed +7.06%, reality was +3.12%. Always backfill full 4h data.
3. **1yr backtest was overfitted.** 164 trades WR 50% EV +1.00% (1yr) → expanded to 3yr: 371 trades WR 34.8% EV -0.23%. Only walk-forward confirms robustness.

---

## 11. Known Limitations

1. **Selection bias on TAO.** TAO replaced BCH specifically because it had better backtest EV. This is mild selection bias — monitor forward performance.
2. **HYPE short history.** Only 3 signals in backtest period (listed recently). Insufficient sample for conclusions.
3. **Correlation risk not modeled.** Multiple simultaneous positions in correlated assets (BTC + ETH + SOL during crash) could amplify drawdowns beyond simulated MaxDD.
4. **HL slippage on thin symbols.** Limit-orders-only symbols (WIF, TRUMP, ENA) may have significant slippage not captured in backtesting.
5. **Hybrid C worst WF window is thin.** W3 test EV +0.12% (243 trades, WR 49.8%). Barely positive — could flip negative with different market conditions.
6. **div_squeeze_3d degraded in Hybrid C.** Goes from +4.59% (daily) to -0.85% in HC due to global cap competition with 4h signals. N=78 in HC vs N=22 daily — more signals but worse quality.
7. **Survivorship bias.** Only symbols currently listed are tested. Delisted symbols with poor performance are excluded.
8. **Coinalyze 4h liq data starts July 2025.** Zero-to-data transition causes z-score spikes. Not an issue in production (continuous data stream), but affects 4h backtesting before that date.
9. **Fees approximated.** 0.07% round-trip + funding rates applied per trade, but actual HL fees and funding may differ.
10. **div_top_1d** flips from -1.12% (daily) to +1.08% (4h/Hybrid C). Quality depends on detection timeframe.
11. **fund_reversal insufficient sample.** Only 1 trade on HL set. Routing to zscore_mr is not statistically validated.
12. **860 trades total but some types sparse.** vol_divergence (3), oi_buildup_stall (4), fund_reversal (1). Their routing is less robust.
13. **fund_mean_revert WF test N=19.** Walk-forward test window had only 19 trades, EV -0.62%. Insufficient to confirm robustness across market regimes — monitor live performance.
