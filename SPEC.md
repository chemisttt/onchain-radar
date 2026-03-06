# On-Chain Radar — Полное ТЗ

## Что это

Персональный trading dashboard, объединяющий:
- Мониторинг новых токенов и whale activity в реальном времени
- Funding rate арбитраж между 11+ биржами
- Анализ токенов на скам (risk scoring)
- Деривативные данные для directional trading (OI, liquidations, z-scores)

Не SaaS, не для продажи — инструмент для себя.

---

## Текущее состояние (что УЖЕ работает)

### Backend (Python, FastAPI, SQLite)
- **Feed Engine** — polling DexScreener, GeckoTerminal, Etherscan, Helius каждые 10-60с
- **Funding Service** — рейты с 11 бирж (Binance, Bybit, OKX, MEXC, Hyperliquid + 6 perp DEX)
- **Spread Scanner** — находит арбитражные спреды между биржами
- **History + Backfill** — 7 дней исторических фандинг рейтов с бирж
- **Token Security** — GoPlus, Honeypot.is, RugCheck (EVM + Solana)
- **Risk Scorer** — unified score 0-100 по 4 категориям
- **Claude AI** — streaming анализ контрактов через CLI
- **Protocol Tracker** — TVL spikes и новые yield pools (DefiLlama)
- **WebSocket** — realtime feed с batched broadcasting

### Frontend (React 19, Vite, Tailwind, Zustand, Recharts)
3 таба:
1. **Feed** — 2×2 grid: live feed + token detail + watchlist + funding overview
2. **Funding Arb** — spread table + rate comparison + 7d history chart
3. **Token Analyzer** — chain+address input → risk score + red flags

### API Endpoints
```
WS  /ws/feed                          Live events
GET /api/feed                         Paginated events
GET /api/tokens/{chain}/{address}     Token pair data
GET /api/security/{chain}/{address}   Security checks
GET /api/analyze/{chain}/{address}    Risk score 0-100
POST /api/claude/analyze              Claude streaming (SSE)
GET /api/watchlist                    Watchlist CRUD
GET /api/funding/rates                Current rates (11 exchanges)
GET /api/funding/spreads              Arb opportunities
GET /api/funding/history              7d rate history
GET /api/settings                     App settings
```

### Database (SQLite)
```
feed_events          — live events log (capped 2000)
token_cache          — token data (5min TTL)
security_cache       — security results (15min TTL)
analysis_cache       — risk scores (10min TTL)
watchlist            — tracked tokens
funding_snapshots    — funding rate history (7d backfill + live)
settings             — app config
claude_sessions      — AI analysis history
```

---

## Что планируется добавить

### Phase 4: Derivatives Analytics (новое, вдохновлено TradingRiot)

**Зачем:** Для directional BTC/ETH trading — торговля от наклонок, зон интереса, волн Эллиотта. Деривативные данные дают confluence: когда positioning экстремальный → setup надёжнее.

#### 4.1 — Open Interest Pipeline

**Что:** Агрегированный OI с 4-5 бирж для топ-30 символов.

**Источники (все бесплатные):**
| Биржа | API | Rate Limit |
|-------|-----|------------|
| Binance | `GET /fapi/v1/openInterest` | 1200/min |
| Bybit | `GET /v5/market/open-interest` | 120/min |
| OKX | `GET /api/v5/public/open-interest` | 20/sec |
| Bitget | `GET /api/v2/mix/market/open-interest` | 20/sec |

**Данные:** символ, дата, OI в USD, OI change 24h/7d

**Поллинг:** каждые 5 минут, запись в `daily_derivatives` таблицу.

**Бэкфилл:** Binance — `GET /futures/data/openInterestHist` (макс 30 дней). Bybit — аналогично. Дальше накапливаем сами.

#### 4.2 — Liquidations Pipeline

**Что:** Агрегированные ликвидации (longs vs shorts) для top-30 символов.

**Источники:**
| Вариант | Стоимость | Качество |
|---------|-----------|----------|
| CoinGlass API (free tier) | $0, 1000 req/day | Агрегировано с 8+ бирж |
| Binance `GET /futures/data/globalLongShortAccountRatio` | $0 | Только Binance |
| Binance WS `forceOrder` stream | $0, realtime | Только Binance, raw events |

**Рекомендация:** Начать с Binance `forceOrder` WS (бесплатно, realtime). Потом CoinGlass если нужна агрегация.

**Данные:** символ, дата, long_liquidations_usd, short_liquidations_usd, delta (long - short)

#### 4.3 — Z-Score & Percentile Computation

**Что:** Rolling 365-day z-scores и percentiles для каждой метрики.

**Формулы:**
```python
z_score = (current - rolling_mean_365d) / rolling_std_365d
percentile = rank_within_365d_window / total_points * 100
```

**Метрики для z-score:**
- OI Z-Score → показывает когда positioning экстремальный
- Funding Z-Score → показывает перегрев лонгов/шортов
- Liquidation Z-Score → показывает каскадные события
- Volume Z-Score → показывает аномальную активность

**Реализация:** Precompute job, раз в день (или после каждого нового data point). Пишем в `derivatives_timeseries` таблицу.

#### 4.4 — Derivatives API

**Новые эндпоинты:**
```
GET /api/derivatives/{symbol}
  → OI, funding, liquidations + z-scores + percentiles (latest)

GET /api/derivatives/{symbol}/history?days=365
  → Timeseries: price, OI, funding, liq, z-scores, percentiles

GET /api/derivatives/screener
  → Все символы: latest z-scores, sorted by extremes
```

#### 4.5 — Frontend: Derivatives Tab

Новый таб: `[Feed] [Funding Arb] [Analyzer] [Derivatives]`

**Layout:**
```
┌─ [BTC ▼] $87,234  OI: $28.5B (-7.2%)  Vol: $45.2B ──────┐
├───────────────────────────────────────────────────────────┤
│                                                           │
│  [Open Interest]      [Funding Rate]     [Liquidations]   │
│  ┌─── 1Y chart ───┐  ┌─── 1Y chart ──┐  ┌── 1Y chart ─┐ │
│  │   /\    /\     │  │  ─── ── ───   │  │  ▌▌ ▌▌  ▌▌  │ │
│  │  /  \  /  \    │  │               │  │             │ │
│  │ /    \/    \   │  │  ── ── ────   │  │  ▌▌   ▌▌   │ │
│  └────────────────┘  └───────────────┘  └─────────────┘ │
│  $28.5B  -7.2% 24h   0.01%  Ann: 7.8%   Delta: -$7M    │
│                                                           │
├───────────────────────────────────────────────────────────┤
│                                                           │
│  [OI Z-Score]        [Funding Z-Score]  [Liq Z-Score]    │
│  ┌─── 6M chart ───┐  ┌─── 6M chart ──┐ ┌── 6M chart ─┐ │
│  │  ----2σ----    │  │  ----2σ----   │ │ ----2σ----  │ │
│  │      /\        │  │    /\         │ │      /\     │ │
│  │  ----0----     │  │  ----0----    │ │ ----0----   │ │
│  │    \/          │  │     \/        │ │   \/        │ │
│  │  ---−2σ---     │  │  ---−2σ---    │ │ ---−2σ---   │ │
│  └────────────────┘  └───────────────┘ └─────────────┘ │
│  Z: 1.45  %ile: 25   Z: 1.75  %ile: 89  Z: -0.38  36  │
│                                                           │
├───────────────────────────────────────────────────────────┤
│ SCREENER (все символы)                                    │
│ Symbol │ Price    │ OI Z  │ Fund Z │ Liq Z  │ %ile Avg  │
│ BTC    │ $87,234  │  1.45 │  1.75  │ -0.38  │  50%      │
│ ETH    │ $2,072   │  2.10 │  0.85  │  1.20  │  72%      │
│ SOL    │ $142     │ -0.50 │  2.30  │  0.15  │  61%      │
│ DOGE   │ $0.18    │  3.20 │  1.90  │  2.50  │  95% !!!  │
│ ...    │          │       │        │        │           │
└───────────────────────────────────────────────────────────┘
```

**Как помогает торговле:**
- Ты видишь волну 5 на BTC → проверяешь OI Z-Score → если >2σ = все в лонгах → подтверждение разворота
- Funding Z > 2 → толпа перегрета → шорт-setup надёжнее
- Liquidation Z < -2 → массовые ликвидации шортов → bounce likely
- Screener → найти монету с экстремальным positioning = хороший entry

---

### Phase 5: BTC/ETH Options (опционально, потом)

**Зачем:** IV/RV показывает ожидания рынка. Высокий IV + твой волновой анализ = timing.

**Источник:** Deribit API (бесплатно, без авторизации).
```
GET https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option
```

**Данные:**
- IV term structure (7d, 30d, 90d, 180d)
- Realized Volatility
- 25-delta skew (put/call)
- VRP (Variance Risk Premium = IV - RV)

**Frontend:** Дополнительная секция на BTC/ETH detail page.

Это nice-to-have, не приоритет.

---

## Общая архитектура (после всех фаз)

```
Frontend (React 19 + Vite)
  ├── Feed Tab           — live events, token detail, watchlist
  ├── Funding Arb Tab    — spread scanner, rate comparison, charts
  ├── Token Analyzer Tab — risk scoring, red flags
  └── Derivatives Tab    — OI/funding/liq charts, z-scores, screener

Backend (FastAPI + aiohttp)
  ├── feed_engine.py        — DexScreener, GeckoTerminal, Etherscan, Helius
  ├── funding_service.py    — 11 exchange rates + spreads + history
  ├── derivatives_service.py — OI + liquidations + z-scores (NEW)
  ├── protocol_tracker.py   — DefiLlama TVL/yield
  ├── claude_service.py     — AI contract analysis
  └── security services     — GoPlus, Honeypot, RugCheck

Database (SQLite)
  ├── feed_events
  ├── funding_snapshots
  ├── daily_derivatives      (NEW: OI, liq per symbol per day)
  ├── derivatives_timeseries (NEW: precomputed z-scores)
  ├── token_cache, security_cache, analysis_cache
  ├── watchlist
  └── settings

External APIs (все бесплатные)
  ├── Binance, Bybit, OKX, MEXC, Bitget   — funding, OI, liquidations
  ├── Hyperliquid + 5 perp DEX            — funding rates
  ├── DexScreener, GeckoTerminal          — new pairs, trending
  ├── Etherscan V2, Helius                — whale transfers
  ├── GoPlus, Honeypot.is, RugCheck       — security
  ├── DefiLlama                           — protocol TVL/yield
  └── Deribit (потом)                     — options IV/RV
```

---

## Стоимость

```
Все API:      $0 (бесплатные тиры)
Инфра:        $0 (localhost, SQLite)
Если деплой:  $10-20/мо VPS
```

---

## Что уже сделано vs что осталось

| Компонент | Статус |
|-----------|--------|
| Feed Engine (new pairs, whales, protocol events) | DONE |
| WebSocket realtime broadcasting | DONE |
| Funding Rates (11 exchanges) | DONE |
| Spread Scanner | DONE |
| 7-Day Funding History + Backfill | DONE |
| Token Security (GoPlus, Honeypot, RugCheck) | DONE |
| Risk Scorer (0-100) | DONE |
| Claude AI Analysis | DONE |
| Protocol Tracker (TVL, Yield) | DONE |
| Tab Layout (Feed / Funding / Analyzer) | DONE |
| DEX/Protocol tags on tokens | DONE |
| **OI Pipeline (Binance/Bybit/OKX/Bitget)** | **TODO** |
| **Liquidations Pipeline** | **TODO** |
| **Z-Scores + Percentiles** | **TODO** |
| **Derivatives API** | **TODO** |
| **Derivatives Tab (frontend)** | **TODO** |
| **Screener Table** | **TODO** |
| BTC/ETH Options (Deribit) | LATER |
| Orderbook Depth | LATER |
