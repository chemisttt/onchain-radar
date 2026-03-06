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
| [README.md](README.md) | Архитектура, установка, API endpoints |
| [SPEC.md](SPEC.md) | Полное ТЗ и roadmap |

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
- `rate_limiter` — Token bucket per-domain

### Backend Routers (`backend/routers/`)
- `derivatives` — /api/derivatives/* (screener, detail, global, momentum, liq-map, orderbook)
- `feed` — WS /ws/feed + REST /api/feed
- `funding` — /api/funding/* (rates, spreads, history)
- `tokens`, `security`, `analyze` — token analysis
- `claude` — SSE streaming analysis
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

## UI Rules

- Dark theme: bg `#0c0c0c`, card border `#1a1a1a`
- Font: mono for data, 9-11px for chart labels
- Tooltip: bg `#222`, border `#444`, text `#e2e8f0`, label `#999`
- Colors: green `#22c55e`, red `#ef4444`, yellow `#eab308`, cyan `#06b6d4`
- Regime colors: 6-tier from green (z≤-2) through yellow to red (z>2)
