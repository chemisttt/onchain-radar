import { type SpreadRow } from '../../hooks/useFundingSpreads'

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

function formatRate(rate: number): string {
  return (rate * 100).toFixed(4) + '%'
}

function ExchangeLink({ exchange, symbol }: { exchange: string; symbol: string }) {
  const urlFn = EX_TRADE_URLS[exchange]
  const short = EX_SHORT[exchange] || exchange
  if (!urlFn) return <span>{short}</span>
  return (
    <a
      href={urlFn(symbol)}
      target="_blank"
      rel="noopener noreferrer"
      className="hover:underline"
      onClick={(e) => e.stopPropagation()}
    >
      {short}
    </a>
  )
}

interface SpreadTableProps {
  data: SpreadRow[]
  selectedSymbol: string | null
  onSelectSymbol: (symbol: string) => void
}

export default function SpreadTable({ data, selectedSymbol, onSelectSymbol }: SpreadTableProps) {
  if (!data.length) {
    return <div className="text-text-secondary text-xs p-4">No spreads found matching criteria</div>
  }

  return (
    <div className="overflow-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border">
            <th className="text-left px-3 py-2 text-[10px] text-text-secondary uppercase font-medium">Symbol</th>
            <th className="text-left px-3 py-2 text-[10px] text-text-secondary uppercase font-medium">Long @</th>
            <th className="text-left px-3 py-2 text-[10px] text-text-secondary uppercase font-medium">Short @</th>
            <th className="text-right px-3 py-2 text-[10px] text-text-secondary uppercase font-medium">Spread</th>
            <th className="text-right px-3 py-2 text-[10px] text-text-secondary uppercase font-medium">$/day</th>
            <th className="text-right px-3 py-2 text-[10px] text-text-secondary uppercase font-medium">Net $/day</th>
            <th className="text-right px-3 py-2 text-[10px] text-text-secondary uppercase font-medium">OI</th>
            <th className="text-right px-3 py-2 text-[10px] text-text-secondary uppercase font-medium">Vol 24h</th>
            <th className="text-center px-3 py-2 text-[10px] text-text-secondary uppercase font-medium">Ex</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => {
            const isSelected = selectedSymbol === row.symbol
            return (
              <tr
                key={row.symbol}
                onClick={() => onSelectSymbol(row.symbol)}
                className={`border-b border-border cursor-pointer transition-colors ${
                  isSelected ? 'bg-bg-titlebar' : 'hover:bg-bg-titlebar/50'
                }`}
              >
                <td className="px-3 py-2 font-mono text-text-primary font-medium">{row.symbol}</td>
                <td className="px-3 py-2 font-mono">
                  <span className="text-green"><ExchangeLink exchange={row.long_exchange} symbol={row.symbol} /></span>
                  <span className="text-text-secondary ml-1">{formatRate(row.long_rate)}</span>
                </td>
                <td className="px-3 py-2 font-mono">
                  <span className="text-red"><ExchangeLink exchange={row.short_exchange} symbol={row.symbol} /></span>
                  <span className="text-text-secondary ml-1">{formatRate(row.short_rate)}</span>
                </td>
                <td className="px-3 py-2 text-right font-mono text-yellow font-medium">
                  {row.spread_pct.toFixed(4)}%
                </td>
                <td className="px-3 py-2 text-right font-mono text-text-primary">
                  ${row.est_daily_usd.toFixed(2)}
                </td>
                <td className={`px-3 py-2 text-right font-mono font-medium ${row.net_daily > 0 ? 'text-green' : 'text-red'}`}>
                  ${row.net_daily.toFixed(2)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-text-secondary text-[10px]">
                  {row.open_interest > 0 ? `$${(row.open_interest / 1e6).toFixed(1)}M` : '—'}
                </td>
                <td className="px-3 py-2 text-right font-mono text-text-secondary text-[10px]">
                  {row.volume_24h > 0 ? `$${(row.volume_24h / 1e6).toFixed(1)}M` : '—'}
                </td>
                <td className="px-3 py-2 text-center font-mono text-text-secondary text-[10px]">
                  {row.exchanges_count}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
