# On-Chain Radar

Personal derivatives analytics dashboard + auto-trading system. Real-time monitoring of OI, funding, liquidations, volatility across 34 perp symbols. Signal detection → Telegram alerts → Hyperliquid auto-execution.

## Quick Reference

- **Start:** `npm run dev` (concurrently BE:8000 + FE:5173)
- **Backend:** Python 3.10+ / FastAPI / aiosqlite / aiohttp
- **Frontend:** React 19 / Vite 7 / Tailwind 4 / Zustand / TanStack Query / Recharts 3
- **DB:** SQLite WAL mode (`backend/data/radar.db`)

## Documentation

| Doc | Contents |
|-----|----------|
| [docs/metrics-guide.md](docs/metrics-guide.md) | Все метрики + торговые стратегии |
| [docs/TODO.md](docs/TODO.md) | 10 фич — все DONE (VRP, Momentum, Scatter, etc.) |
| [docs/signal-backtest-results.md](docs/signal-backtest-results.md) | Бектест: Hybrid C 860 trades, WR/EV per type, disabled signals |
| [docs/equity-simulation.md](docs/equity-simulation.md) | Equity curves, leverage, position sizing (528 daily-only, needs Hybrid C re-run) |
| [docs/STRATEGY.md](docs/STRATEGY.md) | Финальная стратегия: Hybrid C, сигналы, выходы, walk-forward |
| [README.md](README.md) | Архитектура, установка, полный module map |
| [SPEC.md](SPEC.md) | ТЗ и roadmap (historical — Phases 1-4 planned, all done) |

## Signal System

Two mirrored files define the same signal logic:
- `backtest_service.py` — historical backtesting (daily + 4h timeframes)
- `market_analyzer.py` — live alerts (5min cycle)

**RULE**: Any threshold/condition change MUST be applied to BOTH files (3 sections: daily backtest, 4h backtest, live).

### Alert Pipeline
`market_analyzer.check_alerts()` → `telegram_service` (cache_signal ALL → cooldown filter → send → record_alert) → `trading_service.on_signal()` (fire-and-forget) → HL market order + hard SL → `trades` DB → Telegram notify

Legacy: `alert_tracking` DB → `derivatives.py` router (direction mapping) → frontend chart

### Key Constants
- `TOP_OI_SYMBOLS` — BTC, ETH, SOL, XRP, BNB, DOGE, TRX, UNI, SUI, ADA
- `ALT_MIN_CONFLUENCE = 5` — altcoins (non-TOP_OI) need confluence ≥5 (in BOTH backtest_service + market_analyzer)
- Tiers: SETUP (≥3), SIGNAL (≥4), TRIGGER (≥6 but **capped to SIGNAL**)
- Cooldowns: SIGNAL=12h, TRIGGER=6h (telegram_service.py)
- Direction: backtest uses "long"/"short", live DB uses "up"/"down", router maps to "long"/"short" for frontend

### Backtest Scripts
- `scripts/run_backtest_v2.py` — full backtest with MFE/MAE evaluation
- `scripts/analyze_losers.py` — winner vs loser feature comparison per signal type
- Run from: `cd backend && python3 scripts/run_backtest_v2.py`

## Module Map

### Backend Services (`backend/services/`)
- `derivatives_service` — OI/funding/liq polling (4 exchanges, 34 symbols) + z-scores + screener build
- `options_service` — Deribit IV/RV/Skew + backfill (BTC/ETH only)
- `liquidation_service` — Binance+Bybit WS liquidation collector + theoretical levels
- `orderbook_service` — Binance OB depth ±2% + skew z-score (30s poll)
- `feed_engine` — DexScreener/Gecko/Etherscan/Helius event polling
- `funding_service` — Rates from 11+ exchanges
- `protocol_tracker` — DefiLlama TVL spikes
- `claude_service` — Claude CLI subprocess for contract analysis
- `trading_service` — Hyperliquid auto-trading: on_signal entry, adaptive exits (60s poll), position reconciliation
- `contract_scanner` — Vuln scanner: polls NEW_PAIR → factory filter → GoPlus → regex source scan → Telegram alert (topic 995)
- `telegram_service` — Alert polling: cache_signal → cooldown → send → record_alert → fire on_signal
- `market_analyzer` — Signal detection (5min cycle): confluence scoring, directional alerts, macro alerts
- `backtest_service` — Historical backtesting (daily + 4h timeframes)
- `signal_conditions` — Shared signal detection logic (used by backtest 4h + setup_backtest)
- `momentum_service` — Cross-sectional/time-series momentum, DI, VR, scatter plots
- `price_service` — Binance klines for backtest/market_analyzer price context
- `rate_limiter` — Token bucket per-domain
- `exploit_engine` — Auto-exploit pipeline: polls contract_scans for vulns → build calldata → dry-run → execute → swap → Telegram alert (30s poll, DRY_RUN_ONLY=true default)
- `exploit_templates` — Per-vuln calldata builders (unprotected_mint active, others alert-only)
- `evm_rpc` — Thin JSON-RPC client (HTTP only, no web3.py), TX signing via eth_account, public RPC fallback

### Backend Routers (`backend/routers/`)
- `derivatives` — /api/derivatives/* (screener, detail, global, momentum, liq-map, orderbook)
- `feed` — WS /ws/feed + REST /api/feed
- `funding` — /api/funding/* (rates, spreads, history)
- `tokens`, `security`, `analyze` — token analysis
- `claude` — SSE streaming analysis
- `trading` — /api/trading/* (positions, history, close, stats)
- `watchlist`, `settings`

### Frontend Components (`frontend/src/components/`)
- `derivatives/` — DerivativesPanel, ScreenerTable, SymbolDetail, MomentumTab, LiquidationMap, GlobalDashboard, CompositeRegimeChart, MetricChart, ZScoreChart, ZScatterCard, ExpandedChartModal, BacktestPage, MomentumPage
- `trading/` — TradingPanel, PositionsTable, HistoryTable, EquityCurve
- `feed/` — FeedPanel, FeedItem, FeedFilters
- `funding/` — FundingPanel, FundingArb, SpreadTable, FundingChart, RateComparison
- `token/` — TokenPanel, SecurityScore, ClaudeAnalysis
- `analyzer/` — AnalyzerPanel, RiskScore, CategoryBreakdown
- `docs/` — DocsPanel

### Frontend Hooks (`frontend/src/hooks/`)
- `useDerivativesScreener/Detail/Global` — derivatives data (5min refresh)
- `useMomentum` — IV/RV/Skew (5min)
- `useMomentumPage` — per-symbol momentum page data
- `useLiquidationMap` — theoretical levels + real events (30s)
- `useBacktest` — backtest signal data per symbol
- `useTradingPositions/History/Stats` — trading data (15s/60s/30s refresh)
- `useClosePosition` — close trade mutation
- `useFunding/History/Spreads` — funding data
- `useFeed` — WS + REST events
- `useAnalyze`, `useSecurity`, `useTokenAnalysis` — token analysis

## Gotchas

- DB path: `backend/data/radar.db` (WAL mode, auto-created from schema.sql)
- Recharts formatter types: use `any` for Tooltip callback params (strict TS compatibility)
- Derivatives symbols: 34 hardcoded in `SYMBOLS` list (derivatives_service.py)
- Options data (IV/Skew): only BTC/ETH via Deribit — others get RV-only
- Z-scores: min 7 data points required, 365-day rolling window
- Polling intervals: derivatives 5min, OB 30sec, funding 60sec, feed 10-60sec
- Liq WS batch flush: 3 events OR 10 seconds (whichever first)
- Route order in derivatives.py: specific paths BEFORE `{symbol}` catch-all
- VPS: `77.221.154.136` user `botuser`, path `/home/botuser/onchain-radar`, no sudo — use `/deploy` command
- Signal direction convention: backtest="long"/"short", live DB="up"/"down", router normalizes for frontend
- Trading: `HL_TRADING_ENABLED=false` by default — must explicitly enable in .env
- Trading: hard SL always on HL exchange (survives service restarts), adaptive exits poll every 60s
- Trading: `trades` table tracks positions; exit strategies mirror `ADAPTIVE_EXIT` from setup_backtest.py
- Trading: counter-signal exit needs `_recent_signals` cache — loaded from `alert_tracking` on restart
- Trading: any ADAPTIVE_EXIT or COUNTER_SIGNALS change → update BOTH `setup_backtest.py` AND `trading_service.py`
- Scanner: polls NEW_PAIR from feed_events (EVM only, liq ≥$10k), 60s interval, 45s initial delay
- Scanner: factory detection — EIP-1167 clones + auto-learned bytecode hashes (5+ identical = factory)
- Scanner: 7 vuln patterns with FP filters in `VULN_PATTERNS` list — extensible
- Scanner: alerts go to Telegram topic 995 (`SCANNER_TELEGRAM_THREAD_ID`), separate from derivatives signals (topic 523)
- Scanner: `contract_scans` + `factory_hashes` DB tables, scans cached 24h
- Exploit: `EXPLOIT_ENABLED=false` by default, `EXPLOIT_DRY_RUN_ONLY=true` — safe start
- Exploit: polls `contract_scans` every 30s, 60s initial delay (waits for scanner)
- Exploit: only `unprotected_mint` auto-exploitable, other vuln types → manual alert
- Exploit: public RPC fallback (llamarpc, base.org, arbitrum.io, binance.org) — no API keys needed for dry-run
- Exploit: `exploit_attempts` DB table logs every attempt (dry-run result, tx hash, profit)
- Exploit: swap via UniV2/PancakeV2 routers (token→WETH, fee-on-transfer safe)

## Subagents & Commands

Use project subagents (`.claude/agents/`) to save context. Prefer subagents for exploration and investigation — only read files directly when the exact path is known.

| Agent | When to use |
|-------|-------------|
| `backend-expert` | Python/FastAPI services, DB schema, z-scores, polling logic |
| `frontend-expert` | React components, Recharts charts, hooks, Tailwind styling |
| `explorer` | "Where is X", find references, trace data flow (runs on haiku — fast & cheap) |
| `debugger` | Something is broken — traces root cause, doesn't modify files |
| `reviewer` | After changes: tsc + eslint + build + python check → update docs → git commit |
| `skill-researcher` | Before implementation: finds patterns/snippets from global skills |

Slash commands (`.claude/skills/`):
- `/find-bug <description>` — investigate bug via debugger agent, no modifications
- `/impact <what's changing>` — analyze affected files via explorer agent
- `/review` — full pipeline: review → update docs → commit (stops on failures)
- `/deploy` — push to remote → SSH pull + systemctl restart → health check (one SSH attempt)

## UI Rules

- Dark theme: bg `#0c0c0c`, card border `#1a1a1a`
- Font: mono for data, 9-11px for chart labels
- Tooltip: bg `#222`, border `#444`, text `#e2e8f0`, label `#999`
- Colors: green `#22c55e`, red `#ef4444`, yellow `#eab308`, cyan `#06b6d4`
- Regime colors: 6-tier from green (z≤-2) through yellow to red (z>2)
