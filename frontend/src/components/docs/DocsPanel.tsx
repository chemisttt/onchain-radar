import { useState, useCallback, type ReactNode } from 'react'

/* ─── Helper Components ─── */

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="mb-10 scroll-mt-20">
      <h2 className="text-lg font-semibold text-text-primary border-b border-[#222] pb-2 mb-4">
        <a href={`#${id}`} className="hover:text-[#5ba3ad] transition-colors">{title}</a>
      </h2>
      {children}
    </section>
  )
}

function P({ children }: { children: ReactNode }) {
  return <p className="text-sm text-[#b0b0b0] leading-relaxed mb-3">{children}</p>
}

function Code({ children }: { children: string }) {
  return (
    <pre className="bg-[#111] border border-[#222] rounded p-4 font-mono text-xs text-[#a0a0a0] overflow-x-auto mb-4 whitespace-pre">
      {children}
    </pre>
  )
}

function Table({ headers, rows }: { headers: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto mb-4">
      <table className="w-full text-xs border border-[#222]">
        <thead>
          <tr className="bg-[#111]">
            {headers.map((h, i) => (
              <th key={i} className="text-left px-3 py-2 text-[#999] font-medium border-b border-[#222]">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className="px-3 py-2 text-[#b0b0b0] border-b border-[#1a1a1a]">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Screenshot({ src, caption }: { src: string; caption: string }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <>
      <figure className="my-4">
        <img
          src={src}
          alt={caption}
          className="rounded border border-[#222] max-w-full cursor-pointer hover:border-[#444] transition-colors"
          onClick={() => setExpanded(true)}
        />
        <figcaption className="text-xs text-[#666] italic mt-1">{caption}</figcaption>
      </figure>
      {expanded && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center cursor-pointer p-8"
          onClick={() => setExpanded(false)}
        >
          <img src={src} alt={caption} className="max-w-full max-h-full rounded" />
        </div>
      )}
    </>
  )
}

function Strategy({ children }: { children: ReactNode }) {
  return (
    <div className="border-l-2 border-[#eab308] bg-[#0f0f0f] p-4 rounded-r mb-4">
      {children}
    </div>
  )
}

function B({ children }: { children: ReactNode }) {
  return <strong className="text-[#e2e8f0]">{children}</strong>
}

/* ─── Table of Contents ─── */

const TOC = [
  { id: 'overview', label: '1. Обзор дашборда' },
  { id: 'screener', label: '2. Screener' },
  { id: 'open-interest', label: '3. Open Interest' },
  { id: 'funding-rate', label: '4. Funding Rate' },
  { id: 'liquidations', label: '5. Liquidations' },
  { id: 'volume', label: '6. Volume' },
  { id: 'composite-regime', label: '7. Composite Regime' },
  { id: 'z-scatter', label: '8. Z-Score Scatter Plots' },
  { id: 'liq-map', label: '9. Liquidation Map' },
  { id: 'iv-rv', label: '10. IV/RV/Skew' },
  { id: 'vrp', label: '11. Variance Risk Premium' },
  { id: 'vol-cone', label: '12. Volatility Cone' },
  { id: 'momentum', label: '13. Momentum Indicator' },
  { id: 'di-vr-scatter', label: '14. DI/VR vs Forward Return' },
  { id: 'price-dist', label: '15. Price Distribution' },
  { id: 'signal-gauges', label: '16. Signal Gauges' },
  { id: 'orderbook', label: '17. Orderbook Depth & Skew' },
  { id: 'global', label: '18. Global Dashboard' },
  { id: 'alt-oi', label: '19. Altcoin OI Dominance' },
  { id: 'funding-arb', label: '20. Funding Arb' },
  { id: 'live-feed', label: '21. Live Feed' },
  { id: 'appendix-a', label: 'Appendix A: Формулы' },
  { id: 'appendix-b', label: 'Appendix B: Интервалы обновления' },
]

/* ─── Main Component ─── */

export default function DocsPanel() {
  const scrollTo = useCallback((id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  return (
    <div className="h-full overflow-y-auto bg-[#0a0a0a] font-mono">
      <article className="max-w-5xl mx-auto px-8 py-6">
        <h1 className="text-2xl font-bold text-text-primary mb-2">Metrics Guide — On-Chain Radar</h1>
        <P>Полный гайд по метрикам деривативного дашборда. Каждая метрика: что показывает, как читать, торговая стратегия.</P>

        {/* Table of Contents */}
        <nav className="bg-[#0c0c0c] border border-[#1a1a1a] rounded p-4 mb-8">
          <h3 className="text-sm font-semibold text-[#999] mb-3">Содержание</h3>
          <div className="grid grid-cols-2 gap-1">
            {TOC.map((item) => (
              <button
                key={item.id}
                onClick={() => scrollTo(item.id)}
                className="text-left text-xs text-[#5ba3ad] hover:underline hover:text-[#7cc4ce] transition-colors py-0.5"
              >
                {item.label}
              </button>
            ))}
          </div>
        </nav>

        {/* ── 1. Обзор дашборда ── */}
        <Section id="overview" title="1. Обзор дашборда">
          <P>Дашборд мониторит 30 перп-символов на 4 биржах (Binance, Bybit, OKX, Bitget).</P>
          <P><B>Навигация верхнего уровня:</B></P>
          <ul className="text-sm text-[#b0b0b0] mb-3 ml-4 list-disc space-y-1">
            <li><B>Feed</B> — Live Feed событий (whale transfers, new pairs, TVL spikes, yield)</li>
            <li><B>Funding Arb</B> — Арбитражные спреды между 11 биржами</li>
            <li><B>Token Analyzer</B> — Анализ токенов (security score, Claude AI)</li>
            <li><B>Derivatives</B> — Основной аналитический модуль</li>
          </ul>
          <P><B>Вкладки внутри Derivatives:</B></P>
          <ul className="text-sm text-[#b0b0b0] mb-3 ml-4 list-disc space-y-1">
            <li><B>Analysis</B> — Composite Regime + OI/Funding/Liq/Volume чарты + Z-Scores + Scatter + Liq Map</li>
            <li><B>IV/RV</B> — Implied/Realized Volatility, VRP, Skew, Volatility Cone (BTC/ETH)</li>
            <li><B>Momentum</B> — Multi-component momentum score, DI/VR, scatter plots, price distribution</li>
            <li><B>Global</B> — Risk Appetite, Alt OI Dominance, Global OI, Heatmap, Performance</li>
          </ul>
          <Screenshot src="/docs/02-derivatives-analysis.png" caption="Derivatives → Analysis: Composite Regime + Perpetuals Data (OI, Funding, Liquidations)" />
        </Section>

        {/* ── 2. Screener ── */}
        <Section id="screener" title="2. Screener">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Таблица всех 30 символов с z-scores по 4 метрикам и средним перцентилем. Быстрый скан рынка для поиска аномалий.</P>
          <Screenshot src="/docs/02-derivatives-analysis.png" caption="Screener внизу экрана: Symbol, Price, OI, 24H OI%, OI Z, OI %ile, Fund, Fund Z, Fund %ile, Liq Z, Liq %ile, Volume, Vol Z, OB Depth, OB Skew Z, %ile Avg" />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Колонки</h3>
          <Table
            headers={['Колонка', 'Описание']}
            rows={[
              ['Symbol', 'Тикер (BTCUSDT, ETHUSDT, ...)'],
              ['Price', 'Текущая цена (Binance)'],
              ['OI z', 'Z-score открытого интереса'],
              ['Fund z', 'Z-score фандинга'],
              ['Liq z', 'Z-score ликвидаций (абс. значение дельты)'],
              ['Vol z', 'Z-score объёма'],
              ['OB Skew Z', 'Z-score дисбаланса ордербука'],
              ['%ile Avg', 'Среднее 4 перцентилей'],
              ['OI Δ24h', 'Изменение OI за 24ч (%)'],
            ]}
          />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Цветовая кодировка z-scores</h3>
          <Code>{`z ≥ +2   → красный    (экстремум вверх)
z ≥ +1   → жёлтый     (повышенный)
z ≤ -1   → синий      (пониженный)
z ≤ -2   → зелёный    (экстремум вниз)
иначе    → серый      (норма)`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Как читать</h3>
          <ul className="text-sm text-[#b0b0b0] mb-3 ml-4 list-disc space-y-1">
            <li><B>%ile Avg ≥ 80</B> — символ в экстремальном состоянии по нескольким метрикам</li>
            <li><B>%ile Avg ≥ 60</B> — повышенная активность, потенциальный кандидат</li>
            <li>Сортировка по <code className="text-[#a0a0a0] bg-[#111] px-1 rounded">abs(z)</code> — выносит наверх самые аномальные значения</li>
          </ul>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: Выбор кандидатов</h3>
          <Strategy>
            <Code>{`Шаг 1: Отсортировать по %ile Avg (desc)
Шаг 2: Выбрать символы с %ile Avg ≥ 60
Шаг 3: Проверить дивергенции:

  ┌─────────────────────────────────────────────────┐
  │  OI_z высокий + Fund_z низкий = накопление      │
  │  OI_z высокий + Fund_z высокий = перегрев       │
  │  OI_z падает  + Price растёт = дивергенция (!)  │
  │  Vol_z > 2    + любой другой z > 1 = breakout   │
  └─────────────────────────────────────────────────┘

Шаг 4: Кликнуть символ → перейти в Detail`}</Code>
          </Strategy>
        </Section>

        {/* ── 3. Open Interest ── */}
        <Section id="open-interest" title="3. Open Interest">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Суммарный открытый интерес в USD по 4 биржам. Растущий OI = новые позиции открываются, падающий = позиции закрываются.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Агрегация</h3>
          <Code>{`total_oi = binance_oi_usd
         + bybit_oi_coins  × binance_price
         + okx_oi_coins    × binance_price
         + bitget_oi_usd`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Как читать</h3>
          <Code>{`         OI ($M)
    200 ┤
        │         ╭──╮
    180 ┤    ╭───╯   │     ← OI растёт: новые позиции
        │   ╭╯        │
    160 ┤──╯          ╰──── ← OI падает: ликвидации / закрытие
        │
    140 ┤
        └──────────────────── время

  Z-Score:
   +2 ┤ · · · · · · · · · ·  ← экстремум: перегрев рынка
   +1 ┤ - - - - - - - - - -
    0 ┤────────────────────── ← среднее (365 дней)
   -1 ┤ - - - - - - - - - -
   -2 ┤ · · · · · · · · · ·  ← экстремум: вымытые позиции`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: OI/Price дивергенция</h3>
          <Strategy>
            <Code>{`Сценарий 1: Цена ↑ + OI ↑ = подтверждённый тренд
  → Тренд здоровый, удерживать позицию

Сценарий 2: Цена ↑ + OI ↓ = дивергенция (разворот)
  → Рост на закрытии шортов, топ близко
  → Искать шорт при касании наклонной сверху

Сценарий 3: Цена ↓ + OI ↑ = накопление шортов
  → Агрессивные шорты, потенциальный шорт-сквиз
  → Готовиться к лонгу на сквиз-уровнях

Сценарий 4: OI_z > +2 = перегрев
  → Каскадные ликвидации вероятны
  → Не открывать новые позиции, ждать разгрузку`}</Code>
          </Strategy>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Привязка к волновой теории</h3>
          <Code>{`  Волна 1: OI начинает расти с низов (z < -1)
  Волна 3: OI резко ускоряется (z переходит в +1..+2)
  Волна 5: OI на максимуме (z > +2), дивергенция с ценой
  Коррекция A: OI резко падает, каскад ликвидаций`}</Code>
        </Section>

        {/* ── 4. Funding Rate ── */}
        <Section id="funding-rate" title="4. Funding Rate">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Периодическая выплата между лонгами и шортами для удержания перп-цены у спота. Положительный фандинг = лонги платят шортам (рынок перекуплен). Отрицательный = шорты платят лонгам.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Settlement cycles</h3>
          <Table
            headers={['Биржа', 'Период', 'Нормализация']}
            rows={[
              ['Binance, Bybit, OKX, MEXC', '8ч', 'raw'],
              ['Hyperliquid, Lighter', '1ч', '× 8'],
            ]}
          />
          <P><B>Annualized:</B> <code className="text-[#a0a0a0] bg-[#111] px-1 rounded">rate × 3 × 365 × 100%</code></P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Как читать</h3>
          <Code>{`  Funding Rate (8h)
  +0.05% ┤                 ╭─╮
         │              ╭──╯ │    ← лонги платят: рынок перегрет
  +0.01% ┤─ ─ ─ ─ ─ ─╱─ ─ ─ ─ ─  ← нейтраль
       0 ┤───────────/────────╰──
  -0.01% ┤─ ─ ─ ─ ╱─ ─ ─ ─ ─ ─
         │       ╭╯              ← шорты платят: рынок перепродан
  -0.05% ┤──────╯
         └──────────────────────── время`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: Mean Reversion на экстремумах</h3>
          <Strategy>
            <Code>{`  Funding Z-Score
  +2 ┤ · · · · · · · ╭─╮· ·  ← ШОРТ ЗОНА: funding_z > +2
     │              ╱   ╰╮
  +1 ┤ - - - - - -╱- - - ╰─
   0 ┤────────────────────────
  -1 ┤ - - - - - - - - - - -
  -2 ┤ · · · · · · · · · · ·  ← ЛОНГ ЗОНА: funding_z < -2
     └────────────────────────

  Правила:
  1. Funding_z > +2 → ищем шорт
  2. Funding_z < -2 → ищем лонг
  3. Подтверждение: совпадение с наклонной/зоной интереса
  4. Фильтр: не торговать в изоляции — только с контекстом`}</Code>
          </Strategy>
        </Section>

        {/* ── 5. Liquidations ── */}
        <Section id="liquidations" title="5. Liquidations">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Реал-тайм ликвидации с Binance и Bybit через WebSocket. Delta = long_liq - short_liq. Каскады ликвидаций создают точки разворота.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">WebSocket источники</h3>
          <Table
            headers={['Биржа', 'Endpoint', 'Side mapping']}
            rows={[
              ['Binance', '!forceOrder@arr', 'SELL = long liquidated'],
              ['Bybit', 'allLiquidation', 'Sell = long liquidated'],
            ]}
          />
          <P><B>Batch flush:</B> 3 события ИЛИ 10 секунд.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Как читать</h3>
          <Code>{`  Liq Delta ($M)
  +5 ┤          │         ← массовые ликвидации лонгов
     │          │            (давление продаж)
   0 ┤──────────┼──────────
     │     │    │    │
  -5 ┤     │         │    ← массовые ликвидации шортов
     └──────────────────── время`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: Каскады как точки разворота</h3>
          <Strategy>
            <Code>{`  1. liq_z > +2 + delta резкий всплеск → каскад
  2. После каскада: вся слабость выжжена
  3. Ищем лонг ПОСЛЕ каскада (не во время)
  4. Подтверждение: OI упал (z < -1), funding стал отрицательным`}</Code>
          </Strategy>
        </Section>

        {/* ── 6. Volume ── */}
        <Section id="volume" title="6. Volume">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Торговый объём в USD (Binance). Z-score показывает аномалии.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: Volume Confirmation</h3>
          <Strategy>
            <Code>{`  ┌────────────────────────────────────────────────┐
  │  vol_z > +2 + пробой уровня = настоящий breakout│
  │  vol_z > +2 + нет пробоя    = кульминация       │
  │  vol_z < 0  + тренд         = затухание тренда  │
  │  vol_z < -1 + боковик       = накопление/распр.  │
  └────────────────────────────────────────────────┘`}</Code>
          </Strategy>
          <P><B>Важно:</B> Volume z-score НЕ входит в composite regime.</P>
        </Section>

        {/* ── 7. Composite Regime ── */}
        <Section id="composite-regime" title="7. Composite Regime">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Среднее трёх z-scores: OI + Funding + Liquidations + SMA-5 smoothing. Показывает «температурный режим» рынка.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Формула</h3>
          <Code>{`composite_z     = (oi_zscore + funding_zscore + liq_zscore) / 3
composite_sma5  = SMA(composite_z, 5)   ← жёлтая линия на графике`}</Code>

          <Screenshot src="/docs/02-derivatives-analysis.png" caption="Composite Regime: верх — Price chart (белая линия), низ — Composite Z bars (цветные) + SMA-5 overlay (жёлтая)" />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">6 режимов (цветовая шкала)</h3>
          <Code>{`     ≤ -2   │ ██ Deep Oversold  │ зелёный
  -2 .. -1  │ ██ Oversold       │ бирюзовый
  -1 ..  0  │ ██ Neutral Cool   │ лайм
   0 .. +1  │ ██ Neutral Hot    │ жёлтый
  +1 .. +2  │ ██ Overbought     │ оранжевый
     > +2   │ ██ Extreme        │ красный`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: Regime Transitions</h3>
          <Strategy>
            <Code>{`  Переход из красного в оранжевый:
  → Рынок начинает остывать → ищем шорт

  Переход из зелёного в бирюзовый:
  → Рынок начинает восстанавливаться → ищем лонг

  Фильтр: НЕ торговать переходы внутри нейтральной зоны.
  Только экстремумы (green/red) дают высоковероятные сетапы.`}</Code>
          </Strategy>
        </Section>

        {/* ── 8. Z-Score Scatter Plots ── */}
        <Section id="z-scatter" title="8. Z-Score Scatter Plots">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Scatter plot: X = z-score метрики в момент T, Y = forward return через N дней. Показывает предсказательную силу z-score для будущего движения цены.</P>
          <Screenshot src="/docs/03-zscores-scatter.png" caption="OI Z vs Fwd Return, Funding Z vs Fwd Return, Liq Z vs Fwd Return — с R², n, Avg at current" />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Периоды</h3>
          <P>Переключатели: <B>10d</B>, <B>30d</B> (default), <B>60d</B>.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Статистика</h3>
          <Code>{`  R² = коэффициент детерминации (0..1)
  n  = количество исторических точек
  Avg at current = средний return при текущем z

  ● = историческая точка (серая)
  ★ = текущая позиция (жёлтая)`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: Вероятностный edge</h3>
          <Strategy>
            <Code>{`  ┌─────────────────────────────────────────────┐
  │  R² > 0.15 → есть статистическая связь      │
  │  R² > 0.30 → сильная связь, можно торговать │
  │  R² < 0.10 → z-score не предсказывает return │
  └─────────────────────────────────────────────┘

  Если текущий z < -2 и Avg at current > +10%:
  → Лонг на зоне интереса

  Если текущий z > +2 и Avg at current < -10%:
  → Шорт на наклонной сверху`}</Code>
          </Strategy>
        </Section>

        {/* ── 9. Liquidation Map ── */}
        <Section id="liq-map" title="9. Liquidation Map">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Карта теоретических уровней ликвидации на основе текущей цены и распределения плеч.</P>
          <Screenshot src="/docs/03-zscores-scatter.png" caption="Liquidation Map: горизонтальные бары — теоретические ликвидационные уровни на разных плечах (5x, 10x, 25x, 50x, 100x)" />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Leverage Tiers</h3>
          <Table
            headers={['Плечо', 'Вес (% от OI)', 'Liq Long', 'Liq Short']}
            rows={[
              ['5×', '10%', 'price × 0.80', 'price × 1.20'],
              ['10×', '25%', 'price × 0.90', 'price × 1.10'],
              ['25×', '30%', 'price × 0.96', 'price × 1.04'],
              ['50×', '20%', 'price × 0.98', 'price × 1.02'],
              ['100×', '15%', 'price × 0.99', 'price × 1.01'],
            ]}
          />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: Ликвидационные магниты</h3>
          <Strategy>
            <Code>{`  Кластеры ликвидаций работают как магниты для цены.

  1. Определить ближайший крупный кластер
  2. Кластер сверху = магнит для роста (шорт-сквиз)
  3. Кластер снизу = магнит для падения (лонг-сквиз)
  4. После забора кластера — быстрый разворот

  Если ликвидационный кластер совпадает с наклонной:
  → Усиленный магнит
  → Высокая вероятность реакции на этом уровне`}</Code>
          </Strategy>
        </Section>

        {/* ── 10. IV/RV/Skew ── */}
        <Section id="iv-rv" title="10. IV/RV/Skew (вкладка IV/RV)">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Опционные метрики для BTC и ETH (Deribit). IV = ожидаемая волатильность, RV = реализованная, Skew = перекос путов к коллам.</P>
          <P><B>Только BTC и ETH</B> — для остальных символов доступен только RV.</P>
          <Screenshot src="/docs/04-ivrv-tab.png" caption="IV/RV вкладка: Price/IV/RV chart (dual-axis), VRP bar chart, 25d Skew Z-Score" />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Формулы</h3>
          <P><B>IV:</B> Deribit DVOL Index (30-day)</P>
          <P><B>RV:</B></P>
          <Code>{`log_returns[i] = ln(price[i] / price[i-1])
window = last 30 log returns
rv_30d = √variance × √365 × 100     ← annualized %`}</Code>
          <P><B>25-Delta Skew:</B></P>
          <Code>{`skew_25d = IV(25δ put) - IV(25δ call)`}</Code>
          <P>Положительный skew = путы дороже коллов = страх падения.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Как читать</h3>
          <Code>{`  Volatility (%)
  80 ┤
     │          ╭─╮
  60 ┤    IV ──╯  ╰──    ← IV выше RV: рынок ожидает движение
     │   ╭──────────╮
  40 ┤──╯  RV ──────╰──  ← RV ниже IV: рынок спокойнее ожиданий
     │
  20 ┤
     └──────────────────── время

  Vol Premium = IV - RV
  Premium > 0: опционы дорогие (продавать vol)
  Premium < 0: опционы дешёвые (покупать vol)`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: Vol Premium + Skew Extremes</h3>
          <Strategy>
            <Code>{`  ┌──────────────────────────────────────────────┐
  │  IV >> RV + skew_z > +2 (путы дорогие)        │
  │  = Панический хедж → лонг на зоне интереса   │
  ├──────────────────────────────────────────────┤
  │  IV << RV + skew_z < -2 (коллы дорогие)       │
  │  = Эйфория → шорт на наклонной              │
  ├──────────────────────────────────────────────┤
  │  IV и RV оба низкие (< 30%)                  │
  │  = Сжатие волатильности → breakout ahead     │
  └──────────────────────────────────────────────┘`}</Code>
          </Strategy>
        </Section>

        {/* ── 11. VRP ── */}
        <Section id="vrp" title="11. Variance Risk Premium (VRP)">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Разница между IV и RV. Положительный VRP = опционы переоценены, отрицательный = недооценены.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Формула</h3>
          <Code>{`VRP   = IV_30d - RV_30d
VRP_z = z-score(VRP, window=365)`}</Code>
          <Screenshot src="/docs/05-ivrv-volcone.png" caption="VRP (IV-RV) bar chart с Rich Vol / Cheap Vol badge, ниже — 25d Skew Z-Score и Volatility Cone" />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Как читать</h3>
          <Code>{`  VRP (%)
  +20 ┤
      │       ╭──╮
  +10 ┤  ╭───╯  ╰───   ← VRP высокий: опционы дорогие
   0  ┤╯─────────────── ← нейтраль
  -10 ┤─────────╯ ╰──  ← VRP отрицательный: опционы дешёвые
  -20 ┤
      └──────────────── время

  VRP Z-Score:
  z > +2  → "Rich Vol" badge (зелёный) — продавать vol
  z < -2  → "Cheap Vol" badge (красный) — покупать vol`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: VRP Mean Reversion</h3>
          <Strategy>
            <Code>{`  ┌──────────────────────────────────────────────────┐
  │  VRP_z > +2 ("Rich Vol"):                        │
  │  → Sell vol: продавать стрэддлы/стрэнглы         │
  │  → Или: VRP_z > +2 + skew_z > +2 = паника       │
  │    → лонг на зоне интереса                       │
  ├──────────────────────────────────────────────────┤
  │  VRP_z < -2 ("Cheap Vol"):                       │
  │  → Buy vol: покупать стрэддлы перед движением    │
  │  → Готовиться к breakout (vol expansion)         │
  └──────────────────────────────────────────────────┘`}</Code>
          </Strategy>
        </Section>

        {/* ── 12. Volatility Cone ── */}
        <Section id="vol-cone" title="12. Volatility Cone">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Статистическое распределение RV на разных временных горизонтах (7d, 14d, 30d, 60d, 90d, 180d). Percentile bands показывают где текущая RV находится относительно истории.</P>
          <Screenshot src="/docs/05-ivrv-volcone.png" caption="Volatility Cone: stacked area bands (p10-p25, p25-p50, p50-p75, p75-p90) + current RV points (жёлтые) + p50 median line (пунктир)" />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Bands</h3>
          <Code>{`  RV (annualized %)
  100 ┤
      │  ╭───── 90th percentile
   80 ┤ ╱    ╭── 75th
      │╱   ╱
   60 ┤  ╱   ╭── 50th (median, пунктир)
      │ ╱  ╱
   40 ┤╱ ╱   ╭── 25th
      │ ╱  ╱
   20 ┤╱ ╱   ╭── 10th percentile
      │╱ ╱  ╱
    0 ┤─┴──┴──┴──┴──┴──┴──
      7d  14d 30d 60d 90d 180d

  ★ = текущая RV на каждом горизонте (жёлтые точки)`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия</h3>
          <Strategy>
            <Code>{`  ┌────────────────────────────────────────────────┐
  │  Current RV < 10th pctl на всех горизонтах:    │
  │  → Vol compression extreme                     │
  │  → Breakout imminent → buy vol                 │
  ├────────────────────────────────────────────────┤
  │  Current RV > 90th pctl на коротких горизонтах: │
  │  → Vol spike → проверить liq_z                 │
  │  → После каскада vol вернётся к median          │
  └────────────────────────────────────────────────┘`}</Code>
          </Strategy>
        </Section>

        {/* ── 13. Momentum ── */}
        <Section id="momentum" title="13. Momentum Indicator (вкладка Momentum)">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Multi-component momentum score [-100, +100]. Определяет трендовые режимы, exhaustion, дивергенции с ценой.</P>
          <Screenshot src="/docs/06-momentum-tab.png" caption="Momentum вкладка: header с regime badge + 4 metric cards + Price/Momentum dual-axis chart + DI/VR time series" />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Компоненты</h3>
          <Code>{`  1. Cross-Sectional Decile
     Ранг 1-месячного return среди 30 peers
     Decile 10 = top-10% performers

  2. Time-Series Decile
     Ранг return относительно собственной истории
     Decile 10 = top-10% исторических returns

  3. Relative Volume Decile
     Volume vs историческая норма (BTC-relative для альтов)

  4. 52W High Proximity
     Расстояние до 52-недельного максимума`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Формула</h3>
          <Code>{`score = decile_avg × 60 + DI × 30 + VR_signal × 10
clamped to [-100, +100]`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Regime Badges</h3>
          <Code>{`  score > +70  → Overbought (синий)
  score > +10  → Bullish
  score > -10  → Neutral
  score > -70  → Bearish
  score ≤ -70  → Oversold (жёлтый)`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Metric Cards</h3>
          <P>4 карточки вверху Momentum page:</P>
          <ul className="text-sm text-[#b0b0b0] mb-3 ml-4 list-disc space-y-1">
            <li><B>Cross-Sectional</B> — Decile + status (Positive/Negative)</li>
            <li><B>Time Series</B> — Decile + status</li>
            <li><B>Relative Volume</B> — множитель (e.g. 0.9x) + status</li>
            <li><B>52W High Prox</B> — процент от high + status</li>
          </ul>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Price + Momentum Chart</h3>
          <P>Dual-axis chart: цена (белая линия, левая ось) + momentum score (гистограмма, правая ось). Показывает расхождения цены и momentum.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">DI / VR Time Series</h3>
          <Screenshot src="/docs/06-momentum-tab.png" caption="Directional Intensity (левый) и Volatility Regime (правый) — отдельные time series" />
          <P><B>Directional Intensity [-1, +1]:</B></P>
          <Code>{`DI = (positive_days - negative_days) / total_days
+1 = все дни положительные
-1 = все дни отрицательные`}</Code>
          <P><B>Volatility Regime:</B></P>
          <Code>{`Expanding  = short-term vol > smoothed trend
Contracting = short-term vol < smoothed trend`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия</h3>
          <Strategy>
            <Code>{`  ┌──────────────────────────────────────────────────┐
  │  Momentum > +70 (overbought):                    │
  │  = Exhaustion zone, не добавлять лонги            │
  │  → Ждать crossover вниз для шорта                │
  ├──────────────────────────────────────────────────┤
  │  Momentum < -70 (oversold):                      │
  │  = Capitulation zone                             │
  │  → Ждать crossover вверх для лонга               │
  ├──────────────────────────────────────────────────┤
  │  Все deciles ≥ 7 + momentum > +10:               │
  │  = Тренд подтверждён по всем осям                │
  │  → Momentum confirmation для existing setups      │
  └──────────────────────────────────────────────────┘`}</Code>
          </Strategy>
        </Section>

        {/* ── 14. DI/VR Scatter ── */}
        <Section id="di-vr-scatter" title="14. DI/VR vs Forward Return Scatter Plots">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Аналог Z-Score Scatter Plots, но для DI и Volatility Regime. X = DI (или VR) значение, Y = forward return через N дней.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Периоды</h3>
          <P>Переключатели: <B>10d</B>, <B>30d</B>, <B>60d</B>.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Как читать</h3>
          <Code>{`  ● = историческая точка (серая)
  ★ = текущая позиция (жёлтая)
  ── = линия линейной регрессии

  Статистика: R², n, Avg at current`}</Code>
          <P><B>Примечание:</B> scatter plots заполняются по мере накопления данных (нужно 10+ дней momentum history). При старте системы отображается «insufficient data».</P>
        </Section>

        {/* ── 15. Price Distribution ── */}
        <Section id="price-dist" title="15. Price Distribution">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Ожидаемый диапазон цены на разных горизонтах. Две версии: Implied (на основе IV) и Momentum-Adjusted (с поправкой на тренд).</P>
          <Screenshot src="/docs/07-momentum-gauges.png" caption="Price Distribution: горизонтальные бары — 1σ (тёмные) и 2σ (светлые) диапазоны для Implied и Momentum-Adjusted" />
          <P><B>Только BTC и ETH</B> (требует IV из Deribit).</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Формулы</h3>
          <P><B>Implied:</B></P>
          <Code>{`upper_1σ = price × (1 + IV/100 × √(days/365))
lower_1σ = price × (1 - IV/100 × √(days/365))`}</Code>
          <P><B>Momentum-Adjusted:</B></P>
          <Code>{`drift       = momentum_score / 100 × 0.3
vol_adj     = VR > 0 ? 1.15 : 0.85
adjusted_IV = IV × vol_adj
upper_1σ    = price × (1 + drift + adjusted_IV/100 × √(days/365))`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Горизонты</h3>
          <P>Переключатели: <B>7d</B>, <B>10d</B>, <B>14d</B>, <B>30d</B>, <B>60d</B>.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Как читать</h3>
          <Code>{`  IMPLIED                    MOMENTUM-ADJUSTED
  $16.0k                     $15.2k ... $17.3k
  1σ: $56.9K — $78.5K       1σ: $57.3K — $77.7K
  2σ: $46.1K — $89.3K       2σ: $47.0K — $88.0K

  ├──────████████████──────┤  Implied 1σ
  ├────██████████████████──┤  Implied 2σ
  ├──────████████████──────┤  Adjusted 1σ
  ├────██████████████████──┤  Adjusted 2σ`}</Code>
        </Section>

        {/* ── 16. Signal Gauges ── */}
        <Section id="signal-gauges" title="16. Signal Gauges">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Горизонтальные gauge-индикаторы: Momentum Score и Volatility Skew.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Momentum Gauge</h3>
          <Code>{`  Oversold │ Bearish │ Neutral │ Bullish │ Overbought
  ─────────┼─────────┼─────────┼─────────┼──────────
  < -70    │ -70..-10│ -10..+10│ +10..+70│ > +70
           │         │    ▲    │         │
           │         │ current │         │`}</Code>
          <P>Показывает: score, z-score, avg, 30d change.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Skew Gauge (BTC/ETH only)</h3>
          <Code>{`  Bearish │ Neutral │ Bullish
  ────────┼─────────┼────────
  puts>>  │ balance │ calls>>`}</Code>
          <P>Показывает: skew value, z-score, avg, 30d change.</P>
        </Section>

        {/* ── 17. Orderbook ── */}
        <Section id="orderbook" title="17. Orderbook Depth & Skew">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Глубина ордербука ±2% от mid-price (Binance Futures). Skew показывает дисбаланс bid/ask.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Формулы</h3>
          <Code>{`mid       = (best_bid + best_ask) / 2
bid_depth = Σ(p × q) where p ≥ mid × 0.98
ask_depth = Σ(p × q) where p ≤ mid × 1.02
ob_skew   = (bid - ask) / (bid + ask)`}</Code>
          <P><B>Skew range:</B> [-1, +1]</P>
          <ul className="text-sm text-[#b0b0b0] mb-3 ml-4 list-disc space-y-1">
            <li>+1 = вся ликвидность на bid (поддержка)</li>
            <li>-1 = вся ликвидность на ask (давление продаж)</li>
          </ul>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: Подтверждение наклонных</h3>
          <Strategy>
            <Code>{`  ┌────────────────────────────────────────────────────┐
  │  Цена касается наклонной снизу + ob_skew > +0.3:   │
  │  → Ордербук подтверждает отбой → лонг              │
  ├────────────────────────────────────────────────────┤
  │  Цена касается наклонной сверху + ob_skew < -0.3:  │
  │  → Ордербук подтверждает отбой вниз → шорт         │
  ├────────────────────────────────────────────────────┤
  │  Пробой + skew разворот:                            │
  │  → Подтверждение breakout                          │
  └────────────────────────────────────────────────────┘`}</Code>
          </Strategy>
        </Section>

        {/* ── 18. Global Dashboard ── */}
        <Section id="global" title="18. Global Dashboard">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Обзор всего рынка: Risk Appetite, Alt OI Dominance, Global OI, Liquidations, Performance, Funding Heatmap.</P>
          <Screenshot src="/docs/09-global-dashboard.png" caption="Global: Risk Appetite Index, Altcoin OI Dominance, Global Liquidations, Global OI, OI Z-Score" />
          <Screenshot src="/docs/10-global-heatmap.png" caption="Global: Performance chart (все 30 символов) + Funding Rate Heatmap (цветовая матрица)" />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Risk Appetite Index</h3>
          <Code>{`risk_appetite = AVG((oi_z + funding_z + liq_z) / 3)
               по TOP-10 символам по OI`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Funding Rate Heatmap</h3>
          <P>Цветовая матрица: строки = символы, столбцы = даты. Цвет = funding rate:</P>
          <ul className="text-sm text-[#b0b0b0] mb-3 ml-4 list-disc space-y-1">
            <li>Фиолетовый — сильно отрицательный</li>
            <li>Бирюзовый — слабо отрицательный</li>
            <li>Зелёный — нейтральный</li>
            <li>Жёлтый/оранжевый — положительный</li>
          </ul>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Performance Chart</h3>
          <P>Линии всех 30 символов на одном графике. Показывает корреляцию и дивергенции.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия: Risk-On/Risk-Off</h3>
          <Strategy>
            <Code>{`  ┌────────────────────────────────────────────┐
  │  Risk Appetite > +1.5:                     │
  │  → Не открывать новые лонги                │
  │  → Искать шорт-сетапы                      │
  ├────────────────────────────────────────────┤
  │  Risk Appetite < -1.5:                     │
  │  → Наращивать экспозицию                   │
  │  → Искать лонг-сетапы                      │
  ├────────────────────────────────────────────┤
  │  Heatmap: >70% символов в одном цвете:     │
  │  → Торговать только BTC/ETH (ликвидность)  │
  └────────────────────────────────────────────┘`}</Code>
          </Strategy>
        </Section>

        {/* ── 19. Altcoin OI Dominance ── */}
        <Section id="alt-oi" title="19. Altcoin OI Dominance">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Доля альткоинов в суммарном OI. Показывает куда течёт спекулятивный капитал.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Формула</h3>
          <Code>{`alt_dom = (total_oi - btc_oi) / total_oi × 100`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Как читать</h3>
          <P>На Global Dashboard — area chart с двумя reference lines:</P>
          <ul className="text-sm text-[#b0b0b0] mb-3 ml-4 list-disc space-y-1">
            <li><B>40%</B> — risk-off порог (капитал в BTC)</li>
            <li><B>65%</B> — risk-on порог (капитал в альтах)</li>
          </ul>
          <P>Текущее значение + статус: <B>Risk-On</B> / <B>Neutral</B> / <B>Risk-Off</B>.</P>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Стратегия</h3>
          <Strategy>
            <Code>{`  ┌────────────────────────────────────────────────┐
  │  Alt dominance > 65% + funding_z > +1.5:       │
  │  → Альты перегреты → сокращать экспозицию      │
  ├────────────────────────────────────────────────┤
  │  Alt dominance < 40% + OI global z < -1:       │
  │  → Альты вымыты → искать лонг в топ-альтах    │
  ├────────────────────────────────────────────────┤
  │  Быстрый рост alt dominance (>5% за неделю):   │
  │  → "Alt season" → агрессивно торговать альты   │
  └────────────────────────────────────────────────┘`}</Code>
          </Strategy>
        </Section>

        {/* ── 20. Funding Arb ── */}
        <Section id="funding-arb" title="20. Funding Arb">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Мониторинг funding rate спредов между 11 биржами для дельта-нейтрального арбитража.</P>
          <Screenshot src="/docs/12-funding-arb.png" caption="Funding Arb: таблица спредов (Long @ / Short @ / Spread / Net 8day / OI / Vol 24h), Rate Comparison + History" />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Как работает</h3>
          <Code>{`  Биржа A:  +0.08% (платят лонги)
  Биржа B:  -0.02% (платят шорты)
  Спред:     0.10%
  ──────────────────────────────
  Действие:
  → Шорт на A (получаем +0.08%)
  → Лонг на B (получаем +0.02%)
  → Дельта-нейтральная позиция
  → Доход: 0.10% за 8ч = ~136% APR`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Фильтры</h3>
          <ul className="text-sm text-[#b0b0b0] mb-3 ml-4 list-disc space-y-1">
            <li><B>Min Spread</B> — минимальный спред для отображения</li>
            <li><B>Only Net {'>'} 0</B> — только прибыльные спреды</li>
            <li><B>Rate Comparison</B> — визуальное сравнение рейтов между биржами</li>
            <li><B>History</B> — история фандинга для выбранного символа</li>
          </ul>
        </Section>

        {/* ── 21. Live Feed ── */}
        <Section id="live-feed" title="21. Live Feed">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Что это</h3>
          <P>Реал-тайм поток событий с on-chain и DEX.</P>
          <Screenshot src="/docs/11-feed.png" caption="Live Feed: события (WHALE, NEW, TVL, YIELD, PUMP, DUMP, SPREAD) + Token Analysis + Funding Rates + Watchlist" />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Типы событий</h3>
          <Table
            headers={['Тип', 'Описание', 'Источник']}
            rows={[
              ['WHALE', 'Крупные переводы (>$50K)', 'Etherscan, Helius'],
              ['NEW', 'Новые пары на DEX', 'DexScreener, GeckoTerminal'],
              ['PUMP/DUMP', 'Резкие изменения цены', 'DexScreener'],
              ['TVL', 'Спайки TVL протоколов', 'DefiLlama'],
              ['YIELD', 'Высокие APY пулов', 'DexScreener'],
              ['SPREAD', 'Funding спреды', '11 бирж'],
              ['FUND', 'Экстремальный фандинг', 'Binance и др.'],
            ]}
          />

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Фильтры</h3>
          <P>Кнопки по chain (ETH, BSC, SOL, ARB, BASE, ...) и по типу события.</P>
        </Section>

        {/* ── Appendix A ── */}
        <Section id="appendix-a" title="Appendix A: Формулы">
          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Z-Score (универсальная)</h3>
          <Code>{`window = последние 365 значений
mean   = Σ(values) / n
std    = √(Σ(x - mean)² / n)     ← population std dev
z      = (current - mean) / std
pctl   = count(x < current) / n × 100

Минимум: n ≥ 7 (иначе z = 0, percentile = 50)`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Composite Regime</h3>
          <Code>{`composite_z    = (oi_z + funding_z + liq_z) / 3
composite_sma5 = SMA(composite_z, window=5)
composite_pct  = (oi_pctl + funding_pctl + liq_pctl) / 3`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Momentum Score</h3>
          <Code>{`decile_avg = (cross_sectional + time_series + relative) / 3
score      = decile_avg × 60 + DI × 30 + VR_signal × 10
clamped [-100, +100]`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Variance Risk Premium</h3>
          <Code>{`VRP   = IV_30d - RV_30d
VRP_z = z-score(VRP, window=365)`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Altcoin OI Dominance</h3>
          <Code>{`alt_dom = (total_oi - btc_oi) / total_oi × 100`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Directional Intensity</h3>
          <Code>{`DI = (positive_days - negative_days) / total_days
Range: [-1, +1]`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">OI Aggregation</h3>
          <Code>{`total_oi = binance_oi_usd
         + bybit_oi_coins × price
         + okx_oi_coins × price
         + bitget_oi_usd`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Realized Volatility</h3>
          <Code>{`log_ret[i] = ln(price[i] / price[i-1])
window     = last 30 log returns
rv_30d     = √(Var(window)) × √365 × 100%`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Orderbook Skew</h3>
          <Code>{`mid       = (best_bid + best_ask) / 2
bid_depth = Σ(p × q) where p ≥ mid × 0.98
ask_depth = Σ(p × q) where p ≤ mid × 1.02
ob_skew   = (bid - ask) / (bid + ask)`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Liquidation Levels</h3>
          <Code>{`liq_long  = price × (1 - 1/leverage)
liq_short = price × (1 + 1/leverage)`}</Code>

          <h3 className="text-sm font-semibold text-[#ccc] mb-2">Funding Annualized</h3>
          <Code>{`APR = rate_8h × 3 × 365 × 100%`}</Code>
        </Section>

        {/* ── Appendix B ── */}
        <Section id="appendix-b" title="Appendix B: Интервалы обновления">
          <Table
            headers={['Сервис', 'Интервал', 'Источник']}
            rows={[
              ['Derivatives (OI/Funding/Vol)', '5 мин', 'Binance + Bybit + OKX + Bitget'],
              ['Orderbook Depth', '30 сек', 'Binance Futures'],
              ['Funding Rates', '60 сек', '11 бирж'],
              ['Liquidations WS', 'real-time', 'Binance + Bybit WS'],
              ['Liq WS batch flush', '3 events / 10 sec', '—'],
              ['Options (IV/RV/Skew)', '5 мин', 'Deribit'],
              ['Momentum', '60 мин', 'calculated'],
              ['Feed Events', '10-60 сек', 'DexScreener/Gecko/Etherscan/Helius'],
              ['Screener Cache', '45 сек TTL', 'in-memory'],
              ['OB Skew Z-Score', '288 readings (24h)', 'rolling'],
              ['Liq Events cleanup', 'hourly', '> 7 days deleted'],
            ]}
          />
        </Section>

        <div className="text-xs text-[#444] text-center py-8 border-t border-[#1a1a1a] mt-10">
          Последнее обновление: март 2026
        </div>
      </article>
    </div>
  )
}
