#!/usr/bin/env python3
"""Generate full signal + bar cache — run once (~3s), then all analysis scripts load instantly.

Saves to /tmp/signal_cache.pkl (all) or with --train/--test suffix.

Cache format:
{
    'meta': { 'created': str, 'n_signals': int, 'n_symbols': int, 'split': str },
    'symbols': {
        'BTCUSDT': {
            'daily': list[Bar],           # full daily bars with z-scores
            'bars_4h': list[Bar4h],       # full 4h bars
            'date_first': dict,           # date → first 4h idx
            'date_last': dict,            # date → last 4h idx
            'signals': list[Signal],      # detected signals (filtered by split)
            'cache_4h': dict,             # counter-signal cache {4h_idx: [(type, dir)]}
        },
        ...
    },
    'all_signals': [                      # flat list for quick iteration
        { 'sig': Signal, 'sig_date': str, 'sym': str,
          'bars_4h': <ref>, 'cache_4h': <ref>, 'daily_bar': dict },
        ...
    ],
}

Usage:
    python scripts/lab/cache_signals.py              # cache all signals
    python scripts/lab/cache_signals.py --train       # only before 2025-01-01
    python scripts/lab/cache_signals.py --test        # only from 2025-01-01

Then in analysis scripts:
    import pickle
    with open('/tmp/signal_cache.pkl', 'rb') as f:
        cache = pickle.load(f)
    for sym, data in cache['symbols'].items():
        daily = data['daily']
        bars_4h = data['bars_4h']
        signals = data['signals']
        ...
"""
import asyncio, sys, os, pickle, time
from datetime import datetime

sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')
sys.argv = ['test', '--4h']

from db import init_db
from scripts.setup_backtest import (
    load_symbol_data, load_4h_bars, detect_all_signals,
    detect_signals_at_bar, SYMBOLS, BARS_PER_DAY, TRAIN_TEST_SPLIT
)

# Parse --train / --test from CLI
_split = "all"
if "--train" in sys.argv:
    _split = "train"
elif "--test" in sys.argv:
    _split = "test"

CACHE_PATH = '/tmp/signal_cache.pkl'
if _split != "all":
    CACHE_PATH = f'/tmp/signal_cache_{_split}.pkl'


async def main():
    t0 = time.time()
    await init_db()

    symbols_data = {}
    all_signals_flat = []
    total_signals = 0

    for idx, sym in enumerate(SYMBOLS):
        daily = await load_symbol_data(sym)
        if not daily:
            continue
        bars_4h, df, dl = await load_4h_bars(sym, daily)
        if not bars_4h:
            continue

        signals = detect_all_signals(daily, sym, days=1100)

        # Apply split filter
        if _split == "train":
            signals = [s for s in signals if daily[s.bar_idx].date < TRAIN_TEST_SPLIT]
        elif _split == "test":
            signals = [s for s in signals if daily[s.bar_idx].date >= TRAIN_TEST_SPLIT]

        # Build counter-signal cache (daily → 4h mapping)
        sigs_by_date = {}
        for i in range(len(daily)):
            t = detect_signals_at_bar(daily, i)
            if t:
                sigs_by_date[daily[i].date] = t
        c4h = {}
        for i, b4 in enumerate(bars_4h):
            s = sigs_by_date.get(b4.date)
            if s:
                c4h[i] = s

        # Remap signals to 4h indices
        valid_signals = []
        for sig in (signals or []):
            sig_date = daily[sig.bar_idx].date
            idx_4h = dl.get(sig_date, -1)
            if idx_4h < 0:
                continue
            sig.bar_idx_4h = idx_4h
            sig.entry_price = bars_4h[idx_4h].close
            valid_signals.append(sig)

            # Flat entry for backward compat
            b = daily[sig.bar_idx]
            all_signals_flat.append({
                'sig': sig,
                'sig_date': sig_date,
                'sym': sym,
                'bars_4h': bars_4h,
                'cache_4h': c4h,
                'daily_bar': {
                    'trend': b.trend, 'pvs': b.price_vs_sma,
                    'oi_z': b.oi_z, 'fund_z': b.fund_z,
                    'price_chg': b.price_chg, 'oi_chg': b.oi_chg,
                    'funding_rate': b.funding_rate,
                },
            })

        symbols_data[sym] = {
            'daily': daily,
            'bars_4h': bars_4h,
            'date_first': df,
            'date_last': dl,
            'signals': valid_signals,
            'cache_4h': c4h,
        }
        total_signals += len(valid_signals)

        n_sig = len(valid_signals) if valid_signals else 0
        if n_sig > 0:
            print(f"  [{idx+1}/{len(SYMBOLS)}] {sym}: {len(daily)}d + {len(bars_4h)} 4h, {n_sig} signals")

    cache = {
        'meta': {
            'created': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'n_signals': total_signals,
            'n_symbols': len(symbols_data),
            'split': _split,
            'bars_per_day': BARS_PER_DAY,
            'train_test_split': TRAIN_TEST_SPLIT,
        },
        'symbols': symbols_data,
        'all_signals': all_signals_flat,
    }

    with open(CACHE_PATH, 'wb') as f:
        pickle.dump(cache, f, protocol=pickle.HIGHEST_PROTOCOL)

    size_mb = os.path.getsize(CACHE_PATH) / 1024 / 1024
    elapsed = time.time() - t0
    print(f"\n  Cached {total_signals} signals, {len(symbols_data)} symbols to {CACHE_PATH}")
    print(f"  File size: {size_mb:.1f} MB, took {elapsed:.1f}s")


asyncio.run(main())
