#!/usr/bin/env python3
"""Analyze where signals are being filtered out."""
import asyncio, sys, os
sys.path.insert(0, '/Users/chemisttt/Desktop/code/onchain-radar/backend')
os.chdir('/Users/chemisttt/Desktop/code/onchain-radar/backend')
sys.argv.append('--4h')

from db import init_db
from scripts.setup_backtest import load_symbol_data, SYMBOLS, _bar_to_signal_input
from services.signal_conditions import detect_signals, compute_confluence, CONFLUENCE_SIGNAL, ALT_MIN_CONFLUENCE

TOP_OI = {'BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','BNBUSDT','DOGEUSDT','TRXUSDT','UNIUSDT','SUIUSDT','ADAUSDT'}

async def main():
    await init_db()

    total_raw = 0
    momentum_killed = 0
    confluence_killed = 0
    alt_killed = 0
    cooldown_killed = 0
    final = 0

    by_sig = {}  # sig -> {raw, mom, conf, alt, cd, final}

    for sym in SYMBOLS:
        daily = await load_symbol_data(sym)
        if not daily:
            continue
        total = len(daily)
        warmup_end = max(30, total - 1100)
        cooldowns = {}

        for i in range(warmup_end, total):
            if daily[i].close <= 0:
                continue
            inp = _bar_to_signal_input(daily, i)
            triggered = detect_signals(inp)

            for sig_type, direction in triggered:
                total_raw += 1
                if sig_type not in by_sig:
                    by_sig[sig_type] = {'raw':0,'mom':0,'conf':0,'alt':0,'cd':0,'final':0}
                by_sig[sig_type]['raw'] += 1

                trend = daily[i].trend
                if direction == 'long' and trend == 'down':
                    momentum_killed += 1
                    by_sig[sig_type]['mom'] += 1
                    continue
                if direction == 'short' and trend == 'up':
                    momentum_killed += 1
                    by_sig[sig_type]['mom'] += 1
                    continue

                confluence, _ = compute_confluence(inp, direction)
                if confluence < CONFLUENCE_SIGNAL:
                    confluence_killed += 1
                    by_sig[sig_type]['conf'] += 1
                    continue

                if sym not in TOP_OI and confluence < ALT_MIN_CONFLUENCE:
                    alt_killed += 1
                    by_sig[sig_type]['alt'] += 1
                    continue

                cd_key = f'{sig_type}:{sym}'
                if cd_key in cooldowns and (i - cooldowns[cd_key]) < 1:
                    cooldown_killed += 1
                    by_sig[sig_type]['cd'] += 1
                    continue
                cooldowns[cd_key] = i
                final += 1
                by_sig[sig_type]['final'] += 1

    am = total_raw - momentum_killed
    ac = am - confluence_killed
    aa = ac - alt_killed

    print('=== SIGNAL FUNNEL ===')
    print(f'Raw detections:      {total_raw}')
    print(f'  - momentum filter: -{momentum_killed} ({momentum_killed*100//total_raw}%)')
    print(f'After momentum:      {am}')
    print(f'  - confluence <{CONFLUENCE_SIGNAL}:   -{confluence_killed} ({confluence_killed*100//max(am,1)}%)')
    print(f'After confluence:    {ac}')
    print(f'  - alt filter <{ALT_MIN_CONFLUENCE}:   -{alt_killed}')
    print(f'After alt filter:    {aa}')
    print(f'  - cooldown 1d:     -{cooldown_killed}')
    print(f'Final (pre-cluster): {final}')
    print()
    print(f'{"Signal":<25} {"Raw":>5} {"-Mom":>5} {"-Conf":>5} {"-Alt":>5} {"-CD":>5} {"Final":>5}')
    print('-' * 75)
    for sig in sorted(by_sig, key=lambda s: by_sig[s]['raw'], reverse=True):
        d = by_sig[sig]
        print(f'{sig:<25} {d["raw"]:>5} {d["mom"]:>5} {d["conf"]:>5} {d["alt"]:>5} {d["cd"]:>5} {d["final"]:>5}')

asyncio.run(main())
