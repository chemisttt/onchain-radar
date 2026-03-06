import { useState, useMemo } from 'react'
import Panel from '../layout/Panel'
import SpreadTable from './SpreadTable'
import RateComparison from './RateComparison'
import FundingChart from './FundingChart'
import { useFundingSpreads } from '../../hooks/useFundingSpreads'

export default function FundingArb() {
  const [positionSize, setPositionSize] = useState(1000)
  const [minSpread, setMinSpread] = useState(0.0001)
  const [onlyPositive, setOnlyPositive] = useState(false)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)

  const { data, isLoading } = useFundingSpreads({
    min_spread: minSpread,
    position_size: positionSize,
    only_positive: onlyPositive,
    limit: 100,
  })

  const selectedRates = useMemo(() => {
    if (!selectedSymbol || !data) return null
    const row = data.find((r) => r.symbol === selectedSymbol)
    return row?.all_rates || null
  }, [selectedSymbol, data])

  return (
    <div className="h-full flex flex-col gap-px">
      {/* Controls bar */}
      <div className="bg-bg-panel border border-border px-4 py-2 flex items-center gap-4 flex-shrink-0">
        <label className="flex items-center gap-2 text-[11px] text-text-secondary font-mono">
          Position $
          <input
            type="number"
            value={positionSize}
            onChange={(e) => setPositionSize(Number(e.target.value) || 1000)}
            className="w-20 text-[11px] bg-bg-primary border border-border text-text-primary px-2 py-1 font-mono"
          />
        </label>
        <label className="flex items-center gap-2 text-[11px] text-text-secondary font-mono">
          Min Spread
          <input
            type="number"
            step="0.0001"
            value={minSpread}
            onChange={(e) => setMinSpread(Number(e.target.value) || 0)}
            className="w-24 text-[11px] bg-bg-primary border border-border text-text-primary px-2 py-1 font-mono"
          />
        </label>
        <button
          onClick={() => setOnlyPositive(!onlyPositive)}
          className={`text-[11px] px-3 py-1 border font-mono transition-colors ${
            onlyPositive
              ? 'border-green text-green'
              : 'border-border text-text-secondary hover:text-text-primary'
          }`}
        >
          Only Net &gt; 0
        </button>
        {data && (
          <span className="text-[10px] text-text-secondary font-mono ml-auto">
            {data.length} spreads found across 11 exchanges
          </span>
        )}
      </div>

      {/* Main content */}
      <div className="flex-1 grid grid-cols-[1fr_400px] gap-px min-h-0">
        {/* Spread table */}
        <Panel title="Funding Spreads" className="min-h-0">
          {isLoading ? (
            <div className="text-text-secondary text-xs">Loading spreads...</div>
          ) : (
            <SpreadTable
              data={data || []}
              selectedSymbol={selectedSymbol}
              onSelectSymbol={setSelectedSymbol}
            />
          )}
        </Panel>

        {/* Right panel: rate comparison + chart */}
        <div className="flex flex-col gap-px min-h-0">
          <Panel title="Rate Comparison" className="flex-shrink-0 max-h-[45%]">
            <RateComparison allRates={selectedRates} symbol={selectedSymbol} />
          </Panel>
          <Panel title="History" className="flex-1 min-h-0">
            <FundingChart symbol={selectedSymbol} />
          </Panel>
        </div>
      </div>
    </div>
  )
}
