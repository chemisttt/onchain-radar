import { useState, useMemo } from 'react'
import Panel from '../layout/Panel'
import { useDerivativesScreener } from '../../hooks/useDerivativesScreener'
import ScreenerTable from './ScreenerTable'
import SymbolDetail from './SymbolDetail'
import GlobalDashboard from './GlobalDashboard'
import MomentumTab from './MomentumTab'
import MomentumPage from './MomentumPage'
import BacktestPage from './BacktestPage'

function fmtUsd(v: number): string {
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

function fmtPrice(v: number): string {
  if (v >= 100) return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
  if (v >= 1) return `$${v.toFixed(2)}`
  return `$${v.toPrecision(4)}`
}

type TabKey = 'analysis' | 'momentum' | 'momentum-page' | 'global' | 'backtest'

export default function DerivativesPanel() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>('BTCUSDT')
  const [tab, setTab] = useState<TabKey>('analysis')
  const [searchFilter, setSearchFilter] = useState('')
  const [minOi, setMinOi] = useState(0)
  const { data, isLoading } = useDerivativesScreener()

  const selected = useMemo(() => {
    if (!data || !selectedSymbol) return null
    return data.find((r) => r.symbol === selectedSymbol) || null
  }, [data, selectedSymbol])

  // OI slider: max = highest OI in data, step 1M
  const maxOi = useMemo(() => {
    if (!data?.length) return 0
    return Math.max(...data.map((r) => r.open_interest_usd))
  }, [data])

  const filteredCount = useMemo(() => {
    if (!data) return 0
    let result = data
    if (searchFilter) {
      const q = searchFilter.toUpperCase()
      result = result.filter((r) => r.symbol.includes(q))
    }
    if (minOi > 0) {
      result = result.filter((r) => r.open_interest_usd >= minOi)
    }
    return result.length
  }, [data, searchFilter, minOi])

  return (
    <div className="h-full flex flex-col gap-px">
      {/* Stats bar + tabs — TR style top header */}
      <div className="flex items-center gap-4 px-4 py-2 bg-[#0a0a0a] border-b border-[#1a1a1a] flex-shrink-0">
        {/* Tabs */}
        <div className="flex items-center gap-1 mr-4">
          {(['analysis', 'momentum', 'momentum-page', 'global', 'backtest'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3 py-1 text-[11px] font-medium rounded transition-colors ${
                tab === t
                  ? 'bg-[#1a1a1a] text-text-primary'
                  : 'text-[#555] hover:text-text-secondary'
              }`}
            >
              {t === 'analysis' ? 'Analysis' : t === 'momentum' ? 'IV/RV' : t === 'momentum-page' ? 'Momentum' : t === 'global' ? 'Global' : 'Backtest'}
            </button>
          ))}
        </div>

        {/* Symbol stats (only in analysis/momentum/momentum-page tab) */}
        {(tab === 'analysis' || tab === 'momentum' || tab === 'momentum-page' || tab === 'backtest') && selected && (
          <>
            <span className="text-sm font-mono font-semibold text-text-primary">
              {selected.symbol.replace('USDT', '')}
            </span>

            <div className="flex items-center gap-1.5">
              <span className="text-[9px] text-[#555] uppercase">Price</span>
              <span className={`text-xs font-mono font-medium ${selected.price_change_24h_pct >= 0 ? 'text-green' : 'text-red'}`}>
                {fmtPrice(selected.price)}
              </span>
              <span className={`text-[10px] font-mono ${selected.price_change_24h_pct >= 0 ? 'text-green' : 'text-red'}`}>
                {selected.price_change_24h_pct > 0 ? '+' : ''}{selected.price_change_24h_pct.toFixed(2)}%
              </span>
            </div>

            <div className="flex items-center gap-1.5">
              <span className="text-[9px] text-[#555] uppercase">Open Interest</span>
              <span className="text-xs font-mono text-text-primary">{fmtUsd(selected.open_interest_usd)}</span>
              <span className={`text-[10px] font-mono ${selected.oi_change_24h_pct >= 0 ? 'text-green' : 'text-red'}`}>
                {selected.oi_change_24h_pct > 0 ? '+' : ''}{selected.oi_change_24h_pct.toFixed(2)}%
              </span>
            </div>

            <div className="flex items-center gap-1.5">
              <span className="text-[9px] text-[#555] uppercase">Volume</span>
              <span className="text-xs font-mono text-text-primary">{fmtUsd(selected.volume_usd)}</span>
            </div>

            <div className="ml-auto flex items-center gap-3 text-[10px] font-mono">
              <span className="text-[#555]">OI Z:</span>
              <span className="text-text-primary">{selected.oi_zscore.toFixed(2)}</span>
              <span className="text-[#555]">Fund Z:</span>
              <span className="text-text-primary">{selected.funding_zscore.toFixed(2)}</span>
              <span className="text-[#555]">Liq Z:</span>
              <span className="text-text-primary">{selected.liq_zscore.toFixed(2)}</span>
            </div>
          </>
        )}
      </div>

      {/* Main content area */}
      <div className="flex-[4] min-h-0 bg-[#0a0a0a]">
        {tab === 'analysis' ? (
          <SymbolDetail symbol={selectedSymbol} />
        ) : tab === 'momentum' ? (
          <MomentumTab symbol={selectedSymbol} />
        ) : tab === 'momentum-page' ? (
          <MomentumPage symbol={selectedSymbol} />
        ) : tab === 'backtest' ? (
          <BacktestPage symbol={selectedSymbol} />
        ) : (
          <GlobalDashboard />
        )}
      </div>

      {/* Screener Table — bottom */}
      <Panel
        title="Derivatives Screener"
        className="flex-[2] min-h-0"
        actions={
          <div className="flex items-center gap-3">
            {/* Search */}
            <input
              type="text"
              placeholder="Search..."
              value={searchFilter}
              onChange={(e) => setSearchFilter(e.target.value)}
              className="bg-[#111] border border-[#222] rounded px-2 py-0.5 text-[10px] text-text-primary font-mono w-24 focus:outline-none focus:border-[#333]"
            />
            {/* Min OI slider */}
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-text-secondary font-mono">Min OI:</span>
              <input
                type="range"
                min={0}
                max={maxOi}
                step={1_000_000}
                value={minOi}
                onChange={(e) => setMinOi(Number(e.target.value))}
                className="w-20 h-1 accent-[#555]"
              />
              <span className="text-[10px] text-text-primary font-mono w-12">
                {minOi > 0 ? fmtUsd(minOi) : 'All'}
              </span>
            </div>
            <span className="text-[10px] text-text-secondary font-mono">
              {filteredCount} symbols | 4 exchanges | 5min refresh
            </span>
          </div>
        }
      >
        {isLoading ? (
          <div className="text-text-secondary text-xs">Loading screener...</div>
        ) : (
          <ScreenerTable
            data={data || []}
            selectedSymbol={selectedSymbol}
            onSelectSymbol={(sym) => {
              setSelectedSymbol(sym)
              if (tab === 'global') setTab('analysis')
            }}
            searchFilter={searchFilter}
            minOiThreshold={minOi}
          />
        )}
      </Panel>
    </div>
  )
}
