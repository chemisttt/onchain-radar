#!/usr/bin/env python3
"""
Backtest alert system on historical data.

Tests 7 per-symbol alert types + regime shift on ~500 days of daily data.
Mirrors market_analyzer.py logic using only historical DB tables:
  daily_derivatives, derivatives_zscores, daily_volatility, daily_rv

Usage:
  python scripts/backtest_alerts.py                    # full backtest
  python scripts/backtest_alerts.py --symbol BTCUSDT   # one symbol
  python scripts/backtest_alerts.py --z-moderate 1.5   # what-if thresholds
  python scripts/backtest_alerts.py --csv results.csv  # export raw
  python scripts/backtest_alerts.py -v                 # print every alert
"""

import argparse
import csv
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

FORWARD_WINDOWS = [1, 3, 7]

ALERT_TYPES = [
    "OVERHEAT", "CAPITULATION", "DIVERGENCE_SQUEEZE", "DIVERGENCE_TOP",
    "LIQ_FLUSH", "VOLUME_ANOMALY", "VOL_COMPRESSION", "REGIME_SHIFT",
]

REGIME_THRESHOLDS = [
    (-2.0, "Deep Oversold"),
    (-1.0, "Oversold"),
    (0.0,  "Neutral Cool"),
    (1.0,  "Neutral Hot"),
    (2.0,  "Overbought"),
]
REGIME_EXTREME = "Extreme"


# ── Helpers ──────────────────────────────────────────────────────────────────

def regime_label(z):
    for threshold, label in REGIME_THRESHOLDS:
        if z <= threshold:
            return label
    return REGIME_EXTREME


def safe(val, default=0.0):
    return val if val is not None else default


# ── Data Loading ─────────────────────────────────────────────────────────────

def load_data(db_path, symbol_filter=None, from_date=None, to_date=None):
    """
    Single SQL JOIN across 4 tables.
    Returns: rows_by_symbol {symbol: [row_dicts sorted by date]},
             price_index {(symbol, date): price}
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conditions = []
    params = []
    if symbol_filter:
        conditions.append("z.symbol = ?")
        params.append(symbol_filter)
    if from_date:
        conditions.append("z.date >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("z.date <= ?")
        params.append(to_date)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    query = f"""
    SELECT
        z.symbol, z.date,
        z.oi_zscore, z.funding_zscore, z.liq_zscore, z.volume_zscore,
        z.oi_change_24h_pct, z.price_change_24h_pct,
        d.close_price, d.open_interest_usd, d.funding_rate,
        d.liquidations_long, d.liquidations_short, d.volume_usd,
        v.iv_30d, v.rv_30d AS vol_rv_30d, v.skew_25d, v.skew_25d_zscore,
        v.vrp, v.vrp_zscore,
        rv.rv_30d AS all_rv_30d
    FROM derivatives_zscores z
    LEFT JOIN daily_derivatives d ON z.symbol = d.symbol AND z.date = d.date
    LEFT JOIN daily_volatility v ON z.symbol = v.symbol AND z.date = v.date
    LEFT JOIN daily_rv rv ON z.symbol = rv.symbol AND z.date = rv.date
    {where}
    ORDER BY z.symbol, z.date
    """

    rows = conn.execute(query, params).fetchall()
    conn.close()

    rows_by_symbol = defaultdict(list)
    price_index = {}

    for r in rows:
        row = dict(r)
        rows_by_symbol[row["symbol"]].append(row)
        price = row.get("close_price")
        if price is not None:
            price_index[(row["symbol"], row["date"])] = price

    return dict(rows_by_symbol), price_index


# ── Confluence Scoring (0-7) ─────────────────────────────────────────────────

def compute_confluence(row, z_mod=2.0):
    """
    Simplified confluence without OB, momentum, liq proximity, velocity.
    Max score = 7:
      OI z-score:      +2 (>3.0) or +1 (>z_mod)
      Funding z-score: +2 (>3.0) or +1 (>z_mod)
      Liq z-score:     +1 (>z_mod)
      Volume z-score:  +1 (>z_mod)
      OI/price diverg: +1
    """
    score = 0
    oi_z = safe(row.get("oi_zscore"))
    fund_z = safe(row.get("funding_zscore"))
    liq_z = safe(row.get("liq_zscore"))
    vol_z = safe(row.get("volume_zscore"))
    oi_chg = safe(row.get("oi_change_24h_pct"))
    price_chg = safe(row.get("price_change_24h_pct"))

    # OI z-score
    if abs(oi_z) > 3.0:
        score += 2
    elif abs(oi_z) > z_mod:
        score += 1

    # Funding z-score
    if abs(fund_z) > 3.0:
        score += 2
    elif abs(fund_z) > z_mod:
        score += 1

    # Liq z-score
    if abs(liq_z) > z_mod:
        score += 1

    # Volume z-score
    if abs(vol_z) > z_mod:
        score += 1

    # OI/price divergence
    if (oi_chg > 5 and price_chg < -1) or (oi_chg < -5 and price_chg > 2):
        score += 1

    return min(score, 7)


# ── Alert Detection (per row) ───────────────────────────────────────────────

def check_alerts_for_day(row, z_mod, oi_div, price_div, liq_price, liq_oi):
    """
    Check 7 alert types on one data row.
    Returns list of (type, expected_direction, confluence).
    Mirrors market_analyzer.py check_alerts() logic.
    """
    alerts = []

    oi_z = safe(row.get("oi_zscore"))
    fund_z = safe(row.get("funding_zscore"))
    liq_z = safe(row.get("liq_zscore"))
    vol_z = safe(row.get("volume_zscore"))
    oi_chg = safe(row.get("oi_change_24h_pct"))
    price_chg = safe(row.get("price_change_24h_pct"))

    confl = compute_confluence(row, z_mod)

    # 1. OVERHEAT — longs overextended
    if oi_z > z_mod and fund_z > z_mod:
        alerts.append(("OVERHEAT", "down", confl))

    # 2. CAPITULATION — weak hands flushed
    if oi_z < -z_mod and fund_z < -z_mod:
        alerts.append(("CAPITULATION", "up", confl))

    # 3. DIVERGENCE_SQUEEZE — OI up, price down → trend continuation (shorts accumulating)
    if oi_chg > oi_div and price_chg < -price_div:
        alerts.append(("DIVERGENCE_SQUEEZE", "down", confl))

    # 4. DIVERGENCE_TOP — OI down, price up → rally on short covering
    if oi_chg < -oi_div and price_chg > price_div * 2:
        alerts.append(("DIVERGENCE_TOP", "down", confl))

    # 5. LIQ_FLUSH — cascade liquidations
    if liq_z > z_mod and price_chg < -liq_price and oi_chg < -liq_oi:
        alerts.append(("LIQ_FLUSH", "up", confl))

    # 6. VOLUME_ANOMALY — vol spike with OI/funding confirmation + confluence filter
    if vol_z > z_mod and (abs(oi_z) > 1.5 or abs(fund_z) > 1.5) and confl >= 3:
        direction = "up" if price_chg > 0 else "down"
        alerts.append(("VOLUME_ANOMALY", direction, confl))

    # 7. VOL_COMPRESSION — low IV → breakout imminent (BTC/ETH only)
    # RV never drops below 47% in crypto, use IV-only as forward-looking indicator
    iv = row.get("iv_30d")
    if iv is not None and iv < 37:
        alerts.append(("VOL_COMPRESSION", "either", max(confl, 4)))

    return alerts


# ── Regime Shift Detection ──────────────────────────────────────────────────

def check_regime_shift(rows_by_date, date, prev_date):
    """
    Macro alert: composite z-score of top-10 symbols by OI.
    Fires when regime label changes.
    """
    cur_rows = rows_by_date.get(date, [])
    prev_rows = rows_by_date.get(prev_date, [])

    if len(cur_rows) < 5 or len(prev_rows) < 5:
        return None

    def composite_avg(rows):
        top = sorted(rows, key=lambda r: safe(r.get("open_interest_usd")), reverse=True)[:10]
        vals = []
        for r in top:
            z = (safe(r.get("oi_zscore")) + safe(r.get("funding_zscore")) + safe(r.get("liq_zscore"))) / 3
            vals.append(z)
        return sum(vals) / len(vals) if vals else 0

    cur_z = composite_avg(cur_rows)
    prev_z = composite_avg(prev_rows)

    cur_lbl = regime_label(cur_z)
    prev_lbl = regime_label(prev_z)

    if cur_lbl == prev_lbl:
        return None

    # Mirror prod: only alert on transitions involving extreme zones
    extreme_keywords = ("Deep Oversold", REGIME_EXTREME)
    if not any(kw in cur_lbl or kw in prev_lbl for kw in extreme_keywords):
        return None

    if cur_lbl == "Deep Oversold":
        direction = "up"
    elif cur_lbl == REGIME_EXTREME:
        direction = "down"
    elif cur_z < prev_z:
        direction = "up"
    else:
        direction = "down"

    return ("REGIME_SHIFT", direction, 6, f"{prev_lbl} -> {cur_lbl}")


# ── Forward Return ──────────────────────────────────────────────────────────

def compute_forward_return(price_index, symbol, dates, start_idx, window):
    """Price change +N trading days from alert date."""
    end_idx = start_idx + window
    if end_idx >= len(dates):
        return None
    p0 = price_index.get((symbol, dates[start_idx]))
    p1 = price_index.get((symbol, dates[end_idx]))
    if p0 and p1 and p0 > 0:
        return (p1 - p0) / p0 * 100
    return None


def is_hit(direction, ret):
    """Check if alert direction prediction was correct."""
    if ret is None:
        return None
    if direction == "down":
        return ret < 0
    if direction == "up":
        return ret > 0
    if direction == "either":
        return abs(ret) > 2.0
    if direction == "directional":
        return abs(ret) > 1.0
    return None


# ── Main Backtest Loop ──────────────────────────────────────────────────────

def run_backtest(rows_by_symbol, price_index, args):
    """Day by day, symbol by symbol — collect all alert fires + forward returns."""
    results = []

    z_mod = args.z_moderate
    oi_div = args.oi_div_pct
    price_div = args.price_div_pct
    liq_price = args.liq_price_pct
    liq_oi = args.liq_oi_pct
    verbose = args.verbose

    # Build rows_by_date for regime shift
    rows_by_date = defaultdict(list)
    all_dates_set = set()
    for symbol, rows in rows_by_symbol.items():
        for r in rows:
            rows_by_date[r["date"]].append(r)
            all_dates_set.add(r["date"])

    sorted_all_dates = sorted(all_dates_set)

    # ── Per-symbol alerts ──
    for symbol, rows in sorted(rows_by_symbol.items()):
        dates = [r["date"] for r in rows]
        for i, row in enumerate(rows):
            alerts = check_alerts_for_day(row, z_mod, oi_div, price_div, liq_price, liq_oi)
            for alert_type, direction, confl in alerts:
                result = {
                    "date": row["date"],
                    "symbol": symbol,
                    "type": alert_type,
                    "direction": direction,
                    "confluence": confl,
                }
                for w in FORWARD_WINDOWS:
                    ret = compute_forward_return(price_index, symbol, dates, i, w)
                    result[f"ret_{w}d"] = ret
                    result[f"hit_{w}d"] = is_hit(direction, ret)
                results.append(result)

                if verbose:
                    r1 = fmt_ret(result.get("ret_1d"))
                    r3 = fmt_ret(result.get("ret_3d"))
                    r7 = fmt_ret(result.get("ret_7d"))
                    print(
                        f"  {row['date']}  {symbol:12s}  {alert_type:20s}  "
                        f"dir={direction:5s}  confl={confl}  "
                        f"ret1d={r1:>7s}  ret3d={r3:>7s}  ret7d={r7:>7s}"
                    )

    # ── Regime shift alerts ──
    btc_rows = rows_by_symbol.get("BTCUSDT", [])
    btc_dates = [r["date"] for r in btc_rows]

    for i in range(1, len(sorted_all_dates)):
        date = sorted_all_dates[i]
        prev_date = sorted_all_dates[i - 1]

        shift = check_regime_shift(rows_by_date, date, prev_date)
        if shift is None:
            continue

        alert_type, direction, confl, label = shift
        result = {
            "date": date,
            "symbol": "MARKET",
            "type": alert_type,
            "direction": direction,
            "confluence": confl,
            "label": label,
        }

        # Track forward returns via BTC
        if date in btc_dates:
            idx = btc_dates.index(date)
            for w in FORWARD_WINDOWS:
                ret = compute_forward_return(price_index, "BTCUSDT", btc_dates, idx, w)
                result[f"ret_{w}d"] = ret
                result[f"hit_{w}d"] = is_hit(direction, ret)

        results.append(result)

        if verbose:
            r1 = fmt_ret(result.get("ret_1d"))
            r3 = fmt_ret(result.get("ret_3d"))
            print(f"  {date}  {'MARKET':12s}  REGIME_SHIFT          {label}  ret1d={r1}  ret3d={r3}")

    return results


# ── Statistics ───────────────────────────────────────────────────────────────

def compute_stats(results):
    """Group results by alert type, compute hit rates and avg returns."""
    by_type = defaultdict(list)
    for r in results:
        by_type[r["type"]].append(r)

    stats = {}
    for atype in ALERT_TYPES:
        rows = by_type.get(atype, [])
        if not rows:
            stats[atype] = None
            continue

        fires = len(rows)
        avg_confl = sum(r["confluence"] for r in rows) / fires

        s = {"fires": fires, "avg_confl": avg_confl}

        for w in FORWARD_WINDOWS:
            rets = [r[f"ret_{w}d"] for r in rows if r.get(f"ret_{w}d") is not None]
            hits = [r[f"hit_{w}d"] for r in rows if r.get(f"hit_{w}d") is not None]

            total_hits = sum(1 for h in hits if h)
            hit_rate = total_hits / len(hits) * 100 if hits else 0
            avg_ret = sum(rets) / len(rets) if rets else 0
            med_ret = sorted(rets)[len(rets) // 2] if rets else 0
            best = max(rets) if rets else 0
            worst = min(rets) if rets else 0

            s[f"hit_{w}d"] = hit_rate
            s[f"avg_{w}d"] = avg_ret
            s[f"med_{w}d"] = med_ret
            s[f"best_{w}d"] = best
            s[f"worst_{w}d"] = worst
            s[f"n_{w}d"] = len(rets)

        stats[atype] = s

    return stats


# ── Output ───────────────────────────────────────────────────────────────────

def fmt_ret(val):
    if val is None:
        return "  N/A"
    return f"{val:+.1f}%"


def fmt_pct(val, n=None):
    if val is None:
        return "   -"
    if n is not None and n == 0:
        return "   -"
    return f"{val:.1f}%"


def print_table(stats, args, n_days, n_symbols):
    """Formatted table output."""
    print()
    print("=" * 78)
    print(f"  ALERT BACKTEST — {n_days} days, {n_symbols} symbols")
    print(f"  z_mod={args.z_moderate}  oi_div={args.oi_div_pct}%  price_div={args.price_div_pct}%")
    if args.from_date or args.to_date:
        print(f"  range: {args.from_date or '...'} to {args.to_date or '...'}")
    if args.symbol:
        print(f"  symbol: {args.symbol}")
    print("=" * 78)
    print()

    header = (
        f"{'Type':<22s} {'Fires':>5s} {'Confl':>5s} "
        f"{'Hit1d':>6s} {'Hit3d':>6s} {'Hit7d':>6s} "
        f"{'Avg1d':>7s} {'Avg3d':>7s} {'Avg7d':>7s}"
    )
    print(header)
    print("─" * 78)

    for atype in ALERT_TYPES:
        s = stats.get(atype)
        if s is None:
            print(f"{atype:<22s}     0     -      -      -      -       -       -       -")
            continue

        print(
            f"{atype:<22s} {s['fires']:5d} {s['avg_confl']:5.1f} "
            f"{fmt_pct(s['hit_1d'], s['n_1d']):>6s} {fmt_pct(s['hit_3d'], s['n_3d']):>6s} {fmt_pct(s['hit_7d'], s['n_7d']):>6s} "
            f"{fmt_ret(s['avg_1d']):>7s} {fmt_ret(s['avg_3d']):>7s} {fmt_ret(s['avg_7d']):>7s}"
        )

    print()

    # Top performers by 3d hit rate
    ranked = [
        (atype, s)
        for atype, s in stats.items()
        if s is not None and s["fires"] >= 3
    ]
    ranked.sort(key=lambda x: x[1]["hit_3d"], reverse=True)

    if ranked:
        print("TOP PERFORMERS (by 3d hit rate, min 3 fires):")
        for i, (atype, s) in enumerate(ranked[:5], 1):
            print(
                f"  {i}. {atype:<22s} {s['hit_3d']:5.1f}%  "
                f"avg {fmt_ret(s['avg_3d'])}  ({s['fires']} fires)"
            )
        print()

    # Worst performers
    worst = sorted(ranked, key=lambda x: x[1]["hit_3d"])
    low = [x for x in worst if x[1]["hit_3d"] < 50 and x[1]["fires"] >= 3]
    if low:
        print("UNDERPERFORMERS (3d hit rate < 50%):")
        for atype, s in low[:3]:
            print(
                f"  ! {atype:<22s} {s['hit_3d']:5.1f}%  "
                f"avg {fmt_ret(s['avg_3d'])}  ({s['fires']} fires)"
            )
        print()


def export_csv(results, path):
    """Export raw results to CSV."""
    if not results:
        print("No results to export.")
        return

    fieldnames = [
        "date", "symbol", "type", "direction", "confluence", "label",
        "ret_1d", "hit_1d", "ret_3d", "hit_3d", "ret_7d", "hit_7d",
    ]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in sorted(results, key=lambda x: (x["date"], x["symbol"])):
            # Format returns
            out = dict(r)
            for w in FORWARD_WINDOWS:
                ret = out.get(f"ret_{w}d")
                out[f"ret_{w}d"] = f"{ret:.2f}" if ret is not None else ""
                hit = out.get(f"hit_{w}d")
                out[f"hit_{w}d"] = str(hit) if hit is not None else ""
            out.setdefault("label", "")
            writer.writerow(out)

    print(f"Exported {len(results)} rows to {path}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Backtest onchain-radar alert system")

    p.add_argument("--db", default=None, help="Path to radar.db")
    p.add_argument("--symbol", default=None, help="Filter to one symbol (e.g. BTCUSDT)")
    p.add_argument("--from-date", default=None, help="Start date (YYYY-MM-DD)")
    p.add_argument("--to-date", default=None, help="End date (YYYY-MM-DD)")

    # Threshold tuning
    p.add_argument("--z-moderate", type=float, default=2.0, help="Z-score threshold (default 2.0)")
    p.add_argument("--oi-div-pct", type=float, default=8.0, help="OI divergence %% (default 8.0)")
    p.add_argument("--price-div-pct", type=float, default=2.0, help="Price divergence %% (default 2.0)")
    p.add_argument("--liq-price-pct", type=float, default=4.0, help="Price drop for LIQ_FLUSH (default 4.0)")
    p.add_argument("--liq-oi-pct", type=float, default=3.0, help="OI drop for LIQ_FLUSH (default 3.0)")

    # Output
    p.add_argument("--csv", default=None, metavar="FILE", help="Export raw results to CSV")
    p.add_argument("-v", "--verbose", action="store_true", help="Print every alert")

    return p.parse_args()


def find_db(args):
    """Locate radar.db — check common paths."""
    if args.db:
        return args.db

    candidates = [
        "backend/data/radar.db",
        "data/radar.db",
        "../backend/data/radar.db",
        os.path.expanduser("~/onchain-radar/backend/data/radar.db"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    print("ERROR: radar.db not found. Use --db <path>", file=sys.stderr)
    sys.exit(1)


def main():
    args = parse_args()
    db_path = find_db(args)

    print(f"Loading data from {db_path} ...")
    rows_by_symbol, price_index = load_data(
        db_path,
        symbol_filter=args.symbol,
        from_date=args.from_date,
        to_date=args.to_date,
    )

    if not rows_by_symbol:
        print("No data found. Check --symbol / --from-date / --to-date filters.")
        sys.exit(1)

    # Count unique dates and symbols
    all_dates = set()
    for rows in rows_by_symbol.values():
        for r in rows:
            all_dates.add(r["date"])
    n_days = len(all_dates)
    n_symbols = len(rows_by_symbol)

    print(f"Loaded {sum(len(v) for v in rows_by_symbol.values())} rows: "
          f"{n_symbols} symbols, {n_days} days")
    print()

    if args.verbose:
        print("── Alert log ──")

    results = run_backtest(rows_by_symbol, price_index, args)

    print(f"\nTotal alerts fired: {len(results)}")

    stats = compute_stats(results)
    print_table(stats, args, n_days, n_symbols)

    if args.csv:
        export_csv(results, args.csv)


if __name__ == "__main__":
    main()
