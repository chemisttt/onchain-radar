# Equity Simulation: Leverage & Position Sizing

**Date:** 2026-03-11 (Phase B validation complete)
**Script:** `/tmp/equity_sim.py`
**Strategy:** F: Adaptive (4h exit mode) — 441 signals, validated
**Start capital:** $1,000

---

## Phase B Validation (Mar 11, 2026)

### Validation Results — All Checks PASS

| Check | Result | Details |
|---|---|---|
| Train/test split | **PASS** | Train EV +1.32%, Test EV +3.00% (retention 227%) |
| Walk-forward (6 windows) | **PASS** | 6/6 positive, avg test EV +1.84%, worst +0.66% |
| Signal correlation | **PASS** | 0 pairs with Jaccard > 0.3, only 2.4% same-day clustering |
| Exit routing overfitting | **PASS** | Full-data vs train-based diff = 0.61% (< 1% threshold) |
| fund_spike robustness | **PASS** | Trail ATR best (+0.21%), 100% trail exits, present 2023-2025 |
| volume_spike revisit | **REMOVED** | All variants negative in full system (-2.2% to -0.5%) |

### Walk-Forward Detail

| Window | Train period | Test period | Train EV | Test EV | Test WR |
|---|---|---|---|---|---|
| W1 | 2022-01..2022-12 | 2022-12..2023-06 | -0.09% | +0.89% | 40.0% |
| W2 | 2022-06..2023-06 | 2023-06..2023-12 | +0.60% | +2.60% | 55.7% |
| W3 | 2022-12..2023-12 | 2023-12..2024-06 | +2.03% | +0.66% | 43.0% |
| W4 | 2023-06..2024-06 | 2024-06..2024-12 | +1.19% | +1.64% | 50.0% |
| W5 | 2023-12..2024-12 | 2024-12..2025-06 | +1.03% | +1.44% | 54.1% |
| W6 | 2024-06..2025-06 | 2025-06..2025-12 | +1.59% | +3.83% | 58.3% |

### Structural Improvements

1. **DRY refactor**: `backtest_service.py` 4h detection now uses shared `detect_signals()` from `signal_conditions.py` (was 110 lines inline)
2. **volume_spike permanently removed**: tested 5 variants (original, trend-aligned, high-OI, 2-bar confirm) — poisons system EV from +1.58% to -0.51%
3. **Signal system**: single source of truth in `signal_conditions.py`, 3 adapters (daily, 4h-backtest, live)

---

## A. Adaptive Strategy — 3 Years (Mar 2023 → Mar 2026)

**Data:** 441 trades, 30 symbols, ~1100 days, 4h exit bars

### Trade Statistics

| Metric | Phase B (441) | Phase A (441) | Pre-fix (371) |
|---|---|---|---|
| Total trades | 441 | 441 | 371 |
| Win rate | 51.2% | 51.2% | 34.8% |
| EV per trade | **+1.73%** | +1.58% | -0.23% |
| Avg win | +7.01% | — | +10.11% |
| Avg loss | -3.82% | — | -5.75% |
| Max concurrent | 9 | — | 20 |

### Equity Curves — Compounding

| Scenario | Final | ROI | MaxDD | Peak |
|---|---|---|---|---|
| 10%×1x | $2,085 | +108.5% | 4.9% | $2,116 |
| 15%×1x | $2,953 | +195.3% | 7.2% | $3,020 |
| **20%×1x** | **$4,134** | **+313.4%** | **9.6%** | $4,259 |
| 25%×1x | $5,722 | +472.2% | 11.9% | $5,941 |
| 50%×1x | $25,121 | +2412% | 23.2% | $27,134 |
| **20%×2x** | **$14,285** | **+1329%** | **18.8%** | $15,183 |
| **33%×2x** | **$57,991** | **+5699%** | **30.0%** | $64,311 |
| 50%×2x | $271,263 | +27026% | 43.5% | $319,117 |

### Risk-Adjusted (max N concurrent, equal split)

| Max pos | Alloc each | Final | ROI | MaxDD | Skipped |
|---|---|---|---|---|---|
| 1 | 100% | $44,707 | +4371% | 31.6% | 290 |
| 2 | 50% | $26,619 | +2562% | 14.7% | 182 |
| **3** | **33%** | **$9,945** | **+895%** | **13.6%** | 106 |
| **5** | **20%** | **$4,119** | **+312%** | **10.7%** | 24 |
| 10 | 10% | $2,085 | +109% | 4.9% | 0 |

### Monthly PnL — 20%×1x

$1,000 → $4,134 (+313%, MaxDD 9.6%)

| Month | PnL | Cum |
|---|---|---|
| 2023-03 | -$63 | -$63 |
| 2023-04 | +$94 | +$31 |
| 2023-06 | +$107 | +$136 |
| 2023-07 | -$38 | +$98 |
| 2023-08 | +$33 | +$131 |
| 2023-10 | +$76 | +$207 |
| 2023-11 | +$301 | +$508 |
| 2023-12 | +$84 | +$592 |
| 2024-01 | +$99 | +$691 |
| 2024-02 | -$33 | +$658 |
| 2024-03 | +$142 | +$801 |
| 2024-04 | +$209 | +$1,010 |
| 2024-05 | -$14 | +$996 |
| 2024-06 | -$8 | +$988 |
| 2024-07 | +$19 | +$1,007 |
| 2024-08 | -$44 | +$962 |
| **2024-09** | **+$546** | **+$1,508** |
| 2024-10 | -$8 | +$1,500 |
| 2024-11 | +$70 | +$1,570 |
| 2024-12 | +$192 | +$1,762 |
| 2025-01 | +$349 | +$2,112 |
| 2025-02 | +$32 | +$2,143 |
| 2025-03 | -$48 | +$2,095 |
| 2025-05 | +$36 | +$2,132 |
| 2025-06 | +$186 | +$2,318 |
| 2025-07 | +$140 | +$2,458 |
| **2025-08** | **+$786** | **+$3,244** |
| 2025-09 | -$47 | +$3,197 |
| 2025-10 | -$10 | +$3,187 |
| 2025-11 | -$68 | +$3,119 |
| 2026-01 | +$192 | +$3,311 |
| 2026-02 | -$197 | +$3,114 |
| 2026-03 | +$20 | +$3,134 |

---

## B. Recommended Live Configs

| Risk level | Config | 3yr ROI | MaxDD | Note |
|---|---|---|---|---|
| Conservative | 20%×1x max5 | +312% | 10.7% | Safe default |
| **Moderate** | **33%×1x max3** | **+895%** | **13.6%** | **Best risk-adjusted** |
| Aggressive | 20%×2x | +1329% | 18.8% | Leverage adds edge |
| High risk | 33%×2x | +5699% | 30.0% | High DD tolerable |

---

## C. Caveats

1. **Fees included**: 0.07% round-trip fee + funding rates applied per trade
2. **No correlation risk**: Multiple positions in correlated assets possible
3. **Backtest != live**: Signal timing, execution delay, liquidity differences
4. **Sample size**: 441 trades across 3 years — statistically meaningful
5. **Survivorship bias**: Only symbols that still exist are tested
6. **Walk-forward validated**: 6/6 windows positive, no overfitting detected
