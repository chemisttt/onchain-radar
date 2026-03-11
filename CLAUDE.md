# On-Chain Radar

Personal derivatives analytics dashboard. Real-time monitoring of OI, funding, liquidations, volatility across 30 perp symbols.

## Quick Reference

- **Start:** `npm run dev` (concurrently BE:8000 + FE:5173)
- **Backend:** Python 3.10+ / FastAPI / aiosqlite / aiohttp
- **Frontend:** React 19 / Vite 7 / Tailwind 4 / Zustand / TanStack Query / Recharts 3
- **DB:** SQLite WAL mode (`backend/data/radar.db`)

## Documentation

| Doc | Contents |
|-----|----------|
| [docs/metrics-guide.md](docs/metrics-guide.md) | Все метрики + торговые стратегии |
| [docs/TODO.md](docs/TODO.md) | 7 фич (VRP, Alt OI Dom, Vol Cone, Spot Delta, Composite v2, Momentum, Scatter) |
| [docs/signal-backtest-results.md](docs/signal-backtest-results.md) | Бектест сигналов: WR/EV per type, thresholds, disabled signals |
| [docs/equity-simulation.md](docs/equity-simulation.md) | Equity curves, leverage, position sizing, monthly PnL |
| [README.md](README.md) | Архитектура, установка, API endpoints |
| [SPEC.md](SPEC.md) | Полное ТЗ и roadmap |

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
- Tiers: SETUP (≥3), SIGNAL (≥4), TRIGGER (≥6 but **capped to SIGNAL**)
- Cooldowns: SIGNAL=12h, TRIGGER=6h (telegram_service.py)
- Direction: backtest uses "long"/"short", live DB uses "up"/"down", router maps to "long"/"short" for frontend

### Backtest Scripts
- `scripts/run_backtest_v2.py` — full backtest with MFE/MAE evaluation
- `scripts/analyze_losers.py` — winner vs loser feature comparison per signal type
- Run from: `cd backend && python3 scripts/run_backtest_v2.py`

## Module Map

### Backend Services (`backend/services/`)
- `derivatives_service` — OI/funding/liq polling (4 exchanges) + z-scores + screener build
- `options_service` — Deribit IV/RV/Skew + backfill (BTC/ETH only)
- `liquidation_service` — Binance+Bybit WS liquidation collector + theoretical levels
- `orderbook_service` — Binance OB depth ±2% + skew z-score (30s poll)
- `feed_engine` — DexScreener/Gecko/Etherscan/Helius event polling
- `funding_service` — Rates from 11+ exchanges
- `protocol_tracker` — DefiLlama TVL spikes
- `claude_service` — Claude CLI subprocess for contract analysis
- `trading_service` — Hyperliquid auto-trading: on_signal entry, adaptive exits (60s poll), position reconciliation
- `rate_limiter` — Token bucket per-domain

### Backend Routers (`backend/routers/`)
- `derivatives` — /api/derivatives/* (screener, detail, global, momentum, liq-map, orderbook)
- `feed` — WS /ws/feed + REST /api/feed
- `funding` — /api/funding/* (rates, spreads, history)
- `tokens`, `security`, `analyze` — token analysis
- `claude` — SSE streaming analysis
- `trading` — /api/trading/* (positions, history, close, stats)
- `watchlist`, `settings`

### Frontend Components (`frontend/src/components/`)
- `derivatives/` — DerivativesPanel, ScreenerTable, SymbolDetail, MomentumTab, LiquidationMap, GlobalDashboard, CompositeRegimeChart, MetricChart, ZScoreChart, ZScatterCard, ExpandedChartModal
- `feed/` — FeedPanel, FeedItem, FeedFilters
- `funding/` — FundingPanel, FundingArb, SpreadTable, FundingChart, RateComparison
- `token/` — TokenPanel, SecurityScore, ClaudeAnalysis
- `analyzer/` — AnalyzerPanel, RiskScore, CategoryBreakdown

### Frontend Hooks (`frontend/src/hooks/`)
- `useDerivativesScreener/Detail/Global` — derivatives data (5min refresh)
- `useMomentum` — IV/RV/Skew (5min)
- `useLiquidationMap` — theoretical levels + real events (30s)
- `useFunding/History/Spreads` — funding data
- `useFeed` — WS + REST events
- `useAnalyze`, `useSecurity`, `useTokenAnalysis` — token analysis

## Gotchas

- DB path: `backend/data/radar.db` (WAL mode, auto-created from schema.sql)
- Recharts formatter types: use `any` for Tooltip callback params (strict TS compatibility)
- Derivatives symbols: 30 hardcoded in `SYMBOLS` list (derivatives_service.py)
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
