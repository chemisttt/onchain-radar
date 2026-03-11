# Signal Backtest Results (2026-03-11)

Backtest: 30 symbols, ~1100 days (3yr: Mar 2023 → Mar 2026), Adaptive exit strategy (4h)

## Overall Performance (3-year, Adaptive exit)

| Metric | Value |
|---|---|
| Trades | 441 |
| Win rate | 49.9% |
| EV per trade | +1.58% |
| Profit Factor | 1.86x |

## Phase B Validation (2026-03-11)

| Check | Result |
|---|---|
| Train/test EV retention | 227% (train +1.32%, test +3.00%) |
| Walk-forward (6 windows) | 6/6 positive, avg +1.84%, worst +0.66% |
| Signal correlation | 0 pairs Jaccard > 0.3 |
| Exit routing overfitting | 0.61% diff (< 1% threshold) |
| fund_spike robustness | +0.21% trail_atr, 100% trail exits, distributed 2023-2025 |
| volume_spike | PERMANENTLY REMOVED — all variants negative in full system |

## Adaptive Exit Routing (per-signal)

| Signal | N | WR | Exit Strategy | 3yr EV |
|---|---|---|---|---|
| liq_short_squeeze | 81 | 56% | counter_sig | +3.54% |
| vol_divergence | ~3 | 100% | counter_sig | +3.76% |
| fund_reversal | ~3 | 50% | zscore_mr | +3.60% |
| div_squeeze_3d | 18 | 50% | counter_sig | +2.11% |
| distribution | 23 | 61% | fixed | +1.85% |
| momentum_divergence | 82 | 57% | counter_sig | +1.72% |
| liq_ratio_extreme | 82 | 56% | counter_sig | +1.51% |
| div_top_1d | 18 | 44% | counter_sig | +1.04% |
| oi_buildup_stall | ~7 | 50% | fixed | +0.98% |
| fund_spike | 95 | 33% | trail_atr | +0.37% |
| overheat | 33 | 39% | fixed | +0.15% |
| overextension | — | — | trail_atr | marginal |
| capitulation | — | — | zscore_mr | rare |
| div_squeeze_1d | — | — | hybrid | rare |

## Disabled Signals

| Signal | Reason |
|--------|--------|
| volume_spike | PERMANENTLY REMOVED — tested 5 variants, poisons system EV from +1.58% to -0.51% |
| liq_long_flush | WR 4.5% long, 21% short — noise |
| oi_flush_vol | WR 14% long, 19% short — noise |
| vol_expansion | WR 16.7% — coin flip |
| div_top_3d | 5/8 trades short at pvs 17-67% above SMA — net negative over 3yr |
| liq_flush | MFE avg 2.7% too small for TP=5%, MAE 3.9% — net negative over 3yr |
| liq_flush_3d | Same as liq_flush — disabled |

## Key Threshold Changes Applied

### Round 1 (2026-03-08): Signal tuning
| Signal | Old | New |
|--------|-----|-----|
| liq_short_squeeze | liq_short_z>2, price>2 | liq_short_z>3, price>3 & <8, oi<20, fund_z<1.5, trend!="down" |
| div_squeeze_1d | oi>5, price<-2, fund>0 | +trend!="down" |
| div_squeeze_3d | oi_3d>5, price_3d<-2 | +fund_z>-1.0 |
| vol_divergence | no trend filter | +trend!="up" |
| capitulation | no trend filter | +trend!="down" |
| distribution | price_3d>2 | price_3d>1.5 (relaxed) |
| oi_buildup_stall | oi_3d>5, price<1.5, fund>0.5 | oi_3d>4, price<2, fund>0.3 (relaxed) |

### Round 2 (2026-03-10): 3-year validation fixes
| Signal | Change | Why |
|--------|--------|-----|
| overheat | +`trend != "up"` | Was shorting into bull runs (44% of all pre-fix trades) |
| div_top_1d | +`trend != "up"` | Fails in uptrends, works in down/neutral |
| overextension | +`price_vs_sma < 15` | Skip parabolic moves (pvs 15%+ = unstoppable) |
| div_top_3d | DISABLED | 5/8 trades at pvs 17-67% above SMA |
| liq_flush | DISABLED | MFE too small (2.7%) for any TP level |
| liq_flush_3d | DISABLED | Same as liq_flush |

## Structural Changes

- TRIGGER tier capped → max SIGNAL (TRIGGER was paradoxically worst: WR 40%)
- Crash penalty: confluence -2 when longing into crash (price<-5 + 2+ extreme z-scores)
- ALT filter: non-TOP10 symbols need confluence ≥5 (no SETUP tier)
- Daily cap: 3 signals/symbol/day (backtest), 5 directional alerts/cycle (live)
- TOP_OI_SYMBOLS: BTC, ETH, SOL, XRP, BNB, DOGE, TRX, UNI, SUI, ADA

## Phase A: New Signals (2026-03-11)

Added 3 new signal types from existing derivatives + momentum data:

| Signal | Type | Logic | N | EV |
|--------|------|-------|---|-----|
| momentum_divergence | Bidirectional | Price vs composite momentum disagree | 82 | +1.72% |
| liq_ratio_extreme | Bidirectional | Skewed liquidations → pressure | 82 | +1.51% |
| fund_spike | Short-only | Extreme funding + price momentum (rehabilitated) | 95 | +0.37% |
| ~~volume_spike~~ | ~~Bidirectional~~ | ~~Anomalous volume + direction + OI~~ | ~~DISABLED~~ | ~~-0.57%~~ |

Prerequisites:
- Backfilled `daily_momentum` table (36,080 rows, 2022-03-02 → 2026-03-10) via `scripts/backfill_momentum.py`
- Added `momentum_value`, `relative_volume`, `price_chg_5d` to SignalInput
- `momentum_divergence` and `fund_spike` exempt from momentum filter (counter-trend by design)

## Evolution

| Stage | Signals | WR | EV | Period |
|-------|---------|-----|-----|--------|
| Original (pre-tuning) | 789 | ~46% | ~-0.5% | 1yr |
| Round 1 (plan changes) | 199 | 42.7% | +0.48% | 1yr |
| Round 2 (loser fixes) | 176 | 47.1% | +0.77% | 1yr |
| Round 3 (kill noise) | 164 | 50.0% | +1.00% | 1yr |
| 3yr expansion (pre-fix) | 371 | 34.8% | -0.23% | 3yr |
| 3yr + signal fixes | 324 | 38.9% | +1.19% | 3yr |
| Phase A (new signals) | 441 | 49.9% | +1.58% | 3yr |
| **Phase B (validated)** | **441** | **49.9%** | **+1.58%** | **3yr** |

### Phase B Changes
- Walk-forward validated: 6/6 windows positive
- Train/test confirmed: no overfitting (test outperforms train)
- DRY: `backtest_service.py` 4h detection → shared `detect_signals()`
- volume_spike permanently removed (tested 5 variants, all negative in system)
- Exit routing validated: train-based ≈ full-data (0.61% diff)
