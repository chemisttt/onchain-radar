# On-Chain Radar

Персональный derivatives analytics dashboard + auto-trading система. Мониторинг OI, funding, liquidations, volatility для 34 perp-символов. Сигналы → Telegram → Hyperliquid auto-execution.

---

## Архитектура

```
┌────────────────────────────────────────────────────────────────┐
│                     Browser (5173)                              │
│  [Feed] [Funding Arb] [Analyzer] [Derivatives] [Trading] [Docs]│
│                                                                │
│  Derivatives: Screener, SymbolDetail, GlobalDashboard,         │
│               LiquidationMap, Momentum, Backtest               │
│  Trading: Positions, History, EquityCurve, Stats               │
└───────────────────────────┬────────────────────────────────────┘
                            │ http:// / ws://
┌───────────────────────────┼────────────────────────────────────┐
│                     FastAPI (8000)                              │
│                                                                │
│  Routers:                                                      │
│    /api/derivatives/*  /api/trading/*  /api/funding/*           │
│    /api/feed  /ws/feed  /api/tokens  /api/security             │
│    /api/claude  /api/watchlist  /api/settings                  │
│                                                                │
│  Services:                                                     │
│    derivatives_service ── OI/funding/liq (4 exchanges, 34 sym) │
│    market_analyzer ────── signal detection (5min cycle)        │
│    trading_service ────── HL auto-trading + adaptive exits     │
│    telegram_service ───── alerts pipeline + cooldowns          │
│    options_service ────── Deribit IV/RV/Skew (BTC/ETH)        │
│    momentum_service ───── cross-sectional/time-series momentum │
│    liquidation_service ── Binance+Bybit WS liq collector      │
│    orderbook_service ──── Binance OB depth ±2% (30s)          │
│    feed_engine ────────── DexScreener/Gecko/Etherscan/Helius   │
│    funding_service ────── 11+ exchange funding rates           │
│    contract_scanner ───── vuln scan for new EVM pairs          │
│    claude_service ─────── Claude CLI contract analysis         │
│                                                                │
│  SQLite (WAL) ── radar.db                                      │
└────────────────────────────────────────────────────────────────┘
         │                              │
    Binance/Bybit/OKX/Bitget      Hyperliquid
    (data: OI, funding, liq,      (execution: market orders,
     OHLCV, orderbook)             SL triggers, positions)
```

**Стек:**
- **Backend:** Python 3.10+ / FastAPI / aiosqlite / aiohttp
- **Frontend:** React 19 / Vite 7 / Tailwind 4 / Zustand / TanStack Query / Recharts 3
- **DB:** SQLite WAL mode (`backend/data/radar.db`)
- **Trading:** Hyperliquid (EIP-712, decentralized perp DEX)
- **Alerts:** Telegram Bot API

---

## Запуск

```bash
# Dev (concurrent backend + frontend)
npm run dev

# Or separately:
cd backend && python3 -m uvicorn main:app --reload --port 8000
cd frontend && npm run dev
```

### .env (ключевые переменные)

```env
# Derivatives data (обязательно)
ETHERSCAN_API_KEY=         # Etherscan V2
HELIUS_API_KEY=            # Helius (Solana)

# Telegram alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_THREAD_ID=523     # Derivatives signals topic

# Hyperliquid trading
HL_WALLET_KEY=0x...
HL_TRADING_ENABLED=false    # Must explicitly enable
HL_ALLOC_PCT=20
HL_LEVERAGE=3
HL_MAX_POSITIONS=10
HL_HARD_STOP_PCT=8
```

---

## Signal System

### Pipeline

```
market_analyzer.check_alerts() (every 5min)
  → per-symbol: z-scores, confluence scoring, directional alerts
  → ALT_MIN_CONFLUENCE=5 filter (alts need confluence ≥5)
  → telegram_service: cache_signal → cooldown → send → record_alert
  → trading_service.on_signal() (fire-and-forget)
    → validate → size (equity × 20% × 3x) → HL market order → hard SL → trades DB
```

### Strategy: Hybrid C (860 trades, 3yr backtest)

- **WR 50.0%, EV +1.45% (net), PF 1.64x, PnL +1,246%**
- Walk-forward: 6/6 positive windows
- 3 types on 4h detection (liq_short_squeeze, momentum_divergence, div_top_1d), rest daily
- Full details: [docs/STRATEGY.md](docs/STRATEGY.md)

### Exit Strategies (adaptive per signal type)

| Strategy | Signals | Logic |
|----------|---------|-------|
| counter_sig | liq_short_squeeze, div_squeeze_3d, div_top_1d, vol_divergence, momentum_divergence, liq_ratio_extreme | 12% hard stop, exit on opposite signal |
| fixed | distribution, oi_buildup_stall, overheat | TP 5%, SL 3%, timeout 7d |
| trail_atr | overextension, fund_spike | 1.5×ATR trail, break-even at +2% |
| zscore_mr | fund_reversal, capitulation | Exit when z normalizes or worsens +1.0 |
| hybrid | div_squeeze_1d | trail + counter + zscore combined |

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `daily_derivatives` | OI, funding, liq, volume per symbol per day |
| `derivatives_zscores` | Precomputed 365d z-scores |
| `derivatives_4h` | 4h timeframe derivatives data |
| `ohlcv_4h` | Binance 4h candles |
| `daily_volatility` | IV, RV, Skew, VRP (BTC/ETH) |
| `daily_momentum` | Momentum scores per symbol |
| `daily_rv` | Realized volatility |
| `liquidation_events` | Real-time liq events from WS |
| `trades` | Trading positions (open/closed) |
| `alert_tracking` | Signal history + trade decisions |
| `alert_cooldowns` | Per-signal cooldown tracking |
| `feed_events` | Live feed events (NEW_PAIR, WHALE, etc.) |
| `funding_snapshots` | Funding rate history |
| `token_cache` | Token data cache (5min TTL) |
| `security_cache` | Security scan cache (15min TTL) |
| `contract_scans` | Vuln scanner results |
| `factory_hashes` | Auto-learned factory bytecodes |
| `settings` | App config |

---

## API Endpoints

### Derivatives
```
GET  /api/derivatives/screener          All symbols: z-scores, regime, price
GET  /api/derivatives/global            Global OI, dominance, regime
GET  /api/derivatives/{symbol}          Symbol detail + z-score history
GET  /api/derivatives/{symbol}/momentum-page  Momentum analytics
GET  /api/derivatives/{symbol}/backtest Signal history for charting
GET  /api/derivatives/liquidation-map   Theoretical liq levels + events
GET  /api/derivatives/orderbook/{symbol} OB depth + skew
```

### Trading
```
GET  /api/trading/positions             Open trades
GET  /api/trading/history?limit=50      Closed trades
GET  /api/trading/stats                 WR, PnL, counts
POST /api/trading/close/{id}            Manual close
```

### Feed / Funding / Token
```
WS   /ws/feed                           Live events
GET  /api/feed                          Paginated events
GET  /api/funding/rates                 Current rates (11+ exchanges)
GET  /api/funding/spreads               Arb opportunities
GET  /api/funding/history               Rate history
GET  /api/tokens/{chain}/{address}      Token pair data
GET  /api/security/{chain}/{address}    Security checks
POST /api/claude/analyze                Claude streaming (SSE)
```

---

## VPS Deployment

```
Host: 77.221.154.136, user: botuser
Path: /home/botuser/onchain-radar
Service: systemd onchain-radar
Nginx: serves frontend/dist, proxies /api + /ws to :8000
Deploy: /deploy slash command (git push → SSH pull → restart → health check)
```

---

## Rate Limits

| API | Limit | Our Load |
|-----|-------|----------|
| DexScreener | 300/min | ~6 req/min |
| GeckoTerminal | 30/min | ~8 req/min |
| Etherscan V2 | 5/sec | ~2 req/min |
| Binance futures | 1200/min | ~20 req/5min |
| Bybit | 120/5sec | ~20 req/5min |
| OKX | 20/sec | ~20 req/5min |
| Deribit | 20/sec | ~2 req/60min |
| Hyperliquid | unlimited | ~1 req/60s (exit poll) |

All managed via token bucket in `rate_limiter.py`.
