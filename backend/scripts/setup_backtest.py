#!/usr/bin/env python3
"""Setup Backtest — Bar-by-bar exit strategy comparison.

Compares 6 exit strategies on the same signal set:
  A: Fixed TP/SL (baseline)
  B: Z-Score Mean Reversion
  C: Counter-Signal
  D: Trailing ATR
  E: Hybrid (B + D + C)
  F: Adaptive (per signal type routing)

Two modes:
  --daily  Signal detection AND exit simulation on daily bars (default)
  --4h     Signal detection on daily, exit simulation on 4h OHLCV bars (6x resolution)

Usage:
  cd backend && python3 scripts/setup_backtest.py                # daily, all data
  cd backend && python3 scripts/setup_backtest.py --4h           # 4h exit, all data
  cd backend && python3 scripts/setup_backtest.py --4h --days 1100  # 4h, ~3 years
"""

import asyncio
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import init_db, get_db
from services.signal_conditions import SignalInput, detect_signals, compute_confluence

# ─── Mode ────────────────────────────────────────────────────────────────
USE_4H_EXIT = "--4h" in sys.argv
BARS_PER_DAY = 6 if USE_4H_EXIT else 1

# ─── Constants ───────────────────────────────────────────────────────────
MIN_POINTS = 30
Z_WINDOW = 365
SMA_PERIOD = 20
HARD_STOP_PCT = 8.0

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "TRXUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "NEARUSDT",
    "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "AAVEUSDT",
    "INJUSDT", "SUIUSDT", "TIAUSDT", "WIFUSDT", "JUPUSDT",
    "SEIUSDT", "TRUMPUSDT", "TONUSDT", "RENDERUSDT", "ENAUSDT",
]

TOP_OI_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "DOGEUSDT", "TRXUSDT", "UNIUSDT", "SUIUSDT", "ADAUSDT",
}
ALT_MIN_CONFLUENCE = 5
Z_MODERATE = 2.0
Z_STRONG = 3.0
CONFLUENCE_SIGNAL = 4
COOLDOWN_DAYS = 1
CLUSTER_GAP_DAYS = 2

# Signal → primary z-score for Strategy B
SIGNAL_PRIMARY_Z = {
    "overheat": "oi_z", "div_squeeze_1d": "oi_z", "div_squeeze_3d": "oi_z",
    "div_squeeze_5d": "oi_z", "div_top_1d": "oi_z", "div_top_3d": "oi_z",
    "oi_buildup_stall": "oi_z",
    "capitulation": "fund_z", "fund_spike": "fund_z", "fund_reversal": "fund_z",
    "liq_flush": "liq_z", "liq_flush_3d": "liq_z", "liq_short_squeeze": "liq_z",
    "vol_divergence": "vol_z",
    "distribution": "vol_z", "overextension": "oi_z",
    # Phase A new signals
    "momentum_divergence": "fund_z", "volume_spike": "vol_z",
    "liq_ratio_extreme": "liq_z",
}
ZSCORE_TP_THRESH = {"oi_z": 0.5, "fund_z": 0.3, "liq_z": 1.0, "vol_z": 0.5}
ZSCORE_SL_INCREASE = 1.0

COUNTER_SIGNALS = {
    "long": {"overheat", "fund_spike", "distribution", "overextension", "div_top_1d", "momentum_divergence", "volume_spike", "fund_reversal"},
    "short": {"capitulation", "liq_flush", "liq_short_squeeze", "vol_divergence", "momentum_divergence", "volume_spike", "liq_ratio_extreme", "fund_reversal"},
}

# ─── Data Structures ────────────────────────────────────────────────────

@dataclass
class Bar:
    """One daily bar with all pre-computed features."""
    date: str
    close: float
    high: float       # from 4h OHLCV aggregation, or close if unavailable
    low: float
    oi: float
    funding_rate: float
    liq_long: float
    liq_short: float
    liq_delta: float
    deriv_volume: float
    # Pre-computed
    oi_z: float = 0.0
    fund_z: float = 0.0
    liq_z: float = 0.0
    vol_z: float = 0.0
    liq_long_z: float = 0.0
    liq_short_z: float = 0.0
    price_chg: float = 0.0     # 1d
    oi_chg: float = 0.0
    price_chg_3d: float = 0.0
    oi_chg_3d: float = 0.0
    price_chg_5d: float = 0.0
    oi_chg_5d: float = 0.0
    price_momentum: float = 0.0  # 5d
    z_accel: float = 0.0
    price_vs_sma: float = 0.0
    trend: str = "neutral"
    vol_declining_3d: bool = False
    atr: float = 0.0
    momentum_value: float = 0.0
    relative_volume: float = 0.0


@dataclass
class ExitResult:
    exit_bar: int
    exit_price: float
    exit_reason: str
    pnl_pct: float          # gross PnL (before costs)
    bars_held: int
    max_drawdown_pct: float
    max_favorable_pct: float
    fees_pct: float = 0.0
    funding_cost_pct: float = 0.0
    net_pnl_pct: float = 0.0


@dataclass
class Signal:
    bar_idx: int
    signal_type: str
    direction: str
    entry_price: float
    confluence: int
    factors: list
    zscores: dict
    bar_idx_4h: int = -1  # set when USE_4H_EXIT, points into bars_4h


@dataclass
class Bar4h:
    """4h OHLCV bar for exit simulation. Carries daily z-scores for strategy compat."""
    ts: int
    date: str
    close: float
    high: float
    low: float
    atr: float = 0.0
    # Copied from corresponding daily bar (constant within a day):
    oi_z: float = 0.0
    fund_z: float = 0.0
    liq_z: float = 0.0
    vol_z: float = 0.0
    funding_rate: float = 0.0


# ─── Numpy Helpers ───────────────────────────────────────────────────────

def _rolling_zscore_np(arr: np.ndarray, window: int, min_pts: int = 30) -> np.ndarray:
    """Rolling z-score via cumsum. O(n)."""
    n = len(arr)
    out = np.zeros(n)
    cs = np.cumsum(arr)
    cs2 = np.cumsum(arr ** 2)
    for i in range(min_pts - 1, n):
        start = max(0, i - window + 1)
        cnt = i - start + 1
        if cnt < min_pts:
            continue
        s = cs[i] - (cs[start - 1] if start > 0 else 0)
        s2 = cs2[i] - (cs2[start - 1] if start > 0 else 0)
        mean = s / cnt
        var = s2 / cnt - mean * mean
        if var < 1e-20:
            continue
        out[i] = (arr[i] - mean) / var ** 0.5
    return out


def _rolling_sma_np(arr: np.ndarray, period: int) -> np.ndarray:
    n = len(arr)
    out = np.empty(n)
    cs = np.cumsum(arr)
    for i in range(n):
        if i < period - 1:
            out[i] = cs[i] / (i + 1)
        else:
            out[i] = (cs[i] - (cs[i - period] if i >= period else 0)) / period
    return out


def _rolling_atr_np(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(highs)
    prev_close = np.empty(n)
    prev_close[0] = closes[0]
    prev_close[1:] = closes[:-1]
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close)))
    atr = np.zeros(n)
    cs = np.cumsum(tr)
    for i in range(period - 1, n):
        atr[i] = (cs[i] - (cs[i - period] if i >= period else 0)) / period
    return atr


def _shift_pct(arr: np.ndarray, shift: int) -> np.ndarray:
    n = len(arr)
    out = np.zeros(n)
    if shift < n:
        prev = np.empty(n)
        prev[:shift] = arr[0]
        prev[shift:] = arr[:-shift]
        mask = prev > 0
        out[mask] = (arr[mask] - prev[mask]) / prev[mask] * 100
    return out


# ─── Step 1: Data Loader ────────────────────────────────────────────────

async def load_symbol_data(symbol: str) -> list[Bar]:
    """Load daily_derivatives + aggregated daily OHLCV from 4h candles."""
    db = get_db()

    rows = await db.execute_fetchall(
        """SELECT date, close_price, open_interest_usd, oi_binance_usd, funding_rate,
                  liquidations_long, liquidations_short, liquidations_delta, volume_usd
           FROM daily_derivatives WHERE symbol = ? ORDER BY date ASC""",
        (symbol,),
    )
    if not rows or len(rows) < MIN_POINTS + 10:
        return []

    # Load momentum data
    momentum_rows = await db.execute_fetchall(
        "SELECT date, momentum_value, relative_volume FROM daily_momentum WHERE symbol = ? ORDER BY date ASC",
        (symbol,),
    )
    momentum_by_date: dict[str, tuple[float, float]] = {}
    for r in momentum_rows:
        momentum_by_date[r["date"]] = (r["momentum_value"] or 0.0, r["relative_volume"] or 0.0)

    # Aggregate 4h candles to daily OHLCV
    ohlcv_rows = await db.execute_fetchall(
        "SELECT ts, open, high, low, close FROM ohlcv_4h WHERE symbol = ? ORDER BY ts ASC",
        (symbol,),
    )
    daily_ohlcv: dict[str, tuple[float, float]] = {}  # date → (high, low)
    for r in ohlcv_rows:
        ts = r["ts"] // 1000
        d = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        h, l = r["high"] or 0, r["low"] or 0
        if d in daily_ohlcv:
            prev_h, prev_l = daily_ohlcv[d]
            daily_ohlcv[d] = (max(prev_h, h), min(prev_l, l) if prev_l > 0 else l)
        else:
            daily_ohlcv[d] = (h, l)

    # Build bars
    bars: list[Bar] = []
    for r in rows:
        date = r["date"]
        close = r["close_price"] or 0
        if close <= 0:
            continue
        oi = (r["oi_binance_usd"] or 0) or (r["open_interest_usd"] or 0)
        ohlcv = daily_ohlcv.get(date)
        if ohlcv:
            high, low = ohlcv
            if high <= 0: high = close
            if low <= 0: low = close
        else:
            high, low = close, close  # no intra-day data — conservative

        mom = momentum_by_date.get(date, (0.0, 0.0))
        bars.append(Bar(
            date=date, close=close, high=high, low=low,
            oi=oi, funding_rate=r["funding_rate"] or 0,
            liq_long=r["liquidations_long"] or 0,
            liq_short=r["liquidations_short"] or 0,
            liq_delta=r["liquidations_delta"] or 0,
            deriv_volume=r["volume_usd"] or 0,
            momentum_value=mom[0], relative_volume=mom[1],
        ))

    if len(bars) < MIN_POINTS + 10:
        return []

    n = len(bars)

    # Vectorized computations
    prices = np.array([b.close for b in bars], dtype=np.float64)
    ois = np.array([b.oi for b in bars], dtype=np.float64)
    funds = np.array([b.funding_rate for b in bars], dtype=np.float64)
    liq_d = np.array([b.liq_delta for b in bars], dtype=np.float64)
    liq_l = np.array([b.liq_long for b in bars], dtype=np.float64)
    liq_s = np.array([b.liq_short for b in bars], dtype=np.float64)
    vols = np.array([b.deriv_volume for b in bars], dtype=np.float64)
    highs = np.array([b.high for b in bars], dtype=np.float64)
    lows = np.array([b.low for b in bars], dtype=np.float64)

    oi_z = _rolling_zscore_np(ois, Z_WINDOW)
    fund_z = _rolling_zscore_np(funds, Z_WINDOW)
    liq_z = _rolling_zscore_np(liq_d, Z_WINDOW)
    vol_z = _rolling_zscore_np(vols, Z_WINDOW)
    liq_long_z = _rolling_zscore_np(liq_l, Z_WINDOW)
    liq_short_z = _rolling_zscore_np(liq_s, Z_WINDOW)

    sma = _rolling_sma_np(prices, SMA_PERIOD)
    pvs = np.where(sma > 0, (prices - sma) / sma * 100, 0.0)
    atr = _rolling_atr_np(highs, lows, prices, 14)

    pc1 = _shift_pct(prices, 1)
    oc1 = _shift_pct(ois, 1)
    pc3 = _shift_pct(prices, 3)
    oc3 = _shift_pct(ois, 3)
    pc5 = _shift_pct(prices, 5)
    oc5 = _shift_pct(ois, 5)

    for i in range(n):
        b = bars[i]
        b.oi_z = float(oi_z[i])
        b.fund_z = float(fund_z[i])
        b.liq_z = float(liq_z[i])
        b.vol_z = float(vol_z[i])
        b.liq_long_z = float(liq_long_z[i])
        b.liq_short_z = float(liq_short_z[i])
        b.price_vs_sma = float(pvs[i])
        b.trend = "up" if pvs[i] > 2 else ("down" if pvs[i] < -2 else "neutral")
        b.atr = float(atr[i])
        b.price_chg = float(pc1[i])
        b.oi_chg = float(oc1[i])
        b.price_chg_3d = float(pc3[i])
        b.oi_chg_3d = float(oc3[i])
        b.price_chg_5d = float(pc5[i])
        b.oi_chg_5d = float(oc5[i])
        b.price_momentum = b.price_chg_5d
        if i >= 3:
            b.z_accel = float(oi_z[i] - oi_z[i - 3])
            b.vol_declining_3d = bool(vols[i] < vols[i - 1] < vols[i - 2])

    return bars


# ─── Step 1b: 4h Bar Loader (for exit simulation) ─────────────────────

async def load_4h_bars(symbol: str, daily_bars: list[Bar]) -> tuple[list[Bar4h], dict[str, int], dict[str, int]]:
    """Load 4h OHLCV, compute ATR, copy daily z-scores.

    Returns: (bars_4h, date_to_first_idx, date_to_last_idx)
    """
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT ts, open, high, low, close, volume FROM ohlcv_4h WHERE symbol = ? ORDER BY ts ASC",
        (symbol,),
    )
    if not rows:
        return [], {}, {}

    # Build date→daily_bar mapping
    daily_by_date: dict[str, Bar] = {b.date: b for b in daily_bars}

    # Build Bar4h list
    bars_4h: list[Bar4h] = []
    for r in rows:
        ts = r["ts"]
        date = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        c = r["close"] or 0
        h = r["high"] or c
        l = r["low"] or c
        if c <= 0:
            continue
        bars_4h.append(Bar4h(ts=ts, date=date, close=c, high=h, low=l))

    if len(bars_4h) < 20:
        return [], {}, {}

    # Compute ATR on 4h bars
    n = len(bars_4h)
    highs = np.array([b.high for b in bars_4h], dtype=np.float64)
    lows = np.array([b.low for b in bars_4h], dtype=np.float64)
    closes = np.array([b.close for b in bars_4h], dtype=np.float64)
    atr = _rolling_atr_np(highs, lows, closes, 14)

    for i in range(n):
        bars_4h[i].atr = float(atr[i])

    # Copy z-scores + funding_rate from daily bars (constant within a day)
    for b4 in bars_4h:
        db_bar = daily_by_date.get(b4.date)
        if db_bar:
            b4.oi_z = db_bar.oi_z
            b4.fund_z = db_bar.fund_z
            b4.liq_z = db_bar.liq_z
            b4.vol_z = db_bar.vol_z
            b4.funding_rate = db_bar.funding_rate

    # Build date→index mappings
    date_first: dict[str, int] = {}
    date_last: dict[str, int] = {}
    for i, b in enumerate(bars_4h):
        if b.date not in date_first:
            date_first[b.date] = i
        date_last[b.date] = i

    return bars_4h, date_first, date_last


# ─── Step 2: Signal Generator ───────────────────────────────────────────

def _bar_to_signal_input(bars: list[Bar], i: int) -> SignalInput:
    """Adapter: convert daily Bar at index i to shared SignalInput."""
    b = bars[i]
    has_fd = i >= 3
    fd = (b.funding_rate - bars[i - 3].funding_rate) if has_fd else 0.0
    return SignalInput(
        oi_z=b.oi_z, fund_z=b.fund_z, liq_z=b.liq_z, vol_z=b.vol_z,
        price_chg=b.price_chg, oi_chg=b.oi_chg,
        price_chg_3d=b.price_chg_3d, oi_chg_3d=b.oi_chg_3d,
        price_vs_sma=b.price_vs_sma, trend=b.trend,
        funding_rate=b.funding_rate,
        liq_long_z=b.liq_long_z, liq_short_z=b.liq_short_z,
        price_momentum=b.price_momentum, z_accel=b.z_accel,
        vol_declining_3d=b.vol_declining_3d,
        fund_delta_3d=fd, has_fund_delta=has_fd,
        momentum_value=b.momentum_value, relative_volume=b.relative_volume,
        price_chg_5d=b.price_chg_5d,
    )


def detect_signals_at_bar(bars: list[Bar], i: int) -> list[tuple[str, str]]:
    """Check daily signal conditions via shared module."""
    return detect_signals(_bar_to_signal_input(bars, i))


def _compute_confluence_at_bar(bars: list[Bar], i: int, direction: str) -> tuple[int, list[str]]:
    """Compute confluence via shared module."""
    return compute_confluence(_bar_to_signal_input(bars, i), direction)


def detect_all_signals(bars: list[Bar], symbol: str, days: int = 365) -> list[Signal]:
    total = len(bars)
    warmup_end = max(MIN_POINTS, total - days)

    raw: list[Signal] = []
    cooldowns: dict[str, int] = {}

    for i in range(warmup_end, total):
        if bars[i].close <= 0:
            continue

        triggered = detect_signals_at_bar(bars, i)
        for sig_type, direction in triggered:
            # Momentum filter: skip signals against the trend
            # Counter-trend signals exempt: distribution, momentum_divergence, fund_spike
            trend = bars[i].trend
            if sig_type not in ("distribution", "momentum_divergence", "fund_spike"):
                if direction == "long" and trend == "down":
                    continue
                if direction == "short" and trend == "up":
                    continue

            confluence, factors = _compute_confluence_at_bar(bars, i, direction)
            if confluence < CONFLUENCE_SIGNAL:
                continue
            if symbol not in TOP_OI_SYMBOLS and confluence < ALT_MIN_CONFLUENCE:
                continue

            cd_key = f"{sig_type}:{symbol}"
            if cd_key in cooldowns and (i - cooldowns[cd_key]) < COOLDOWN_DAYS:
                continue
            cooldowns[cd_key] = i

            raw.append(Signal(
                bar_idx=i, signal_type=sig_type, direction=direction,
                entry_price=bars[i].close, confluence=confluence,
                factors=factors[:5],
                zscores={"oi_z": bars[i].oi_z, "fund_z": bars[i].fund_z,
                         "liq_z": bars[i].liq_z, "vol_z": bars[i].vol_z},
            ))

    # Cluster
    if len(raw) <= 1:
        return raw
    raw.sort(key=lambda s: s.bar_idx)
    clustered: list[Signal] = []
    for sig in raw:
        merged = False
        for existing in clustered:
            if existing.direction != sig.direction:
                continue
            if abs(sig.bar_idx - existing.bar_idx) <= CLUSTER_GAP_DAYS:
                if sig.confluence > existing.confluence:
                    clustered[clustered.index(existing)] = sig
                merged = True
                break
        if not merged:
            clustered.append(sig)

    # Per-day cap (top-3 by confluence)
    from collections import defaultdict as dd
    by_date: dict[str, list[Signal]] = dd(list)
    for s in clustered:
        by_date[bars[s.bar_idx].date].append(s)
    capped: list[Signal] = []
    for day, sigs in by_date.items():
        sigs.sort(key=lambda x: -x.confluence)
        capped.extend(sigs[:3])
    return capped


# ─── Cost Model ────────────────────────────────────────────────────────

ROUND_TRIP_FEE_PCT = 0.07   # Binance maker+taker round-trip
SETTLEMENTS_PER_BAR = 3 / BARS_PER_DAY  # daily=3, 4h=0.5


def _apply_costs(result: ExitResult, bars, entry_idx: int, direction: str):
    """Compute fees + funding cost and set net_pnl on ExitResult."""
    result.fees_pct = ROUND_TRIP_FEE_PCT

    # Sum funding rates over holding period
    funding_sum = 0.0
    end = min(result.exit_bar + 1, len(bars))
    for j in range(entry_idx + 1, end):
        funding_sum += getattr(bars[j], 'funding_rate', 0.0)

    # Long pays positive funding, short pays negative
    sign = 1.0 if direction == "long" else -1.0
    result.funding_cost_pct = sign * funding_sum * SETTLEMENTS_PER_BAR * 100

    result.net_pnl_pct = result.pnl_pct - result.fees_pct - result.funding_cost_pct


# ─── Step 3: Exit Strategies ────────────────────────────────────────────

def _walk_pnl(bars: list[Bar], entry_idx: int, direction: str, j: int) -> float:
    ep = bars[entry_idx].close
    cp = bars[j].close
    return ((cp - ep) / ep * 100) if direction == "long" else ((ep - cp) / ep * 100)


def _fav_adv(bars: list[Bar], entry_idx: int, direction: str, j: int) -> tuple[float, float]:
    ep = bars[entry_idx].close
    h, l = bars[j].high, bars[j].low
    if direction == "long":
        return (h - ep) / ep * 100, (ep - l) / ep * 100
    return (ep - l) / ep * 100, (h - ep) / ep * 100


def strategy_fixed(bars, signal, _cache=None, tp_pct=5.0, sl_pct=3.0, timeout=7, hard_stop=HARD_STOP_PCT):
    """Strategy A: Fixed TP/SL. Timeout = 7 days (daily bars)."""
    ei = signal.bar_idx
    ep = signal.entry_price
    d = signal.direction
    mf, ma = 0.0, 0.0

    for j in range(ei + 1, min(ei + timeout + 1, len(bars))):
        fav, adv = _fav_adv(bars, ei, d, j)
        mf, ma = max(mf, fav), max(ma, adv)

        if adv >= hard_stop:
            return ExitResult(j, bars[j].close, "hard_stop", -hard_stop, j - ei, ma, mf)
        if adv >= sl_pct:
            return ExitResult(j, bars[j].close, "sl", -sl_pct, j - ei, ma, mf)
        if fav >= tp_pct:
            return ExitResult(j, bars[j].close, "tp", tp_pct, j - ei, ma, mf)

    last = min(ei + timeout, len(bars) - 1)
    pnl = _walk_pnl(bars, ei, d, last)
    return ExitResult(last, bars[last].close, "timeout", pnl, last - ei, ma, mf)


def strategy_zscore(bars, signal, _cache=None, max_hold=30):
    """Strategy B: Z-Score Mean Reversion. Max hold 30 days."""
    ei = signal.bar_idx
    ep = signal.entry_price
    d = signal.direction
    pk = SIGNAL_PRIMARY_Z.get(signal.signal_type, "oi_z")
    tp_t = ZSCORE_TP_THRESH.get(pk, 0.5)
    entry_z = signal.zscores.get(pk, 0.0)
    mf, ma = 0.0, 0.0

    for j in range(ei + 1, min(ei + max_hold + 1, len(bars))):
        fav, adv = _fav_adv(bars, ei, d, j)
        mf, ma = max(mf, fav), max(ma, adv)

        if adv >= HARD_STOP_PCT:
            return ExitResult(j, bars[j].close, "hard_stop", -HARD_STOP_PCT, j - ei, ma, mf)

        cur_z = getattr(bars[j], pk, 0.0)
        if abs(cur_z) < tp_t:
            pnl = _walk_pnl(bars, ei, d, j)
            return ExitResult(j, bars[j].close, "zscore_tp", pnl, j - ei, ma, mf)
        if abs(cur_z) > abs(entry_z) + ZSCORE_SL_INCREASE:
            pnl = _walk_pnl(bars, ei, d, j)
            return ExitResult(j, bars[j].close, "zscore_sl", pnl, j - ei, ma, mf)

    last = min(ei + max_hold, len(bars) - 1)
    pnl = _walk_pnl(bars, ei, d, last)
    return ExitResult(last, bars[last].close, "timeout", pnl, last - ei, ma, mf)


def strategy_counter(bars, signal, cache=None, max_hold=30, hard_stop=HARD_STOP_PCT):
    """Strategy C: Exit on counter-signal. Max hold 30 days."""
    ei = signal.bar_idx
    ep = signal.entry_price
    d = signal.direction
    cs = COUNTER_SIGNALS.get(d, set())
    if cache is None:
        cache = {}
    mf, ma = 0.0, 0.0

    for j in range(ei + 1, min(ei + max_hold + 1, len(bars))):
        fav, adv = _fav_adv(bars, ei, d, j)
        mf, ma = max(mf, fav), max(ma, adv)

        if adv >= hard_stop:
            return ExitResult(j, bars[j].close, "hard_stop", -hard_stop, j - ei, ma, mf)

        triggered = cache.get(j, ())
        for st, sd in triggered:
            if st in cs:
                pnl = _walk_pnl(bars, ei, d, j)
                return ExitResult(j, bars[j].close, "counter", pnl, j - ei, ma, mf)

    last = min(ei + max_hold, len(bars) - 1)
    pnl = _walk_pnl(bars, ei, d, last)
    return ExitResult(last, bars[last].close, "timeout", pnl, last - ei, ma, mf)


def strategy_trailing(bars, signal, _cache=None, atr_mult=1.5, be_pct=2.0, max_hold=30):
    """Strategy D: Trailing ATR. Max hold 30 days.

    Order of operations per bar: check stop (old level) → update trail for next bar.
    This avoids the pessimistic scenario of tightening stop and triggering it in same bar.
    """
    ei = signal.bar_idx
    ep = signal.entry_price
    d = signal.direction
    ia = bars[ei].atr
    if ia <= 0:
        ia = ep * 0.02

    if d == "long":
        stop = ep - atr_mult * ia
        best = ep
    else:
        stop = ep + atr_mult * ia
        best = ep

    be = False
    mf, ma = 0.0, 0.0

    for j in range(ei + 1, min(ei + max_hold + 1, len(bars))):
        h, l = bars[j].high, bars[j].low
        ca = bars[j].atr if bars[j].atr > 0 else ia

        if d == "long":
            fav = (h - ep) / ep * 100
            adv = (ep - l) / ep * 100
            # 1. Check stop with PREVIOUS bar's stop level
            if l <= stop:
                pnl = (stop - ep) / ep * 100
                return ExitResult(j, stop, "trail", pnl, j - ei, max(ma, adv), max(mf, fav))
            # 2. Update trail for NEXT bar
            if h > best:
                best = h
                stop = max(stop, best - atr_mult * ca)
            if not be and fav >= be_pct:
                stop = max(stop, ep)
                be = True
        else:
            fav = (ep - l) / ep * 100
            adv = (h - ep) / ep * 100
            # 1. Check stop first
            if h >= stop:
                pnl = (ep - stop) / ep * 100
                return ExitResult(j, stop, "trail", pnl, j - ei, max(ma, adv), max(mf, fav))
            # 2. Update trail for next bar
            if l < best:
                best = l
                stop = min(stop, best + atr_mult * ca)
            if not be and fav >= be_pct:
                stop = min(stop, ep)
                be = True

        mf, ma = max(mf, fav), max(ma, adv)

    last = min(ei + max_hold, len(bars) - 1)
    pnl = _walk_pnl(bars, ei, d, last)
    return ExitResult(last, bars[last].close, "timeout", pnl, last - ei, ma, mf)


def strategy_hybrid(bars, signal, cache=None, max_hold=30):
    """Strategy E: First of Z-score TP, trailing ATR, counter-signal, hard stop."""
    ei = signal.bar_idx
    ep = signal.entry_price
    d = signal.direction
    if cache is None:
        cache = {}

    pk = SIGNAL_PRIMARY_Z.get(signal.signal_type, "oi_z")
    tp_t = ZSCORE_TP_THRESH.get(pk, 0.5)
    counter_set = COUNTER_SIGNALS.get(d, set())

    ia = bars[ei].atr
    if ia <= 0:
        ia = ep * 0.02
    am = 1.5

    if d == "long":
        stop = ep - am * ia
        best = ep
    else:
        stop = ep + am * ia
        best = ep

    be = False
    mf, ma = 0.0, 0.0

    for j in range(ei + 1, min(ei + max_hold + 1, len(bars))):
        b = bars[j]
        h, l = b.high, b.low
        ca = b.atr if b.atr > 0 else ia

        if d == "long":
            fav = (h - ep) / ep * 100
            adv = (ep - l) / ep * 100
        else:
            fav = (ep - l) / ep * 100
            adv = (h - ep) / ep * 100
        mf, ma = max(mf, fav), max(ma, adv)

        # 1. Hard stop
        if adv >= HARD_STOP_PCT:
            return ExitResult(j, b.close, "hard_stop", -HARD_STOP_PCT, j - ei, ma, mf)

        # 2. Trailing ATR — check stop FIRST, then update
        if d == "long":
            if l <= stop:
                pnl = (stop - ep) / ep * 100
                return ExitResult(j, stop, "trail", pnl, j - ei, ma, mf)
            if h > best:
                best = h
                stop = max(stop, best - am * ca)
            if not be and fav >= 2.0:
                stop = max(stop, ep)
                be = True
        else:
            if h >= stop:
                pnl = (ep - stop) / ep * 100
                return ExitResult(j, stop, "trail", pnl, j - ei, ma, mf)
            if l < best:
                best = l
                stop = min(stop, best + am * ca)
            if not be and fav >= 2.0:
                stop = min(stop, ep)
                be = True

        # 3. Z-score TP
        cur_z = getattr(b, pk, 0.0)
        if abs(cur_z) < tp_t:
            pnl = _walk_pnl(bars, ei, d, j)
            return ExitResult(j, b.close, "zscore_tp", pnl, j - ei, ma, mf)

        # 4. Counter-signal
        triggered = cache.get(j, ())
        for st, sd in triggered:
            if st in counter_set:
                pnl = _walk_pnl(bars, ei, d, j)
                return ExitResult(j, b.close, "counter", pnl, j - ei, ma, mf)

    last = min(ei + max_hold, len(bars) - 1)
    pnl = _walk_pnl(bars, ei, d, last)
    return ExitResult(last, bars[last].close, "timeout", pnl, last - ei, ma, mf)


# ─── Strategy F: Trail + Counter overlay ─────────────────────────────────

def strategy_trail_counter(bars, signal, cache=None, atr_mult=1.5, be_pct=2.0, max_hold=30, hard_stop=HARD_STOP_PCT):
    """Trail ATR as primary + counter-signal as early exit."""
    ei = signal.bar_idx
    ep = signal.entry_price
    d = signal.direction
    cs = COUNTER_SIGNALS.get(d, set())
    if cache is None:
        cache = {}

    ia = bars[ei].atr
    if ia <= 0:
        ia = ep * 0.02

    if d == "long":
        stop = ep - atr_mult * ia
        best = ep
    else:
        stop = ep + atr_mult * ia
        best = ep

    be = False
    mf, ma = 0.0, 0.0

    for j in range(ei + 1, min(ei + max_hold + 1, len(bars))):
        b = bars[j]
        h, l = b.high, b.low
        ca = b.atr if b.atr > 0 else ia

        if d == "long":
            fav = (h - ep) / ep * 100
            adv = (ep - l) / ep * 100
            # Check stop first, then update
            if l <= stop:
                pnl = (stop - ep) / ep * 100
                return ExitResult(j, stop, "trail", pnl, j - ei, max(ma, adv), max(mf, fav))
            if h > best:
                best = h
                stop = max(stop, best - atr_mult * ca)
            if not be and fav >= be_pct:
                stop = max(stop, ep)
                be = True
        else:
            fav = (ep - l) / ep * 100
            adv = (h - ep) / ep * 100
            if h >= stop:
                pnl = (ep - stop) / ep * 100
                return ExitResult(j, stop, "trail", pnl, j - ei, max(ma, adv), max(mf, fav))
            if l < best:
                best = l
                stop = min(stop, best + atr_mult * ca)
            if not be and fav >= be_pct:
                stop = min(stop, ep)
                be = True

        mf, ma = max(mf, fav), max(ma, adv)

        # Hard stop
        if adv >= hard_stop:
            return ExitResult(j, b.close, "hard_stop", -hard_stop, j - ei, ma, mf)

        # Counter-signal early exit
        triggered = cache.get(j, ())
        for st, sd in triggered:
            if st in cs:
                pnl = _walk_pnl(bars, ei, d, j)
                return ExitResult(j, b.close, "counter", pnl, j - ei, ma, mf)

    last = min(ei + max_hold, len(bars) - 1)
    pnl = _walk_pnl(bars, ei, d, last)
    return ExitResult(last, bars[last].close, "timeout", pnl, last - ei, ma, mf)


# ─── Strategy F: Adaptive exit per signal type ──────────────────────────

ADAPTIVE_EXIT = {
    # Best per signal type from 3-year 4h backtest (2026-03-11, 441 signals, EV +1.46%)
    # Counter-signal — best for divergence & squeeze signals
    "liq_short_squeeze": "counter_sig",  # +3.54% EV, WR 56%
    "div_squeeze_3d":    "counter_sig",  # +2.11% EV, WR 50%
    "div_top_1d":        "counter_sig",  # +1.04% EV, WR 44%
    "distribution":      "fixed",        # +1.85% EV, WR 61%
    "oi_buildup_stall":  "fixed",        # +0.98% EV, WR 50%
    "vol_divergence":    "counter_sig",  # +3.76% EV, WR 100% (N=3)
    # Trail ATR — best for momentum/trend signals
    "overheat":          "fixed",        # +0.15% EV, WR 39%
    "overextension":     "trail_atr",    # default
    # Mean-reversion → Z-Score MR
    "fund_reversal":     "zscore_mr",    # +3.60% EV, WR 50%
    "capitulation":      "zscore_mr",
    # Phase A new signals (2026-03-11)
    "momentum_divergence": "counter_sig", # +1.72% EV, WR 57%, N=82
    "liq_ratio_extreme":   "counter_sig", # +1.51% EV, WR 56%, N=82
    "fund_spike":          "trail_atr",   # +0.37% EV, WR 33%, N=95 (rehabilitated)
    "div_squeeze_1d":      "hybrid",      # mixed — hybrid safest
    # volume_spike: PERMANENTLY REMOVED (poisons system: -0.51% vs +1.58% without)
}

def _adaptive_dispatch():
    mh = 30 * BARS_PER_DAY
    return {
        "trail_atr":     lambda bars, sig, cache: strategy_trailing(bars, sig, max_hold=mh),
        "zscore_mr":     lambda bars, sig, cache: strategy_zscore(bars, sig, max_hold=mh),
        "counter_sig":   lambda bars, sig, cache: strategy_counter(bars, sig, cache, max_hold=mh, hard_stop=12.0),
        "trail_counter": lambda bars, sig, cache: strategy_trail_counter(bars, sig, cache, max_hold=mh, hard_stop=12.0),
        "hybrid":        lambda bars, sig, cache: strategy_hybrid(bars, sig, cache, max_hold=mh),
        "fixed":         lambda bars, sig, cache: strategy_fixed(bars, sig, timeout=7 * BARS_PER_DAY),
    }

_ADAPTIVE_DISPATCH = _adaptive_dispatch()


def strategy_adaptive(bars, signal, cache=None):
    """Strategy F: Route each signal type to its best exit strategy."""
    exit_type = ADAPTIVE_EXIT.get(signal.signal_type, "trail_atr")
    fn = _ADAPTIVE_DISPATCH[exit_type]
    return fn(bars, signal, cache)


# ─── Step 4: Runner + Output ────────────────────────────────────────────

def _build_strategies():
    mh = 30 * BARS_PER_DAY
    return {
        "A: Fixed 5/3": lambda b, s, c: strategy_fixed(b, s, timeout=7 * BARS_PER_DAY),
        "B: Z-Score MR": lambda b, s, c: strategy_zscore(b, s, max_hold=mh),
        "C: Counter-Sig": lambda b, s, c: strategy_counter(b, s, c, max_hold=mh),
        "D: Trail ATR": lambda b, s, c: strategy_trailing(b, s, max_hold=mh),
        "E: Hybrid": lambda b, s, c: strategy_hybrid(b, s, c, max_hold=mh),
        "F: Adaptive": lambda b, s, c: strategy_adaptive(b, s, c),
    }

STRATEGIES = _build_strategies()


def _compute_stats(results: list[ExitResult]) -> dict:
    if not results:
        return {}
    wins = [r for r in results if r.pnl_pct > 0]
    losses = [r for r in results if r.pnl_pct <= 0]
    total_pnl = sum(r.pnl_pct for r in results)
    gp = sum(r.pnl_pct for r in wins)
    gl = abs(sum(r.pnl_pct for r in losses))
    avg_hold_bars = sum(r.bars_held for r in results) / len(results)
    max_dd = max((r.max_drawdown_pct for r in results), default=0)
    pf = (gp / gl) if gl > 0 else float("inf")

    # Net stats (after fees + funding)
    net_wins = [r for r in results if r.net_pnl_pct > 0]
    net_total = sum(r.net_pnl_pct for r in results)
    net_gp = sum(r.net_pnl_pct for r in net_wins)
    net_gl = abs(sum(r.net_pnl_pct for r in results if r.net_pnl_pct <= 0))
    net_pf = (net_gp / net_gl) if net_gl > 0 else float("inf")
    avg_fees = sum(r.fees_pct for r in results) / len(results)
    avg_funding = sum(r.funding_cost_pct for r in results) / len(results)

    # Drawdown analysis (equity curve based on net PnL)
    equity = 0.0
    peak = 0.0
    max_equity_dd = 0.0
    max_consec_losses = 0
    cur_consec = 0
    dd_start = 0
    longest_dd_trades = 0
    cur_dd_trades = 0
    recovery_trades = []  # list of trades to recover from each DD

    for i, r in enumerate(results):
        equity += r.net_pnl_pct
        if equity > peak:
            if cur_dd_trades > 0:
                recovery_trades.append(cur_dd_trades)
            peak = equity
            cur_dd_trades = 0
        else:
            cur_dd_trades += 1
            dd = peak - equity
            if dd > max_equity_dd:
                max_equity_dd = dd
            longest_dd_trades = max(longest_dd_trades, cur_dd_trades)

        if r.net_pnl_pct <= 0:
            cur_consec += 1
            max_consec_losses = max(max_consec_losses, cur_consec)
        else:
            cur_consec = 0

    avg_recovery = sum(recovery_trades) / len(recovery_trades) if recovery_trades else 0

    return {
        "trades": len(results), "wins": len(wins), "losses": len(losses),
        "wr": len(wins) / len(results) * 100,
        "ev": total_pnl / len(results),
        "total_pnl": total_pnl,
        "avg_hold_d": avg_hold_bars / BARS_PER_DAY,
        "max_dd": max_dd, "pf": pf,
        # Net (after costs)
        "net_wr": len(net_wins) / len(results) * 100,
        "net_ev": net_total / len(results),
        "net_total_pnl": net_total,
        "net_pf": net_pf,
        "avg_fees": avg_fees,
        "avg_funding": avg_funding,
        # Drawdown
        "max_equity_dd": max_equity_dd,
        "max_consec_losses": max_consec_losses,
        "longest_dd_trades": longest_dd_trades,
        "avg_recovery": avg_recovery,
    }


async def run_comparison(symbols: list[str] | None = None, days: int = 365, split: str = "all"):
    if symbols is None:
        symbols = SYMBOLS

    mode_label = "4h exit" if USE_4H_EXIT else "daily"
    split_label = f", split={split}" if split != "all" else ""
    print(f"  Mode: {mode_label} (BARS_PER_DAY={BARS_PER_DAY}){split_label}\n")

    # --- tuple format: (signal, exit_bars, symbol) where exit_bars = bars_4h or daily bars ---
    all_signals: list[tuple[Signal, list, str]] = []
    daily_bars_by_sym: dict[str, list[Bar]] = {}  # cache for signal cache phase
    total_bars = 0
    skipped_4h = 0
    t0 = time.time()

    for idx, sym in enumerate(symbols):
        daily_bars = await load_symbol_data(sym)
        if not daily_bars:
            continue
        total_bars += len(daily_bars)
        daily_bars_by_sym[sym] = daily_bars

        signals = detect_all_signals(daily_bars, sym, days=days)
        if not signals:
            continue

        # Train/test split filter
        if split == "train":
            signals = [s for s in signals if daily_bars[s.bar_idx].date < TRAIN_TEST_SPLIT]
        elif split == "test":
            signals = [s for s in signals if daily_bars[s.bar_idx].date >= TRAIN_TEST_SPLIT]
        if not signals:
            continue

        if USE_4H_EXIT:
            bars_4h, date_first, date_last = await load_4h_bars(sym, daily_bars)
            if not bars_4h:
                skipped_4h += len(signals)
                continue

            # Remap signals: bar_idx stays (for daily ref), bar_idx_4h = last 4h bar of signal date
            valid_signals = []
            for sig in signals:
                sig_date = daily_bars[sig.bar_idx].date
                idx_4h = date_last.get(sig_date, -1)
                if idx_4h < 0:
                    skipped_4h += 1
                    continue
                sig.bar_idx_4h = idx_4h
                # Update entry_price to match 4h close (should be ~same as daily close)
                sig.entry_price = bars_4h[idx_4h].close
                valid_signals.append(sig)

            if valid_signals:
                print(f"  [{idx+1}/{len(symbols)}] {sym}: {len(daily_bars)}d + {len(bars_4h)} 4h bars, "
                      f"{len(valid_signals)} signals", flush=True)

            for sig in valid_signals:
                all_signals.append((sig, bars_4h, sym))
        else:
            if signals:
                print(f"  [{idx+1}/{len(symbols)}] {sym}: {len(daily_bars)} bars, {len(signals)} signals", flush=True)
            for sig in signals:
                all_signals.append((sig, daily_bars, sym))

    print(f"\n  Data loaded in {time.time() - t0:.1f}s")
    if skipped_4h:
        print(f"  Skipped {skipped_4h} signals (no 4h data for their dates)")

    # Global daily cap — use daily bars date
    GLOBAL_DAILY_CAP = 5
    by_day: dict[str, list[tuple[Signal, list, str]]] = defaultdict(list)
    for sig, exit_bars, sym in all_signals:
        if USE_4H_EXIT:
            day = exit_bars[sig.bar_idx_4h].date
        else:
            day = exit_bars[sig.bar_idx].date
        by_day[day].append((sig, exit_bars, sym))
    capped: list[tuple[Signal, list, str]] = []
    for day, ds in by_day.items():
        ds.sort(key=lambda x: -x[0].confluence)
        capped.extend(ds[:GLOBAL_DAILY_CAP])
    all_signals = capped

    print(f"  Total signals: {len(all_signals)} ({len(symbols)} symbols, {total_bars} daily bars)\n")

    if not all_signals:
        print("  No signals found. Exiting.")
        return

    # Pre-compute signal cache per exit_bars set
    t1 = time.time()
    bars_by_id: dict[int, list] = {}
    signal_cache: dict[int, dict[int, list[tuple[str, str]]]] = {}

    if USE_4H_EXIT:
        # For 4h: build counter-signal cache by mapping daily signals → 4h indices
        for sig, exit_bars, sym in all_signals:
            bid = id(exit_bars)
            if bid in bars_by_id:
                continue
            bars_by_id[bid] = exit_bars

            daily_bars = daily_bars_by_sym.get(sym, [])
            if not daily_bars:
                signal_cache[bid] = {}
                continue

            # Build daily signal cache: date → signals
            daily_sigs_by_date: dict[str, list[tuple[str, str]]] = {}
            for i in range(len(daily_bars)):
                t = detect_signals_at_bar(daily_bars, i)
                if t:
                    daily_sigs_by_date[daily_bars[i].date] = t

            # Map to 4h indices: all 4h bars of a date get the same signals
            cache_4h: dict[int, list[tuple[str, str]]] = {}
            for i, b4 in enumerate(exit_bars):
                sigs = daily_sigs_by_date.get(b4.date)
                if sigs:
                    cache_4h[i] = sigs
            signal_cache[bid] = cache_4h
    else:
        for sig, bars, sym in all_signals:
            bid = id(bars)
            if bid not in bars_by_id:
                bars_by_id[bid] = bars
                cache: dict[int, list[tuple[str, str]]] = {}
                for i in range(len(bars)):
                    t = detect_signals_at_bar(bars, i)
                    if t:
                        cache[i] = t
                signal_cache[bid] = cache

    print(f"  Signal cache: {time.time() - t1:.1f}s")

    # Run strategies
    t2 = time.time()
    strat_results: dict[str, list[ExitResult]] = {n: [] for n in STRATEGIES}
    sig_strat: dict[str, dict[str, list[ExitResult]]] = defaultdict(lambda: {n: [] for n in STRATEGIES})

    for sig, exit_bars, sym in all_signals:
        cache = signal_cache.get(id(exit_bars), {})

        # For 4h mode: temporarily swap bar_idx to point to 4h entry
        orig_idx = sig.bar_idx
        if USE_4H_EXIT:
            sig.bar_idx = sig.bar_idx_4h

        for sn, sf in STRATEGIES.items():
            r = sf(exit_bars, sig, cache)
            if r:
                _apply_costs(r, exit_bars, sig.bar_idx, sig.direction)
                strat_results[sn].append(r)
                sig_strat[sig.signal_type][sn].append(r)

        # Restore daily bar_idx
        if USE_4H_EXIT:
            sig.bar_idx = orig_idx

    print(f"  Simulation: {time.time() - t2:.1f}s\n")

    # ═══════════════════════════════════════════════════════════════
    #  Overall comparison
    # ═══════════════════════════════════════════════════════════════
    print("=" * 110)
    print(f"  EXIT STRATEGY COMPARISON ({len(all_signals)} signals, {len(symbols)} symbols, {mode_label})")
    print("=" * 110)
    print(f"  {'Strategy':<16} {'WR':>6} {'GrossEV':>9} {'NetEV':>9} {'AvgFee':>7} {'AvgFund':>8} {'Hold':>6} {'GrossPF':>8} {'NetPF':>7} {'N':>5}")
    print("  " + "-" * 104)

    for name in STRATEGIES:
        s = _compute_stats(strat_results[name])
        if not s:
            continue
        print(f"  {name:<16} {s['wr']:>5.1f}% {s['ev']:>+8.2f}% {s['net_ev']:>+8.2f}% "
              f"{s['avg_fees']:>6.2f}% {s['avg_funding']:>+7.3f}% "
              f"{s['avg_hold_d']:>5.1f}d {s['pf']:>7.2f}x {s['net_pf']:>6.2f}x {s['trades']:>5}")

    # ═══════════════════════════════════════════════════════════════
    #  Per signal type × strategy
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 120)
    print("  PER SIGNAL TYPE x STRATEGY (WR / GrossEV → NetEV)")
    print("=" * 120)
    sn_list = ["Fixed", "ZScore", "Counter", "Trail", "Hybrid", "Adaptive"]
    hdr = f"  {'Signal':<22} {'N':>4}"
    for sn in sn_list:
        hdr += f"  {sn:>17}"
    print(hdr)
    print("  " + "-" * 114)

    for st in sorted(sig_strat.keys(), key=lambda t: -len(sig_strat[t].get("A: Fixed 5/3", []))):
        sd = sig_strat[st]
        n = len(sd.get("A: Fixed 5/3", []))
        if n < 2:
            continue
        row = f"  {st:<22} {n:>4}"
        for sname in STRATEGIES:
            rs = sd.get(sname, [])
            if not rs:
                row += f"  {'---':>17}"
                continue
            stats = _compute_stats(rs)
            row += f"  {stats['wr']:>3.0f}/{stats['ev']:>+5.1f}→{stats['net_ev']:>+5.1f}"
        print(row)

    # ═══════════════════════════════════════════════════════════════
    #  Exit reason distribution (Hybrid)
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 78)
    print("  EXIT REASON DISTRIBUTION (Strategy E: Hybrid)")
    print("=" * 78)

    hybrid = strat_results.get("E: Hybrid", [])
    if hybrid:
        rc: dict[str, int] = defaultdict(int)
        rp: dict[str, float] = defaultdict(float)
        rw: dict[str, int] = defaultdict(int)
        for r in hybrid:
            rc[r.exit_reason] += 1
            rp[r.exit_reason] += r.pnl_pct
            if r.pnl_pct > 0:
                rw[r.exit_reason] += 1

        total = len(hybrid)
        labels = {
            "zscore_tp": "z-scores normalized", "trail": "trailing ATR",
            "counter": "counter-signal", "hard_stop": "-8% hard stop",
            "timeout": "max hold expired", "tp": "fixed TP", "sl": "fixed SL",
            "zscore_sl": "z-score worsened",
        }
        print(f"  {'Reason':<14} {'Count':>6} {'%':>6}  {'AvgPnL':>8} {'WR':>6}  Description")
        print("  " + "-" * 72)
        for reason, count in sorted(rc.items(), key=lambda x: -x[1]):
            pct = count / total * 100
            avg = rp[reason] / count
            wr = rw[reason] / count * 100
            print(f"  {reason:<14} {count:>6} {pct:>5.1f}%  {avg:>+7.2f}% {wr:>5.1f}%  {labels.get(reason, reason)}")

    # ═══════════════════════════════════════════════════════════════
    #  Best strategy per signal type
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 90)
    print("  BEST STRATEGY PER SIGNAL TYPE (by Net EV)")
    print("=" * 90)
    print(f"  {'Signal':<22} {'N':>4} {'Best':<16} {'WR':>6} {'GrossEV':>9} {'NetEV':>9} {'Hold':>6}")
    print("  " + "-" * 84)

    for st in sorted(sig_strat.keys(), key=lambda t: -len(sig_strat[t].get("A: Fixed 5/3", []))):
        sd = sig_strat[st]
        n = len(sd.get("A: Fixed 5/3", []))
        if n < 3:
            continue
        best_n, best_ev, best_s = "", -999.0, {}
        for sname in STRATEGIES:
            rs = sd.get(sname, [])
            if not rs:
                continue
            stats = _compute_stats(rs)
            if stats.get("net_ev", -999) > best_ev:
                best_ev = stats["net_ev"]
                best_n = sname
                best_s = stats
        if best_s:
            print(f"  {st:<22} {n:>4} {best_n:<16} {best_s['wr']:>5.1f}% "
                  f"{best_s['ev']:>+8.2f}% {best_s['net_ev']:>+8.2f}% {best_s['avg_hold_d']:>5.1f}d")

    # ═══════════════════════════════════════════════════════════════
    #  Drawdown Analysis (if --drawdown flag)
    # ═══════════════════════════════════════════════════════════════
    if "--drawdown" in sys.argv:
        print()
        print("=" * 90)
        print("  DRAWDOWN ANALYSIS (net PnL equity curve)")
        print("=" * 90)
        print(f"  {'Strategy':<16} {'MaxDD':>8} {'ConsecL':>8} {'LongDD':>8} {'AvgRecov':>9} {'NetEV':>8}")
        print("  " + "-" * 84)

        for name in STRATEGIES:
            s = _compute_stats(strat_results[name])
            if not s:
                continue
            print(f"  {name:<16} {s['max_equity_dd']:>+7.1f}% {s['max_consec_losses']:>8} "
                  f"{s['longest_dd_trades']:>8} {s['avg_recovery']:>8.1f} {s['net_ev']:>+7.2f}%")

    print()


TRAIN_TEST_SPLIT = "2025-01-01"  # signals before → train, from → test


def _parse_days() -> int:
    """Parse --days N from CLI args. Default 0 = all available data."""
    for i, arg in enumerate(sys.argv):
        if arg == "--days" and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                pass
    return 0


def _parse_split() -> str:
    """Parse --train / --test from CLI args. Returns 'train', 'test', or 'all'."""
    if "--train" in sys.argv:
        return "train"
    if "--test" in sys.argv:
        return "test"
    return "all"


async def main():
    await init_db()
    days = _parse_days()
    if days <= 0:
        days = 9999
    split = _parse_split()
    await run_comparison(days=days, split=split)


if __name__ == "__main__":
    asyncio.run(main())
