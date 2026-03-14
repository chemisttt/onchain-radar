"""Shared signal detection conditions — single source of truth.

Used by:
  - scripts/setup_backtest.py (backtest)
  - services/backtest_service.py (historical simulation)
  - services/market_analyzer.py (live alerts)

Any threshold change here propagates to all consumers.
"""

from dataclasses import dataclass

# ─── Thresholds ────────────────────────────────────────────────────────

# Z-score classification
Z_MODERATE = 2.0
Z_STRONG = 3.0

# Signal triggers
OI_Z_OVERHEAT = 1.5
FUND_Z_OVERHEAT = 0.8
OI_CHG_1D_SQUEEZE = 5       # % OI change for div_squeeze_1d
PRICE_CHG_1D_SQUEEZE = -2   # % price change
FUND_Z_SQUEEZE_1D_MIN = 0   # fund_z floor for 1d squeeze
OI_CHG_3D_SQUEEZE = 5
PRICE_CHG_3D_SQUEEZE = -2
FUND_Z_SQUEEZE_3D_MIN = -1.0
OI_CHG_1D_TOP = -3          # OI drop for div_top
PRICE_CHG_1D_TOP = 2        # price rise
PRICE_CHG_3D_DIST = 1.5     # distribution price threshold
PRICE_VS_SMA_OVEREXT_LO = 8
PRICE_VS_SMA_OVEREXT_HI = 15
FUND_Z_OVEREXT = 0.5
OI_CHG_3D_STALL = 4
PRICE_CHG_3D_STALL = 2      # abs threshold
FUND_Z_STALL = 0.3
OI_Z_CAPITULATION = -1.5
FUND_Z_CAPITULATION = -0.8
LIQ_SHORT_Z_SQUEEZE = 3.0
PRICE_CHG_SQUEEZE_LO = 3
PRICE_CHG_SQUEEZE_HI = 8
OI_CHG_SQUEEZE_CAP = 20
FUND_Z_SQUEEZE_CAP = 1.5
FUND_Z_REVERSAL = 1.5
FUND_DELTA_REVERSAL = 0.0005
FUND_Z_MEAN_REVERT = 1.5
FUND_Z_SUSTAINED = 1.0

# New signal thresholds (Phase A)
MOMENTUM_DIV_PRICE_CHG = 3       # % price change 5d
MOMENTUM_DIV_MOM_THRESH = 20     # composite momentum threshold
    # volume_spike constants removed — signal permanently disabled
LIQ_RATIO_Z = 2.5                # liquidation z-score threshold
LIQ_RATIO_PRICE_CHG = 1          # % price change 1d
FUND_SPIKE_Z = 1.5               # fund z-score for fund_spike
FUND_SPIKE_MOMENTUM = 3          # price momentum % for fund_spike

# Confluence
CONFLUENCE_SIGNAL = 4
ALT_MIN_CONFLUENCE = 5


@dataclass
class SignalInput:
    """Normalized input for signal detection — abstracts daily/4h/live."""
    oi_z: float
    fund_z: float
    liq_z: float
    vol_z: float
    price_chg: float       # 1-bar % change
    oi_chg: float           # 1-bar % change
    price_chg_3d: float
    oi_chg_3d: float
    price_vs_sma: float
    trend: str              # "up" / "down" / "neutral"
    funding_rate: float
    liq_long_z: float = 0.0
    liq_short_z: float = 0.0
    price_momentum: float = 0.0  # 5d price change %
    z_accel: float = 0.0
    vol_declining_3d: bool = False
    fund_delta_3d: float = 0.0   # funding_rate - funding_rate[i-3]
    has_fund_delta: bool = False  # whether fund_delta is available (i >= 3)
    fund_z_sustained_high: bool = False  # fund_z > 1.0 for last 3 bars
    fund_z_sustained_low: bool = False   # fund_z < -1.0 for last 3 bars
    momentum_value: float = 0.0      # composite momentum (-100..+100)
    relative_volume: float = 0.0     # relative volume (ratio to median)
    price_chg_5d: float = 0.0       # 5d price change %
    # For confluence only (optional):
    rv_regime: str | None = None


def detect_signals(inp: SignalInput) -> list[tuple[str, str]]:
    """Check all signal conditions. Returns [(signal_type, direction), ...]."""
    triggered: list[tuple[str, str]] = []

    # === SHORT ===
    if inp.oi_z > OI_Z_OVERHEAT and inp.fund_z > FUND_Z_OVERHEAT and inp.trend != "up":
        triggered.append(("overheat", "short"))

    if inp.oi_chg > OI_CHG_1D_SQUEEZE and inp.price_chg < PRICE_CHG_1D_SQUEEZE and inp.fund_z > FUND_Z_SQUEEZE_1D_MIN and inp.trend != "down":
        triggered.append(("div_squeeze_1d", "short"))

    if inp.oi_chg_3d > OI_CHG_3D_SQUEEZE and inp.price_chg_3d < PRICE_CHG_3D_SQUEEZE and inp.fund_z > FUND_Z_SQUEEZE_3D_MIN:
        triggered.append(("div_squeeze_3d", "short"))

    if inp.oi_chg < OI_CHG_1D_TOP and inp.price_chg > PRICE_CHG_1D_TOP and inp.trend != "up":
        triggered.append(("div_top_1d", "short"))

    if inp.price_chg_3d > PRICE_CHG_3D_DIST and inp.vol_declining_3d and inp.trend == "up":
        triggered.append(("distribution", "short"))

    if PRICE_VS_SMA_OVEREXT_LO < inp.price_vs_sma < PRICE_VS_SMA_OVEREXT_HI and inp.fund_z > FUND_Z_OVEREXT:
        triggered.append(("overextension", "short"))

    if inp.oi_chg_3d > OI_CHG_3D_STALL and abs(inp.price_chg_3d) < PRICE_CHG_3D_STALL and inp.fund_z > FUND_Z_STALL:
        triggered.append(("oi_buildup_stall", "short"))

    # === LONG ===
    if inp.oi_z < OI_Z_CAPITULATION and inp.fund_z < FUND_Z_CAPITULATION and inp.trend != "down":
        triggered.append(("capitulation", "long"))

    if inp.vol_z > Z_MODERATE and inp.oi_chg < -3 and abs(inp.price_chg) > 2 and inp.trend != "up":
        d = "long" if inp.price_chg < 0 else "short"
        triggered.append(("vol_divergence", d))

    if inp.liq_short_z > LIQ_SHORT_Z_SQUEEZE and PRICE_CHG_SQUEEZE_LO < inp.price_chg < PRICE_CHG_SQUEEZE_HI and inp.oi_chg < OI_CHG_SQUEEZE_CAP and inp.fund_z < FUND_Z_SQUEEZE_CAP and inp.trend != "down":
        triggered.append(("liq_short_squeeze", "long"))

    # Fund reversal (requires 3-bar lookback)
    if inp.has_fund_delta:
        if inp.fund_z > FUND_Z_REVERSAL and inp.fund_delta_3d < -FUND_DELTA_REVERSAL:
            triggered.append(("fund_reversal", "short"))
        if inp.fund_z < -FUND_Z_REVERSAL and inp.fund_delta_3d > FUND_DELTA_REVERSAL:
            triggered.append(("fund_reversal", "long"))

    # Fund mean reversion (sustained extreme funding → reversal)
    if inp.fund_z_sustained_high and inp.fund_z > FUND_Z_MEAN_REVERT and inp.trend != "up":
        triggered.append(("fund_mean_revert", "short"))
    if inp.fund_z_sustained_low and inp.fund_z < -FUND_Z_MEAN_REVERT and inp.trend != "down":
        triggered.append(("fund_mean_revert", "long"))

    # === NEW SIGNALS (Phase A) ===

    # Momentum divergence: price vs composite momentum disagree → reversal
    if inp.price_chg_5d > MOMENTUM_DIV_PRICE_CHG and inp.momentum_value < -MOMENTUM_DIV_MOM_THRESH:
        triggered.append(("momentum_divergence", "short"))
    if inp.price_chg_5d < -MOMENTUM_DIV_PRICE_CHG and inp.momentum_value > MOMENTUM_DIV_MOM_THRESH:
        triggered.append(("momentum_divergence", "long"))

    # Volume spike: PERMANENTLY REMOVED
    # Tested all variants (original, trend-aligned, high-OI, 2-bar confirm):
    # - Isolation: counter_sig shows +1.47%, but in full system it's -2.2%
    # - Poisons counter-signal cache, steals daily cap slots from better signals
    # - Full system EV drops from +1.58% to -0.51% when enabled
    # Decision: do NOT re-enable. Conditions are too loose to generate quality signals.

    # Liq ratio extreme: skewed liquidations → pressure
    if inp.liq_long_z > LIQ_RATIO_Z and inp.liq_short_z < 1.0 and inp.price_chg < -LIQ_RATIO_PRICE_CHG:
        triggered.append(("liq_ratio_extreme", "long"))
    if inp.liq_short_z > LIQ_RATIO_Z and inp.liq_long_z < 1.0 and inp.price_chg > LIQ_RATIO_PRICE_CHG:
        triggered.append(("liq_ratio_extreme", "short"))

    # Fund spike: extreme funding + price momentum → reversal (rehabilitated)
    if inp.fund_z > FUND_SPIKE_Z and inp.price_momentum > FUND_SPIKE_MOMENTUM:
        triggered.append(("fund_spike", "short"))

    return triggered


def compute_confluence(inp: SignalInput, direction: str) -> tuple[int, list[str]]:
    """Confluence scoring for backtest (setup_backtest + backtest_service).

    Live system (market_analyzer) uses its own confluence with OB/velocity/proximity.
    """
    score = 0
    factors = []

    if abs(inp.oi_z) > Z_STRONG:
        score += 2; factors.append(f"OI_z extreme ({inp.oi_z:+.1f})")
    elif abs(inp.oi_z) > Z_MODERATE:
        score += 1; factors.append(f"OI_z elevated ({inp.oi_z:+.1f})")

    if abs(inp.fund_z) > Z_STRONG:
        score += 2; factors.append(f"Fund_z extreme ({inp.fund_z:+.1f})")
    elif abs(inp.fund_z) > Z_MODERATE:
        score += 1; factors.append(f"Fund_z elevated ({inp.fund_z:+.1f})")

    if abs(inp.liq_z) > Z_MODERATE:
        score += 1; factors.append(f"Liq_z ({inp.liq_z:+.1f})")
    if abs(inp.vol_z) > Z_MODERATE:
        score += 1; factors.append(f"Vol_z ({inp.vol_z:+.1f})")
    if abs(inp.price_momentum) > 5:
        score += 1; factors.append(f"Price 5d {inp.price_momentum:+.1f}%")
    if abs(inp.z_accel) > 1.0:
        score += 1; factors.append(f"Z-accel {inp.z_accel:+.1f}")
    if inp.liq_long_z > Z_MODERATE or inp.liq_short_z > Z_MODERATE:
        score += 1
        side = "longs" if inp.liq_long_z > inp.liq_short_z else "shorts"
        factors.append(f"Liq {side} spike")

    if inp.rv_regime in ("low", "high"):
        score += 1; factors.append(f"RV {inp.rv_regime}")

    if direction == "short" and inp.funding_rate > 0:
        score += 1; factors.append("Fund confirms short")
    elif direction == "long" and inp.funding_rate < 0:
        score += 1; factors.append("Fund confirms long")

    if inp.trend != "neutral":
        if (direction == "long" and inp.trend == "up") or (direction == "short" and inp.trend == "down"):
            score += 1; factors.append(f"Trend aligned ({inp.trend})")
        else:
            score -= 1; factors.append(f"Counter-trend ({inp.trend})")

    if direction == "long" and inp.price_momentum < -5:
        if sum([abs(inp.oi_z) > 2.0, abs(inp.liq_z) > 2.0, abs(inp.vol_z) > 2.0]) >= 2:
            score -= 2; factors.append("Crash penalty")
    if direction == "short" and inp.price_momentum > 5:
        if sum([abs(inp.oi_z) > 2.0, abs(inp.liq_z) > 2.0, abs(inp.vol_z) > 2.0]) >= 2:
            score -= 2; factors.append("Crash penalty")

    return score, factors
