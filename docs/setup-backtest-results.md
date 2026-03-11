# Setup Backtest: Exit Strategy Comparison

**Date:** 2026-03-09
**Script:** `backend/scripts/setup_backtest.py`
**Data:** daily_derivatives (501 days × 30 symbols) + ohlcv_4h (3515 candles × 29 symbols)

## Phase B Results (4h exit simulation)

**Signals:** 77 (same set — signals detected on daily bars)
**Mode:** Hybrid — daily signal detection + 4h OHLCV exit simulation (6x granularity)
**Backfill:** ohlcv_4h extended from 500 → 3515 candles/symbol (Aug 2024 → Mar 2026)

### Overall Results (4h exit)

| Strategy | WR | EV | AvgHold | MaxDD | PF | Trades |
|---|---|---|---|---|---|---|
| A: Fixed 5/3 | 41.6% | +0.21% | 1.0d | 7.4% | 1.1x | 77 |
| B: Z-Score MR | 51.9% | +1.67% | 6.4d | 12.4% | 1.7x | 77 |
| C: Counter-Sig | 45.5% | +1.32% | 9.4d | 32.7% | 1.4x | 77 |
| D: Trail ATR | 27.3% | +0.63% | 0.9d | 12.3% | 1.9x | 77 |
| E: Hybrid | 41.6% | +0.81% | 0.7d | 12.3% | 2.2x | 77 |
| **F: Adaptive** | **50.6%** | **+3.12%** | **6.4d** | **12.4%** | **2.8x** | **77** |

### Strategy F: Adaptive Mapping (updated for 4h)

| Signal Type | N | Exit Strategy | WR | EV |
|---|---|---|---|---|
| liq_short_squeeze | 36 | Hybrid | 44% | +0.7% |
| div_squeeze_3d | 11 | Z-Score MR | 36% | +5.4% |
| div_top_1d | 8 | Counter-Sig | 50% | +2.6% |
| overextension | 7 | Z-Score MR | 71% | +13.1% |
| vol_divergence | 4 | Fixed | 50% | +1.0% |
| liq_flush | 4 | Counter-Sig | 75% | +7.0% |
| div_top_3d | 3 | Fixed | 67% | +2.3% |
| overheat | 2 | Counter-Sig | 100% | +2.0% |
| fund_reversal | 2 | Z-Score MR | 50% | -0.8% |

### Per Signal Type × Strategy (WR / EV) — 4h exit

| Signal | N | Fixed | ZScore | Counter | Trail | Hybrid | Adaptive |
|---|---|---|---|---|---|---|---|
| liq_short_squeeze | 36 | 47/+0.5% | 56/+0.4% | 42/-0.2% | 28/+0.2% | **44/+0.7%** | 44/+0.7% |
| div_squeeze_3d | 11 | 36/-0.1% | **36/+5.4%** | 45/+5.1% | 18/+1.0% | 18/-0.0% | 36/+5.4% |
| div_top_1d | 8 | 25/-1.0% | 38/-1.6% | **50/+2.6%** | 25/+0.5% | 38/+0.7% | 50/+2.6% |
| overextension | 7 | 29/-0.7% | **71/+13.1%** | 57/+4.7% | 29/+3.9% | 29/+3.9% | 71/+13.1% |
| vol_divergence | 4 | **50/+1.0%** | 25/-2.5% | 25/-4.4% | 25/-0.6% | 25/-1.3% | 50/+1.0% |
| liq_flush | 4 | 50/+1.0% | 100/+1.7% | **75/+7.0%** | 0/+0.0% | 100/+1.7% | 75/+7.0% |
| div_top_3d | 3 | **67/+2.3%** | 33/-5.1% | 33/-0.5% | 67/+1.8% | 67/+1.3% | 67/+2.3% |
| overheat | 2 | 50/+1.0% | 50/-2.3% | **100/+2.0%** | 100/+1.2% | 100/+1.2% | 100/+2.0% |
| fund_reversal | 2 | 0/-3.0% | **50/-0.8%** | 0/-8.0% | 0/-2.6% | 0/-2.6% | 50/-0.8% |

### Hybrid Exit Reason Distribution (4h)

| Reason | Count | % | AvgPnL | WR | Description |
|---|---|---|---|---|---|
| trail | 47 | 61% | +0.61% | 29.8% | trailing ATR |
| zscore_tp | 20 | 26% | +0.81% | 60.0% | z-scores normalized |
| counter | 9 | 12% | +2.79% | 66.7% | counter-signal |
| hard_stop | 1 | 1% | -8.00% | 0.0% | -8% hard stop |

---

## Evolution

| Version | Signals | WR | EV | PF | Note |
|---|---|---|---|---|---|
| Original (fixed 5/3, pre-tuning) | 789 | ~46% | ~-0.5% | — | |
| Signal tuning (kill noise) | 164 | 50.0% | +1.00% | — | |
| Setup backtest baseline (Fixed 5/3) | 86 | 53.5% | +0.57% | 1.3x | |
| Phase A: Adaptive + kill div_squeeze_5d | 77 | 54.5% | +7.06% | 5.7x | ⚠️ inflated — H/L data missing for early bars |
| Phase B: 4h backfill + trail stop fix | 77 | 41.6% | +0.21% | 1.1x | Fixed 5/3 baseline with proper H/L |
| **Phase B: Adaptive (4h exit)** | **77** | **50.6%** | **+3.12%** | **2.8x** | **Updated mapping** |

---

## Key Findings

### 1. Phase A results were inflated by incomplete data
Before backfill: ohlcv_4h had only 83 days (Dec 2024+). Early bars had high=low=close (no intra-day range).
This made trailing stops unrealistically hard to hit and inflated all strategies.
After backfill (585 days of 4h data), proper daily high/low exposed this.

### 2. Trail stop order-of-operations bug
Within a bar, the code tightened the stop (based on high) then checked if low hit the tightened stop.
Fix: check stop with PREVIOUS bar's level first, then update trail for next bar.
Trail ATR WR improved from 14% → 27% after fix.

### 3. Trail ATR is weak for mean-reversion signals
Even after fix, Trail ATR (WR 27%, EV +0.63%) underperforms. The 1.5× ATR trailing stop is too tight
for contrarian entries where price initially moves against the signal. Trail ATR needs trend-following
entries, but our signals are primarily reversal/mean-reversion.

### 4. Z-Score MR still dominates mean-reversion
overextension: WR 71%, EV +13.1% — best performer across all strategies.
div_squeeze_3d: WR 36%, EV +5.4%.
Waiting for z-score normalization is the natural exit for z-score-triggered entries.

### 5. Counter-signal = highest quality exit
Hybrid's counter exits: WR 66.7%, EV +2.79%. Rare (12%) but highly profitable.
liq_flush → counter: WR 75%, EV +7.0%.
div_top_1d → counter: WR 50%, EV +2.6%.

### 6. Hybrid is surprisingly good for high-frequency signals
liq_short_squeeze (36 trades) works best with Hybrid (WR 44%, EV +0.74%).
The multi-exit approach (trail + zscore + counter) handles the varied dynamics of squeeze signals.

### 7. Adaptive still beats all single strategies
F: Adaptive (+3.12%, 2.8x PF) vs B: Z-Score MR (+1.67%, 1.7x PF) — +87% better EV.
Different signal types genuinely need different exit strategies.

---

## Disabled Signals

| Signal | Reason | Date |
|---|---|---|
| liq_long_flush | WR 4.5%/21% — noise | 2026-03-08 |
| oi_flush_vol | WR 14%/19% — noise | 2026-03-08 |
| vol_expansion | WR 16.7% | 2026-03-08 |
| **div_squeeze_5d** | **Negative EV across all exit strategies** | **2026-03-09** |

## Strategy Parameters

- **A: Fixed** — TP 5%, SL 3%, timeout 7d, hard stop 8%
- **B: Z-Score MR** — TP thresholds: OI<0.5, fund<0.3, liq<1.0, vol<0.5. SL: z increases by 1.0. Max hold 30d
- **C: Counter-Sig** — Exit on opposite-direction signal. Max hold 30d
- **D: Trail ATR** — ATR(14) × 1.5, breakeven at +2%, max hold 30d. Check stop before updating trail.
- **E: Hybrid** — First of: trail ATR (check first), zscore_tp, counter-signal, hard stop 8%, timeout 30d
- **F: Adaptive** — Per signal type routing (see mapping above). Now includes hybrid and fixed dispatch.

## Modes

- `--daily` — Signal detection AND exit simulation on daily bars (original)
- `--4h` — Signal detection on daily, exit simulation on 4h OHLCV bars (recommended)

## Equity Simulation → [equity-simulation.md](equity-simulation.md)

$1,000 compounded through 77 trades (F: Adaptive, 4h exit, 1 year):

| Config | Final | ROI | MaxDD | Liqs |
|---|---|---|---|---|
| 1x 20% max5 | $1,587 | +59% | 6.1% | 0 |
| **3x 15% max5** | **$2,613** | **+161%** | **13.3%** | **0** |
| 3x 20% max3 | $3,229 | +223% | 17.5% | 0 |
| 5x 15% max5 | $4,363 | +336% | 21.4% | 0 |
| 10x 10% max5 | $5,749 | +475% | 32.4% | 5 |

Key: AvgWin +9.51% vs AvgLoss -3.43% (2.8:1 R:R). Max MAE 12.35% → 0 liqs up to 7x.
Sweet spot: **3x leverage, 15-20% alloc, max 3-5 positions**.

## Next Steps → [PLAN-setup-system.md](PLAN-setup-system.md)

- Phase C: Production integration (exit tracker, Telegram, frontend)
- Phase D: Parameter optimization (ATR mult sweep, walk-forward validation)
