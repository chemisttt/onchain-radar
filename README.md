# On-Chain Radar

Личный дашборд для мониторинга on-chain активности в реальном времени. Live feed событий (новые пары, whale moves, price pumps/dumps, funding rate аномалии) + drill-down анализ токенов и контрактов с проверкой безопасности. Интеграция с Claude CLI для глубокого анализа исходного кода контрактов.

---

## Содержание

1. [Архитектура](#архитектура)
2. [Структура проекта](#структура-проекта)
3. [Установка и запуск](#установка-и-запуск)
4. [Backend](#backend)
   - [Точка входа и жизненный цикл](#точка-входа-и-жизненный-цикл)
   - [База данных](#база-данных)
   - [Сервисы](#сервисы)
   - [API роутеры](#api-роутеры)
   - [WebSocket](#websocket)
5. [Frontend](#frontend)
   - [Компоненты](#компоненты)
   - [Хуки](#хуки)
   - [Стейт-менеджмент](#стейт-менеджмент)
   - [Дизайн-система](#дизайн-система)
6. [Источники данных](#источники-данных)
7. [Rate Limits](#rate-limits)
8. [Конфигурация](#конфигурация)

---

## Архитектура

```
┌─────────────────────────────────────────────────────────┐
│                     Browser (5173)                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ FeedPanel│ │TokenPanel│ │Watchlist │ │FundingPanel│ │
│  │          │ │+Security │ │          │ │            │ │
│  │    WS ◄──┤ │+Claude   │ │  REST    │ │   REST     │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘ │
└───────┼────────────┼────────────┼──────────────┼────────┘
        │ ws://      │ http://    │ http://      │ http://
┌───────┼────────────┼────────────┼──────────────┼────────┐
│       ▼            ▼            ▼              ▼        │
│                  FastAPI (8000)                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │               Routers (API layer)                 │   │
│  │  /ws/feed  /api/feed  /api/tokens  /api/security │   │
│  │  /api/watchlist  /api/funding  /api/claude        │   │
│  └──────────┬───────────────────────────────────────┘   │
│             │                                           │
│  ┌──────────▼───────────────────────────────────────┐   │
│  │              Services (business logic)             │   │
│  │  feed_engine ─── dexscreener, geckoterminal       │   │
│  │                  etherscan, helius                 │   │
│  │  funding_service ─ Binance, Bybit, OKX, MEXC     │   │
│  │  claude_service ── Claude CLI subprocess          │   │
│  │  goplus, honeypot, rugcheck                       │   │
│  │  rate_limiter                                     │   │
│  └──────────┬───────────────────────────────────────┘   │
│             │                                           │
│  ┌──────────▼──────┐                                    │
│  │  SQLite (WAL)   │                                    │
│  │  radar.db       │                                    │
│  └─────────────────┘                                    │
└─────────────────────────────────────────────────────────┘
```

**Стек:**
- **Backend:** Python 3.10+, FastAPI, aiohttp, aiosqlite
- **Frontend:** React 19, Vite, Tailwind CSS 4, Zustand, TanStack Query
- **DB:** SQLite с WAL-режимом (~1-2 MB/день)
- **Claude:** CLI subprocess (через подписку, без API ключа)

---

## Структура проекта

```
onchain-radar/
├── backend/
│   ├── main.py                 # FastAPI app, lifespan, CORS, роутеры
│   ├── config.py               # Загрузка настроек из .env
│   ├── db.py                   # aiosqlite: init, get, close
│   ├── schema.sql              # DDL всех таблиц
│   ├── models.py               # Pydantic модели
│   ├── requirements.txt
│   ├── routers/
│   │   ├── feed.py             # GET /api/feed + WS /ws/feed
│   │   ├── tokens.py           # GET /api/tokens/{chain}/{address}
│   │   ├── security.py         # GET /api/security/{chain}/{address}
│   │   ├── watchlist.py        # CRUD /api/watchlist
│   │   ├── funding.py          # GET /api/funding/rates
│   │   ├── claude.py           # POST /api/claude/analyze (SSE)
│   │   └── settings.py         # CRUD /api/settings
│   ├── services/
│   │   ├── feed_engine.py      # Главный polling loop + WS broadcast
│   │   ├── dexscreener.py      # DexScreener API client
│   │   ├── geckoterminal.py    # GeckoTerminal API client (мультичейн)
│   │   ├── etherscan.py        # Etherscan V2 client (whale transfers)
│   │   ├── helius.py           # Helius client (Solana whale transfers)
│   │   ├── goplus.py           # GoPlus Security API (EVM + Solana)
│   │   ├── honeypot.py         # honeypot.is API (EVM only)
│   │   ├── rugcheck.py         # RugCheck API (Solana only)
│   │   ├── funding_service.py  # Funding rates (4 биржи)
│   │   ├── claude_service.py   # Claude CLI subprocess (SSE stream)
│   │   └── rate_limiter.py     # Token bucket per API
│   └── data/
│       └── radar.db            # SQLite database (gitignored)
├── frontend/
│   ├── vite.config.ts          # Vite + Tailwind + proxy → backend
│   └── src/
│       ├── App.tsx             # QueryClientProvider + layout
│       ├── index.css           # Тема (CSS variables через @theme)
│       ├── api/client.ts       # Axios instance
│       ├── store/feed.ts       # Zustand: events, filters, selection
│       ├── hooks/
│       │   ├── useFeed.ts      # WebSocket + REST fallback
│       │   ├── useTokenAnalysis.ts
│       │   ├── useSecurity.ts
│       │   ├── useWatchlist.ts
│       │   ├── useFunding.ts
│       │   └── useClaudeStream.ts  # SSE streaming hook
│       ├── components/
│       │   ├── layout/
│       │   │   ├── AppLayout.tsx   # 2x2 grid + header
│       │   │   ├── Panel.tsx       # Win10-style window
│       │   │   └── Skeleton.tsx    # Loading placeholder
│       │   ├── feed/
│       │   │   ├── FeedPanel.tsx
│       │   │   ├── FeedItem.tsx
│       │   │   └── FeedFilters.tsx
│       │   ├── token/
│       │   │   ├── TokenPanel.tsx
│       │   │   ├── TokenMetrics.tsx
│       │   │   ├── SecurityScore.tsx
│       │   │   └── ClaudeAnalysis.tsx
│       │   ├── watchlist/
│       │   │   └── WatchlistPanel.tsx
│       │   └── funding/
│       │       └── FundingPanel.tsx
│       └── utils/
│           ├── format.ts       # formatUsd, formatPercent, timeAgo, truncateAddress
│           └── chains.ts       # Маппинг чейнов: label, color, explorer URL
├── .env
└── .env.example
```

---

## Установка и запуск

### Зависимости

```bash
# Backend
cd onchain-radar/backend
pip install -r requirements.txt

# Frontend
cd onchain-radar/frontend
npm install
```

### Конфигурация

Скопировать `.env.example` → `.env` и заполнить ключи (опционально):

```env
ETHERSCAN_API_KEY=       # Etherscan V2 — whale transfers (бесплатный)
HELIUS_API_KEY=          # Helius — Solana whale transfers
GOPLUS_API_KEY=          # GoPlus — повышенные лимиты (опционально)
```

Без ключей работают: DexScreener, GeckoTerminal, honeypot.is, RugCheck, все funding rate API.

### Запуск

```bash
# Terminal 1 — backend
cd onchain-radar/backend
python3 -m uvicorn main:app --reload --port 8000

# Terminal 2 — frontend
cd onchain-radar/frontend
npm run dev
```

Открыть http://localhost:5173

---

## Backend

### Точка входа и жизненный цикл

**`main.py`** — FastAPI приложение с lifespan context manager:

```
Startup:
  1. init_db()           — создание SQLite, применение schema.sql
  2. feed_engine.start() — запуск background task (polling loop)
  3. funding_service.start() — запуск polling funding rates

Shutdown:
  1. feed_engine.stop()
  2. funding_service.stop()
  3. close_db()
```

CORS настроен на `http://localhost:5173` (Vite dev server).

Vite проксирует `/api/*` и `/ws/*` на `localhost:8000`, поэтому в браузере всё работает через один порт.

### База данных

**SQLite** с WAL-режимом (Write-Ahead Logging) для concurrent reads. Файл: `backend/data/radar.db`.

**Таблицы:**

| Таблица | Назначение |
|---------|-----------|
| `feed_events` | Все события фида (NEW_PAIR, WHALE_TRANSFER, PRICE_PUMP и т.д.) |
| `token_cache` | Кэш данных токенов от DexScreener (TTL 5 мин) |
| `security_cache` | Кэш результатов GoPlus/honeypot/RugCheck (TTL 15 мин) |
| `watchlist` | Токены в watchlist пользователя |
| `funding_snapshots` | История funding rates со всех бирж |
| `settings` | Ключ-значение настроек (JSON) |
| `claude_sessions` | История запросов к Claude с результатами |

Индексы на `feed_events`: по `created_at DESC`, `event_type`, `chain` — для быстрой фильтрации и пагинации.

### Сервисы

#### feed_engine.py — Главный движок фида

Background task (`asyncio.create_task`) с polling loop:

```
Каждые 10 секунд:
  └─ _poll_dexscreener()     — новые boosted токены

Каждые 30 секунд (tick % 3):
  └─ _poll_geckoterminal()   — 2 сети за тик (ротация)
     ├─ new_pools            — новые пулы
     └─ trending_pools       — pump/dump детекция (>20% за 1ч)

Каждые 60 секунд (tick % 6):
  └─ _poll_whale_transfers() — Etherscan + Helius
```

**Дедупликация:** `_seen_keys: set` хранит ключи вида `{event_type}:{chain}:{address}`. Очищается при > 10000 записей.

**GeckoTerminal ротация:** 8 сетей (eth, bsc, polygon, arbitrum, base, solana, avalanche, optimism). Каждый тик опрашивается 2 сети → полный цикл за ~2 минуты. Это нужно чтобы не превышать rate limit в 30 req/min.

**Broadcast:** Каждое новое событие сохраняется в SQLite и отправляется всем WebSocket клиентам через `ConnectionManager`.

#### dexscreener.py — DexScreener API

```
Base URL: https://api.dexscreener.com
Auth: не нужен
Rate limit: 300 req/min (5/sec)

Endpoints:
  /token-profiles/latest/v1  — последние boosted токены
  /token-boosts/latest/v1    — последние бусты
  /tokens/v1/{address}       — все пары для токена
  /pairs/v1/{chain}/{pair}   — конкретная пара
  /latest/dex/search?q=      — поиск по имени/символу
```

Используется для: новых пар в фиде, данных токена при клике (цена, объём, ликвидность, FDV, транзакции).

#### geckoterminal.py — GeckoTerminal API

```
Base URL: https://api.geckoterminal.com/api/v2
Auth: не нужен
Rate limit: 30 req/min

Endpoints:
  /networks/{net}/trending_pools  — трендовые пулы
  /networks/{net}/new_pools       — новые пулы

Сети: eth, bsc, polygon_pos, arbitrum, base, solana, avalanche, optimism
```

Основной источник мультичейн данных. Маппинг сетей GeckoTerminal → DexScreener chainId (например `eth` → `ethereum`, `polygon_pos` → `polygon`).

`parse_pool()` нормализует данные пула: извлекает символ из имени пула (например `"WETH / USDC 0.05%"` → `"WETH"`), маппит цену, объём, ликвидность.

#### etherscan.py — Etherscan V2

```
Base URL: https://api.etherscan.io/v2/api
Auth: API key (один ключ для всех 60+ EVM чейнов)
Rate limit: 5 req/sec, 100k req/day

Используется для:
  - Whale transfers (большие ETH переводы > 50 ETH)
  - Исходный код контракта (для Claude анализа)
```

`get_large_transfers()` — смотрит последние ~100 блоков через `txlistinternal`, фильтрует по value >= 50 ETH.

`get_contract_source()` — получает верифицированный исходный код контракта.

#### helius.py — Helius (Solana)

```
Base URL: https://api.helius.xyz
Auth: API key (обязателен)
Rate limit: 5 req/sec

Используется для: whale SOL transfers (> 500 SOL)
```

Парсит `nativeTransfers` из enhanced transactions API.

#### goplus.py — GoPlus Security

```
EVM:    https://api.gopluslabs.io/api/v1/token_security/{chain_id}
Solana: https://api.gopluslabs.io/api/v1/solana/token_security
Auth: опциональный Bearer token
Rate limit: ~2 req/sec

Возвращает (EVM):
  is_honeypot, is_mintable, can_take_back_ownership, hidden_owner,
  is_blacklisted, cannot_sell_all, is_proxy, buy_tax, sell_tax,
  holder_count, lp_holder_count, is_open_source, owner_address
```

GoPlus возвращает все значения как строки (`"1"` / `"0"`), сервис преобразует в bool/float.

Маппинг чейнов: `ethereum → 1`, `bsc → 56`, `polygon → 137`, `arbitrum → 42161`, `base → 8453`.

#### honeypot.py — honeypot.is

```
URL: https://api.honeypot.is/v2/IsHoneypot
Auth: не нужен
Поддержка: только EVM (5 чейнов: ETH, BSC, Polygon, Base, Arbitrum)

Возвращает:
  is_honeypot, honeypot_reason, buy_tax, sell_tax, buy_gas, sell_gas
```

Симулирует buy/sell транзакции для определения honeypot и точных значений tax.

#### rugcheck.py — RugCheck

```
URL: https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary
Auth: не нужен
Поддержка: только Solana

Возвращает:
  score (0 = safest, выше = рискованнее)
  risks [{name, level: danger|warn|info, score}]
  top_holders, token_name, token_symbol
```

#### funding_service.py — Funding Rates

Background task, polling каждые 60 секунд. Параллельный запрос к 4 биржам:

| Биржа | Endpoint | Особенности |
|-------|----------|------------|
| Binance | `fapi.binance.com/fapi/v1/premiumIndex` | Все USDM perps одним запросом |
| Bybit | `api.bybit.com/v5/market/tickers?category=linear` | Все linear perps одним запросом |
| OKX | `okx.com/api/v5/public/funding-rate` | Все SWAP инструменты |
| MEXC | `contract.mexc.com/api/v1/contract/funding_rate` | Все контракты |

**APR расчёт:** `rate × 3 × 365` (8-часовые периоды × 3 раза в день × 365 дней).

**FUNDING_EXTREME:** событие в фид при `|rate| >= 0.1%` за 8 часов.

Нормализация символов: OKX `BTC-USDT-SWAP` → `BTCUSDT`, MEXC `BTC_USDT` → `BTCUSDT`.

#### claude_service.py — Claude Analysis

Запускает Claude CLI как subprocess:

```bash
claude -p "<prompt>" \
  --output-format stream-json \
  --max-turns 15 \
  --allowedTools Read,Glob,Grep \
  --disallowedTools Write,Edit,Bash,NotebookEdit,Task,WebFetch,WebSearch
```

**System prompt:** роль — contract security analyst. Анализирует ownership risks, supply risks, trading restrictions, proxy/upgrade risks, liquidity risks, known exploit patterns.

**Контекст:** включает результаты GoPlus/honeypot/RugCheck из кэша.

**Streaming:** stdout читается через `asyncio.subprocess.PIPE`, JSON парсится построчно. Тип `assistant` с `content[].type === "text"` отдаётся клиенту как SSE event.

**Безопасность:** Claude может только читать файлы (Read, Glob, Grep). Запись, редактирование, запуск команд — запрещены.

#### rate_limiter.py — Token Bucket

Каждый API имеет свой bucket:

| API | Rate (req/sec) | Burst capacity |
|-----|---------------|----------------|
| dexscreener | 5 | 5 |
| geckoterminal | 0.4 | 3 |
| etherscan | 5 | 5 |
| goplus | 2 | 5 |
| honeypot | 2 | 5 |
| rugcheck | 1 | 3 |
| helius | 5 | 10 |

`await acquire("dexscreener")` — ждёт пока bucket не разрешит запрос. Thread-safe через `asyncio.Lock`.

### API роутеры

#### GET /api/feed

Пагинированный список событий из `feed_events`.

```
Query params:
  limit   — кол-во (1-200, default: 50)
  offset  — смещение (default: 0)
  event_type — фильтр: NEW_PAIR, WHALE_TRANSFER, PRICE_PUMP, ...
  chain   — фильтр: ethereum, bsc, solana, ...

Response: [{id, event_type, chain, token_address, pair_address, token_symbol, details, severity, created_at}]
```

#### GET /api/tokens/{chain}/{address}

Данные токена от DexScreener. Кэш 5 минут в `token_cache`.

```
Response: {
  chain, address, cached,
  data: {
    pair_address, dex, base_token, quote_token,
    price_usd, price_native, volume, price_change,
    liquidity, fdv, market_cap, txns,
    pair_created_at, url, all_pairs_count
  }
}
```

Если у токена несколько пар — выбирается пара с наибольшей ликвидностью на нужном чейне.

#### GET /api/security/{chain}/{address}

Параллельные проверки безопасности. Кэш 15 минут.

```
EVM:    GoPlus (EVM) + honeypot.is — параллельно
Solana: GoPlus (Solana) + RugCheck — параллельно

Response: { chain, address, goplus, honeypot, rugcheck, cached }
```

#### POST /api/claude/analyze

SSE stream. Body: `{chain, address, prompt?}`.

```
SSE events:
  data: {"type": "text", "content": "..."}     — текст от Claude
  data: {"type": "error", "content": "..."}    — ошибка
  data: {"type": "done", "session_id": "..."}  — завершение
```

#### CRUD /api/watchlist

```
GET    /api/watchlist         — список всех
POST   /api/watchlist         — добавить {chain, address, symbol?, name?, notes?}
DELETE /api/watchlist/{id}    — удалить
GET    /api/watchlist/prices  — batch цены для всех через DexScreener
```

#### GET /api/funding/rates

```
Query params:
  symbol — фильтр по символу (partial match, case insensitive)

Response: [{
  symbol: "BTCUSDT",
  rates: {
    "Binance": {rate: 0.0001, apr: 0.1095, next_funding_time: 1709568000000},
    "Bybit":   {rate: 0.00012, apr: 0.1314, next_funding_time: null},
    ...
  }
}]

Сортировка: по max |rate| across exchanges (убывание).
Лимит: 100 символов.
```

#### CRUD /api/settings

```
GET /api/settings         — все настройки
GET /api/settings/{key}   — одна настройка
PUT /api/settings         — обновить {key, value}
```

### WebSocket

```
WS /ws/feed

Клиент подключается, сервер отправляет:

1. Feed events (при появлении нового):
   {"type": "feed_event", "data": {id, event_type, chain, ...}}

2. Heartbeat (каждые 10 секунд):
   {"type": "heartbeat", "timestamp": 1709568123}
```

`ConnectionManager` — хранит список активных WS соединений, broadcast отправляет JSON всем. Мёртвые соединения автоматически удаляются при ошибке отправки.

---

## Frontend

### Компоненты

#### AppLayout

Grid 2x2 с header:

```
┌──────────────────────────────────────┐
│  ON-CHAIN RADAR    [LIVE]    12:34:56│
├──────────────────┬───────────────────┤
│                  │                   │
│    FeedPanel     │   TokenPanel      │
│                  │   + SecurityScore │
│                  │   + ClaudeAnalysis│
├──────────────────┼───────────────────┤
│                  │                   │
│  WatchlistPanel  │   FundingPanel    │
│                  │                   │
└──────────────────┴───────────────────┘
```

Header показывает живые часы (обновление каждую секунду).

#### Panel

Базовый компонент — окно в стиле Windows 10 flat:
- Titlebar: темнее основного фона, uppercase label, опциональные actions
- Content: скроллируемая область с padding
- Стиль: 1px border, без border-radius, без теней

#### FeedPanel

- Инициализация: REST `GET /api/feed?limit=100` → начальные данные
- Обновления: WebSocket `/ws/feed` → новые события в реальном времени
- Фильтры: по чейну (ETH, BSC, SOL, ...) и типу (NEW, WHALE, PUMP, ...)
- Авто-реконнект WebSocket через 3 секунды при обрыве
- Макс 500 событий в памяти (старые отбрасываются)

#### FeedItem

Строка события:
```
[SOL] [NEW] 7Qb1oz...  3m ago
```
- Chain badge с цветом чейна
- Type label с цветом по типу (синий=NEW, жёлтый=WHALE, зелёный=PUMP, красный=DUMP)
- Символ токена или truncated address
- Relative time (timeAgo)
- Клик → выбор в store → TokenPanel загружает данные

#### TokenPanel

При выборе события из фида:

1. **Header:** chain badge + symbol + name + explorer link + кнопка "+Watch"
2. **TokenMetrics:** grid 3 колонки — price, liquidity, vol24h, FDV, MCap, pairs count, price changes (5m/1h/24h color-coded), buys/sells 24h, DEX
3. **SecurityScore:** safety score 0-100, flag badges (HONEYPOT, MINTABLE, PROXY, VERIFIED, ...), buy/sell tax, RugCheck score (Solana)
4. **ClaudeAnalysis:** кнопка "Analyze" → SSE streaming текста от Claude, expandable/collapsible, кнопка Stop
5. **DexScreener link**

#### SecurityScore

Расчёт safety score (0-100, выше = безопаснее):

```
100 (base)
 -50  is_honeypot
 -20  cannot_sell_all
 -15  is_mintable
 -15  can_take_back_ownership
 -10  hidden_owner
 -10  is_blacklisted
 -10  buy_tax > 5%
 -10  sell_tax > 5%
 -10  !is_open_source
  -5  is_proxy

Цвет: >= 70 зелёный, >= 40 жёлтый, < 40 красный
```

#### ClaudeAnalysis

- Кнопка "Analyze" → `fetch('/api/claude/analyze', {method: 'POST'})` с SSE
- AbortController для отмены (кнопка "Stop")
- Streaming: текст добавляется по мере получения с мигающим курсором `|`
- Expandable: `[+]` / `[-]` для сворачивания
- Результат в monospace блоке с скроллом (max-height 300px)

#### WatchlistPanel

- Список токенов с chain badge, symbol, live price, 24h change
- Цены обновляются каждые 30 секунд (`refetchInterval: 30000`)
- Клик → открывает в TokenPanel
- Кнопка `x` для удаления
- "+Watch" кнопка в TokenPanel добавляет текущий токен

#### FundingPanel

Таблица: строки = символы, колонки = биржи (Binance, Bybit, OKX, MEXC).

Каждая ячейка: `rate%` + `(APR%)` в скобках.

Цветовая кодировка:
- Зелёный: rate > 0.05% (лонги платят — можно шортить)
- Красный: rate < -0.05% (шорты платят — можно лонгить)
- Bold: extreme rates (|rate| >= 0.1%)

Обновление каждые 60 секунд.

### Хуки

| Хук | Тип | Описание |
|-----|-----|---------|
| `useFeed` | WebSocket + REST | Подключение к WS, загрузка истории, авто-реконнект |
| `useTokenAnalysis` | TanStack Query | `GET /api/tokens/{chain}/{address}`, staleTime 5 мин |
| `useSecurity` | TanStack Query | `GET /api/security/{chain}/{address}`, staleTime 15 мин |
| `useWatchlist` | TanStack Query + Mutations | list, prices, add, remove |
| `useFunding` | TanStack Query | `GET /api/funding/rates`, refetchInterval 60 сек |
| `useClaudeStream` | SSE fetch | POST → streaming text, abort, reset |

#### useClaudeStream подробнее

```typescript
const { text, isStreaming, analyze, stop, reset } = useClaudeStream()

analyze(chain, address, prompt?)  // Запуск анализа
stop()                            // AbortController.abort()
reset()                           // Очистка текста и стопа
```

SSE парсинг:
1. `fetch()` с `ReadableStream`
2. `TextDecoder` с `{stream: true}` (UTF-8 chunks)
3. Буфер → split по `\n\n` → парсинг `data: {json}`
4. Тип `text` → append к state

### Стейт-менеджмент

**Zustand** (`store/feed.ts`):

```typescript
{
  events: FeedEvent[]          // Макс 500, newest first
  selectedEvent: FeedEvent     // Текущий выбранный
  chainFilter: string | null   // Фильтр по чейну
  typeFilter: string | null    // Фильтр по типу

  addEvent(event)              // Prepend + trim to 500
  setEvents(events)            // Bulk set (REST fallback)
  selectEvent(event)           // Выбор → TokenPanel обновляется
  setChainFilter(chain)
  setTypeFilter(type)
}
```

**TanStack Query** — кэширование REST запросов с автоматической инвалидацией.

### Дизайн-система

Стиль: **umi.bot / Windows 10 flat dark**.

```css
--bg-app:      #0a0a0a    /* Фон приложения */
--bg-panel:    #141414    /* Фон панели */
--bg-titlebar: #1a1a1a    /* Фон titlebar / hover */
--border:      #2a2a2a    /* Все бордеры */
--text-primary:   #e0e0e0
--text-secondary: #808080
--green:  #22c55e    /* Позитивное: рост цены, safe */
--red:    #ef4444    /* Негативное: падение, danger */
--yellow: #eab308    /* Warning, watchlist */
--blue:   #3b82f6    /* Links, neutral info */
```

Правила:
- **Без border-radius** — все элементы с прямыми углами
- **Без теней** — только 1px borders
- **Без градиентов**
- Шрифт: Inter (основной), JetBrains Mono (числа, адреса, код)
- Размеры текста: `text-xs` (12px) основной, `text-[10px]` для labels/badges

---

## Источники данных

### Фид событий

| Тип события | Источник | Частота | Условие |
|------------|----------|---------|---------|
| `NEW_PAIR` | DexScreener profiles | 10 сек | Новый boosted токен |
| `NEW_PAIR` | GeckoTerminal new_pools | 30 сек | Новый пул на сети |
| `PRICE_PUMP` | GeckoTerminal trending | 30 сек | price_change_1h > +20% |
| `PRICE_DUMP` | GeckoTerminal trending | 30 сек | price_change_1h < -20% |
| `WHALE_TRANSFER` | Etherscan V2 | 60 сек | ETH transfer >= 50 ETH |
| `WHALE_TRANSFER` | Helius | 60 сек | SOL transfer >= 500 SOL |
| `FUNDING_EXTREME` | 4 CEX биржи | 60 сек | \|rate\| >= 0.1% / 8h |

### Анализ токена (при клике)

| Данные | Источник | Кэш |
|--------|----------|-----|
| Цена, объём, ликвидность, FDV | DexScreener pairs | 5 мин |
| Honeypot, taxes, mintable, proxy (EVM) | GoPlus + honeypot.is | 15 мин |
| Rug score, risks (Solana) | GoPlus + RugCheck | 15 мин |
| Глубокий анализ контракта | Claude CLI | нет |

### Funding rates

| Биржа | Данные | Обновление |
|-------|--------|-----------|
| Binance | Все USDM perps | 60 сек |
| Bybit | Все linear perps | 60 сек |
| OKX | Все SWAP инструменты | 60 сек |
| MEXC | Все контракты | 60 сек |

---

## Rate Limits

Все лимиты управляются через token bucket в `rate_limiter.py`:

| API | Лимит | Наша нагрузка |
|-----|-------|--------------|
| DexScreener | 300/min, 5/sec | ~6 req/min (profiles every 10s) |
| GeckoTerminal | 30/min | ~8 req/min (2 networks × 2 endpoints every 30s) |
| Etherscan V2 | 5/sec, 100k/day | ~2 req/min |
| Helius | зависит от плана | ~1 req/min |
| GoPlus | ~2/sec | По запросу (при клике) |
| honeypot.is | ~2/sec | По запросу (при клике) |
| RugCheck | ~1/sec | По запросу (при клике) |
| Binance futures | high | 1 req/min |
| Bybit | 120/5sec | 1 req/min |
| OKX | varies | 1 req/min |
| MEXC | varies | 1 req/min |

---

## Конфигурация

### .env

```env
# Обязательные для whale detection:
ETHERSCAN_API_KEY=        # Бесплатный: etherscan.io/register
HELIUS_API_KEY=           # Бесплатный tier: helius.dev

# Опциональные (повышают лимиты):
GOPLUS_API_KEY=           # gopluslabs.io

# Сервер:
HOST=0.0.0.0
PORT=8000
```

### Что работает без ключей

- DexScreener (новые пары, данные токенов)
- GeckoTerminal (мультичейн пулы, trending)
- honeypot.is (EVM honeypot check)
- RugCheck (Solana security)
- Funding rates (Binance, Bybit, OKX, MEXC)
- Claude CLI (через подписку Claude)

### Что требует ключей

- Etherscan V2 — whale transfers на EVM
- Helius — whale transfers на Solana
- GoPlus — опционально, для повышенных лимитов (работает и без ключа)
