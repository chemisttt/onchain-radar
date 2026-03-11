# 4h Signal Detection Research (2026-03-11)

## Context

Baseline Phase B: 441 signals (adaptive exit), WR 49.9%, EV +1.58%.
This research uses fixed TP=5%/SL=3% MFE evaluation for apple-to-apple comparison.

## Raw Comparison: Daily vs 4h Detection

| Config | N | WR | EV | PF | Total PnL |
|--------|---|-----|------|-----|-----------|
| Daily | 677 | 44.7% | +0.56% | 1.35x | +379% |
| 4h full | 1372 | 40.9% | +0.28% | 1.15x | +390% |

4h generates 2x more signals but EV drops by half. Total PnL roughly same (~390%) due to volume.

## Per Signal Type

| Signal | D.N | D.WR | D.EV | 4h.N | 4h.WR | 4h.EV | Delta |
|--------|-----|------|------|------|-------|-------|-------|
| liq_short_squeeze | 80 | 38.8% | +0.10% | 122 | 42.6% | +0.41% | **+0.31%** |
| momentum_divergence | 199 | 39.3% | +0.15% | 361 | 41.3% | +0.31% | **+0.16%** |
| capitulation | 2 | 50.0% | +1.00% | 8 | 50.0% | +1.00% | 0.00% |
| div_squeeze_3d | 46 | 46.7% | +0.74% | 110 | 45.9% | +0.69% | -0.05% |
| fund_spike | 106 | 44.3% | +0.55% | 191 | 43.4% | +0.46% | -0.09% |
| div_top_1d | 32 | 61.3% | +1.68% | 100 | 52.6% | +1.21% | -0.47% |
| overheat | 39 | 51.3% | +1.10% | 114 | 42.5% | +0.41% | -0.70% |
| distribution | 60 | 46.7% | +0.73% | 111 | 33.0% | -0.30% | -1.03% |
| vol_divergence | 7 | 57.1% | +1.57% | 14 | 42.9% | +0.43% | -1.14% |
| liq_ratio_extreme | 91 | 48.3% | +0.84% | 147 | 32.4% | -0.37% | -1.21% |
| oi_buildup_stall | 13 | 58.3% | +1.21% | 78 | 32.0% | -0.41% | -1.62% |

## Deep Analysis

### Tier Filter
- 4h SIGNAL-only: N=744, WR=40.4%, EV=+0.24%
- 4h SETUP-only: N=627, WR=41.6%, EV=+0.34%
- Surprise: SETUP better than SIGNAL on 4h (opposite of daily)

### 2025-Q2/Q3 Anomaly
- 374 signals with EV -0.04% (noise)
- Dominated by liq_ratio_extreme (116), liq_short_squeeze (98), momentum_divergence (69)
- Without anomaly: 997 signals, WR 42.4%, EV +0.41%
- 4h detection 4.8x-9.2x more signals in this period — needs investigation

### Confluence on 4h
- C>=5: N=482, EV=+0.12% (worse)
- C>=6: N=148, EV=-0.08% (negative!)
- Higher confluence doesn't help on 4h — z-scores too noisy at granular level

## Hybrid Results (MAIN FINDING)

| Config | N | WR | EV | Total PnL |
|--------|---|-----|------|-----------|
| **Daily baseline** | **677** | **44.7%** | **+0.56%** | **+379%** |
| 4h full | 1371 | 40.9% | +0.28% | +390% |
| Hybrid A (6 types 4h) | 1070 | 44.4% | +0.55% | +589% |
| Hybrid B (lss+mom 4h) | 868 | 44.4% | +0.54% | +469% |
| **Hybrid C (4 types 4h)** | **995** | **44.7%** | **+0.57%** | **+569%** |

### Hybrid C Definition
4h detection for: `liq_short_squeeze`, `momentum_divergence`, `div_squeeze_3d`, `div_top_1d`
Daily detection for: everything else

### Why Hybrid C Wins
- EV identical to daily (+0.57% vs +0.56%)
- WR identical (44.7%)
- 47% more signals (995 vs 677)
- +50% more total PnL (+569% vs +379%)
- These 4 signal types benefit from intra-day granularity — catch entries earlier

### Hybrid A Per-Type
| Signal | N | WR | EV | Source |
|--------|---|-----|------|--------|
| div_top_1d | 100 | 52.6% | +1.21% | 4h |
| capitulation | 8 | 50.0% | +1.00% | 4h |
| div_squeeze_3d | 108 | 45.8% | +0.68% | 4h |
| vol_divergence | 7 | 57.1% | +1.57% | daily |
| oi_buildup_stall | 11 | 60.0% | +1.25% | daily |
| fund_spike | 181 | 43.6% | +0.47% | 4h |
| liq_ratio_extreme | 88 | 46.5% | +0.70% | daily |
| overheat | 36 | 47.2% | +0.78% | daily |
| liq_short_squeeze | 122 | 42.6% | +0.41% | 4h |
| distribution | 52 | 42.3% | +0.38% | daily |
| momentum_divergence | 355 | 41.5% | +0.32% | 4h |

## Answered Questions

### 1. 2025-Q2/Q3 Anomaly — DATA ARTIFACT, NOT MARKET REGIME

Root cause: Coinalyze 4h liquidation data starts only from July 2025.
Before that — zeros. When liq data appears after 6 months of zeros,
z-scores spike instantly → mass false signals:
- liq_ratio_extreme: 116 signals (biggest contributor)
- liq_short_squeeze: 98 signals

**Will NOT repeat in production** — continuous data stream, no zero-to-data transition.

Data completeness in derivatives_4h (BTC example):
- OI: full coverage (daily OI propagated to 4h bars)
- Liquidations: 0 before July 2025, full after
- Funding: ~50% of bars (3 settlements/day mapped to 6 bars)

### 2. Oct-Nov 2025 "Flash Crash" — NORMAL CORRECTION

Not a flash crash. Multi-week correction:
- Oct 10: 124k → 112k (-7.3% in 1 day)
- Oct 10-17: 124k → 106k (-15% over 1 week)
- Nov 1-20: 110k → 85k (-23% over 3 weeks)

This is normal crypto correction (~1/year), NOT a black swan.
Strategy should handle this — excluding it would be overfitting.

True flash crashes (May 2021: -30% in 1 hour) are rare and
could be modeled as tail risk, but this isn't one.

### 3. Hybrid in Production

Implementation approach: run both daily and 4h detection,
route each signal type to its preferred timeframe.

## Conclusion

Pure 4h detection = noisy. But **hybrid approach** (4 types on 4h + rest on daily)
preserves quality while increasing signal count 47%. Worth implementing if:
- 2025-Q2/Q3 anomaly is understood and acceptable
- Flash crash impact is quantified
- Walk-forward validation confirms robustness
