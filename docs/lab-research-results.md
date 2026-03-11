# Strategy Lab Research Results (2026-03-10)

Data: 277 signals, 3 years (Mar 2023 — Mar 2026), 30 symbols, 4h timeframe.

---

## Lab 2: Counter Strategy Hard Stop Optimization

### Finding: Widen hard stop from 8% to 12%

| Counter HS | N | WR | EV | PF | Hold |
|-----------|---|-----|------|-----|------|
| 6% | 128 | 38.3% | +1.20% | 1.4x | 4.8d |
| **8% (current)** | **128** | **45.3%** | **+2.31%** | **1.7x** | **6.7d** |
| 10% | 128 | 50.0% | +2.77% | 1.8x | 7.7d |
| **12%** | **128** | **53.9%** | **+3.44%** | **1.9x** | **8.8d** |
| 15% | 128 | 56.2% | +3.65% | 2.0x | 9.9d |
| 20% | 128 | 60.2% | +4.59% | 2.3x | 10.7d |

Full strategy impact:
| Config | N | WR | EV | PF |
|--------|---|-----|------|-----|
| HS=8% (baseline) | 274 | 40.5% | +1.29% | 1.6x |
| **HS=12%** | **274** | **44.5%** | **+1.82%** | **1.8x** |
| HS=15% | 274 | 45.6% | +1.91% | 1.8x |

### Trailing BE / Trail Stop: Hurts EV

Adding trailing protection to counter signals increases WR but kills big winners:
- Counter trail trig=3/dd=2: WR 71.1%, EV +2.89% (counter only) — looks good
- But full strategy: EV +1.42% (worse than simple HS=12 → +1.82%)
- Reason: counter's edge is in +6-21% counter/timeout exits. Trail locks in +2-6% too early.

### Overextension: Still negative, marginal with fixed 3/2
- Trail: EV -0.34%
- Fixed 3/2: EV +0.14% (best)
- Fixed 2/1: EV +0.07%
- No exit strategy saves it. All < +0.15% EV.

### Overheat: counter_be looks promising but N=34
- counter_be (trig=3/lock=0.5): WR 76%, EV +1.29% — but tiny sample

---

## Lab 3: Advanced Analytics

### Per-Symbol Analysis

**Top symbols (clean signals):**
| Symbol | N | WR | EV | PF |
|--------|---|-----|------|-----|
| SEIUSDT | 4 | 75.0% | +15.12% | 45.0x |
| SUIUSDT | 21 | 61.9% | +8.44% | 4.1x |
| ADAUSDT | 13 | 46.2% | +4.62% | 2.8x |
| XRPUSDT | 15 | 60.0% | +4.04% | 4.1x |
| LINKUSDT | 6 | 50.0% | +3.60% | 2.4x |

**Noise symbols (EV < -1%):**
OPUSDT (-5.19%), NEARUSDT (-4.97%), ATOMUSDT (-2.08%), DOTUSDT (-1.37%), BNBUSDT (-1.19%)

**Impact of excluding worst 5 symbols:**
- Without worst: N=224, WR 47.3%, EV +2.74%, PF 2.3x
- All symbols:   N=274, WR 44.5%, EV +1.82%, PF 1.8x
- Delta: +0.92% EV, but risky (overfitting to past symbol performance)

### Trend × Direction (Momentum vs Contrarian)

| Direction + Trend | N | WR | EV |
|-------------------|---|-----|------|
| Long + uptrend | 84 | 52% | +2.51% |
| Long + downtrend | 9 | 33% | +3.85% |
| Short + uptrend | 100 | 41% | +0.30% |
| Short + downtrend | 57 | 42% | +2.96% |

**Momentum filter (long↑, short↓, any neutral):**
N=165, WR 47.3%, EV +2.63%, PF 1.9x — **strong improvement over baseline +1.82%**

### Signal Clustering

| Day density | N | WR | EV |
|-------------|---|-----|------|
| Exactly 1 signal/day | 130 | 46.2% | +3.07% |
| Exactly 2 signals/day | 92 | 47.8% | +0.91% |
| 3+ signals/day | 52 | 34.6% | +0.27% |
| Solo (no same-sym ±3d) | 202 | 46.5% | +2.33% |
| Clustered (same sym ±3d) | 72 | 38.9% | +0.37% |

**Key insight:** Isolated signals >> clustered signals. Busy days dilute quality.

### Signal Pairs (same symbol within 3 days)

**Pair-confirmed trades are WORSE:** N=42, WR 33.3%, EV -0.52%
**Solo trades are better:** N=232, WR 46.6%, EV +2.24%

Signal combinations do NOT confirm each other — they indicate noise/volatility.

### Fund_Z Bands (strongest single filter)

| Band | N | WR | EV | PF |
|------|---|-----|------|-----|
| fund_z < -1 | 23 | 47.8% | +3.45% | 2.4x |
| **fund_z -1..0** | **48** | **60.4%** | **+6.95%** | **3.9x** |
| fund_z 0..1 | 103 | 45.6% | +0.82% | 1.3x |
| fund_z 1..2 | 25 | 44.0% | +0.39% | 1.2x |
| fund_z 2+ | 75 | 32.0% | -0.13% | 0.9x |

**Massive insight:** fund_z -1..0 (slightly negative funding) is the sweet spot. EV +6.95%!
High funding (fund_z 2+) = negative EV. Makes sense — crowded trade.

### Quarterly Seasonality

| Quarter | N | WR | EV | PF |
|---------|---|-----|------|-----|
| Q1 | 93 | 37.6% | +0.47% | 1.2x |
| Q2 | 28 | 53.6% | +5.11% | 3.9x |
| **Q3** | **42** | **66.7%** | **+6.38%** | **3.9x** |
| Q4 | 111 | 39.6% | +0.38% | 1.2x |

Strong seasonality. Q2+Q3 massively outperform Q1+Q4. But sample is biased (only 3 years).

### Best Combined Filter (Grid Search)

**Winner: momentum + quality≥2 + pvs<30**
N=74, WR 51.4%, EV +3.81%, PF 3.4x

But N=74 may be overfitted. More robust alternatives:
- momentum + any quality + pvs<30: N=140, WR 50.0%, EV +2.96%, PF 2.1x
- momentum + any quality + any pvs: N=165, WR 47.3%, EV +2.63%, PF 1.9x
- no filter + any quality + pvs<30: N=232, WR 45.7%, EV +2.03%, PF 1.9x

---

## Updated Strategy Profiles (Post-Research)

### Profile 1: STABLE (recommended for production)
- Routing: Adaptive with HS=12% for counter signals
- Exclude: fund_spike, overextension, overheat
- Filter: momentum (long↑, short↓, neutral=any)
- Expected: N≈100-120/yr, WR ~47%, EV ~+2.5%, PF ~1.9x

### Profile 2: TURBO (max signal count)
- Routing: Adaptive with HS=12%
- Exclude: fund_spike only
- Filter: none (or momentum for conservative turbo)
- Expected: N≈180-200/yr, WR ~44%, EV ~+1.8%, PF ~1.8x

### Profile 3: CONSERVATIVE (max WR, min risk)
- Routing: Top 6 signals only, HS=12% for counter
- Filter: momentum + fund_z < 1.0
- Expected: N≈50-80/yr, WR ~55%, EV ~+3%, PF ~2.5x

---

## Key Actionable Changes

1. **HARD_STOP_PCT 8→12 for counter signals** — +0.53% EV, biggest single improvement
2. **Momentum filter** (long only in uptrend, short only in downtrend) — +0.8% EV
3. **fund_z < 1 filter** — high funding = crowded = bad signal (fund_z 2+ has negative EV)
4. **Avoid clustered signals** — solo signals EV +2.33% vs clustered +0.37%
5. **Symbol blacklist consideration** — 5 symbols consistently negative, but risky to act on

## What NOT to Do

- Signal combinations/pairs don't help — they indicate noise, not confirmation
- Trailing BE on counter — kills big winners despite improving WR
- Overextension rescue — no exit strategy makes it profitable
- Quality score filter — no clear edge (Q=0 and Q=2 both good)
