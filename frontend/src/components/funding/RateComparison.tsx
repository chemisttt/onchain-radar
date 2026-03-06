const EX_TRADE_URLS: Record<string, (symbol: string) => string> = {
  Binance: (s) => `https://www.binance.com/en/futures/${s}`,
  Bybit: (s) => `https://www.bybit.com/trade/usdt/${s}`,
  OKX: (s) => `https://www.okx.com/trade-futures/${s.replace('USDT', '-usdt-swap').toLowerCase()}`,
  MEXC: (s) => `https://futures.mexc.com/exchange/${s}`,
  Hyperliquid: (s) => `https://app.hyperliquid.xyz/trade/${s.replace('USDT', '')}`,
  Paradex: (s) => `https://app.paradex.trade/trade/${s.replace('USDT', '-USD-PERP')}`,
  Lighter: () => `https://app.lighter.xyz/trade`,
  Extended: () => `https://app.extended.exchange`,
  EdgeX: () => `https://pro.edgex.exchange/trade`,
  Aster: (s) => `https://app.asterdex.com/en/futures/${s}`,
  Variational: () => `https://app.variational.io`,
}

interface RateComparisonProps {
  allRates: Record<string, number> | null
  symbol: string | null
}

const EX_ORDER = [
  'Binance', 'Bybit', 'OKX', 'MEXC', 'Hyperliquid',
  'Paradex', 'Lighter', 'Extended', 'EdgeX', 'Aster', 'Variational',
]

const EX_SHORT: Record<string, string> = {
  Binance: 'BN',
  Bybit: 'BY',
  OKX: 'OKX',
  MEXC: 'MX',
  Hyperliquid: 'HL',
  Paradex: 'PDX',
  Lighter: 'LTR',
  Extended: 'EXT',
  EdgeX: 'EDG',
  Aster: 'AST',
  Variational: 'VAR',
}

export default function RateComparison({ allRates, symbol }: RateComparisonProps) {
  if (!allRates || !symbol) {
    return (
      <div className="text-text-secondary text-xs p-2">
        Select a symbol to compare rates
      </div>
    )
  }

  const entries = EX_ORDER
    .filter((ex) => ex in allRates)
    .map((ex) => ({ exchange: ex, rate: allRates[ex] }))

  if (!entries.length) return null

  const maxAbsRate = Math.max(...entries.map((e) => Math.abs(e.rate)))

  return (
    <div className="space-y-1">
      <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-2">
        {symbol} — All Exchanges
      </div>
      {entries.map(({ exchange, rate }) => {
        const pct = (rate * 100).toFixed(4)
        const barWidth = maxAbsRate > 0 ? (Math.abs(rate) / maxAbsRate) * 100 : 0
        const color = rate > 0.0005
          ? 'text-green'
          : rate < -0.0005
            ? 'text-red'
            : 'text-text-primary'
        const barColor = rate > 0 ? 'bg-green/20' : 'bg-red/20'

        return (
          <div key={exchange} className="flex items-center gap-2 text-[11px] font-mono">
            <a
              href={EX_TRADE_URLS[exchange]?.(symbol) || '#'}
              target="_blank"
              rel="noopener noreferrer"
              className="w-8 text-text-secondary text-right hover:text-blue hover:underline"
            >
              {EX_SHORT[exchange]}
            </a>
            <div className="flex-1 relative h-5 bg-bg-app rounded-sm overflow-hidden">
              <div
                className={`absolute top-0 h-full ${barColor} transition-all`}
                style={{ width: `${barWidth}%`, left: rate < 0 ? 'auto' : '0', right: rate < 0 ? '0' : 'auto' }}
              />
              <span className={`absolute inset-0 flex items-center px-2 ${color}`}>
                {pct}%
              </span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
