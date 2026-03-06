import { useState, useMemo, useEffect } from 'react'
import Panel from '../layout/Panel'
import { useFunding, type FundingRow } from '../../hooks/useFunding'

const EXCHANGES = ['Binance', 'Bybit', 'OKX', 'MEXC', 'Hyperliquid']

type SortKey = 'symbol' | 'rate' | 'apr' | 'countdown'

function formatCountdown(ms: number | null): string {
  if (!ms || ms <= 0) return '--:--'
  const totalMin = Math.floor(ms / 60000)
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

function RateCell({ rate }: { rate?: { rate: number; apr: number } }) {
  if (!rate) return <td className="px-2 py-1 text-text-secondary text-center">--</td>

  const pct = (rate.rate * 100).toFixed(4)
  const apr = (rate.apr * 100).toFixed(1)
  const isExtreme = Math.abs(rate.rate) >= 0.001
  const color = rate.rate > 0.0005
    ? 'text-green'
    : rate.rate < -0.0005
    ? 'text-red'
    : 'text-text-primary'

  return (
    <td className={`px-2 py-1 text-right font-mono text-[10px] ${color} ${isExtreme ? 'font-bold' : ''}`}>
      {pct}%
      <span className="text-text-secondary ml-1">({apr}%)</span>
    </td>
  )
}

export default function FundingPanel() {
  const [search, setSearch] = useState('')
  const [onlyProfitable, setOnlyProfitable] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('rate')
  const [sortAsc, setSortAsc] = useState(false)
  const [now, setNow] = useState(Date.now())

  const { data, isLoading } = useFunding()

  // Tick countdown every 30s
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(id)
  }, [])

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc)
    } else {
      setSortKey(key)
      setSortAsc(false)
    }
  }

  const filtered = useMemo(() => {
    if (!data) return []
    let rows = [...data]

    if (search) {
      const q = search.toUpperCase()
      rows = rows.filter((r) => r.symbol.toUpperCase().includes(q))
    }

    if (onlyProfitable) {
      rows = rows.filter((r) =>
        Object.values(r.rates).some((v) => Math.abs(v.rate) >= 0.0003)
      )
    }

    rows.sort((a, b) => {
      let cmp = 0
      if (sortKey === 'symbol') {
        cmp = a.symbol.localeCompare(b.symbol)
      } else if (sortKey === 'rate') {
        const aMax = Math.max(...Object.values(a.rates).map((v) => Math.abs(v.rate)))
        const bMax = Math.max(...Object.values(b.rates).map((v) => Math.abs(v.rate)))
        cmp = bMax - aMax
      } else if (sortKey === 'apr') {
        const aMax = Math.max(...Object.values(a.rates).map((v) => Math.abs(v.apr)))
        const bMax = Math.max(...Object.values(b.rates).map((v) => Math.abs(v.apr)))
        cmp = bMax - aMax
      } else if (sortKey === 'countdown') {
        const aMs = a.next_funding_ms ?? Infinity
        const bMs = b.next_funding_ms ?? Infinity
        cmp = aMs - bMs
      }
      return sortAsc ? -cmp : cmp
    })

    return rows
  }, [data, search, onlyProfitable, sortKey, sortAsc, now])

  const sortArrow = (key: SortKey) => {
    if (sortKey !== key) return ''
    return sortAsc ? ' ^' : ' v'
  }

  return (
    <Panel title="Funding Rates">
      {/* Controls */}
      <div className="flex items-center gap-2 mb-2">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search symbol..."
          className="flex-1 text-[10px] bg-bg-primary border border-border text-text-primary px-1.5 py-0.5 font-mono placeholder:text-text-secondary/50 min-w-0"
        />
        <button
          onClick={() => setOnlyProfitable(!onlyProfitable)}
          className={`text-[10px] px-1.5 py-0.5 border font-mono transition-colors whitespace-nowrap ${
            onlyProfitable
              ? 'border-yellow text-yellow'
              : 'border-border text-text-secondary hover:text-text-primary'
          }`}
        >
          Profitable
        </button>
      </div>

      {isLoading && <div className="text-text-secondary text-xs">Loading funding rates...</div>}
      {data && filtered.length === 0 && <div className="text-text-secondary text-xs">No funding data</div>}
      {filtered.length > 0 && (
        <div className="overflow-auto -mx-3">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border">
                <th
                  className="text-left px-2 py-1 text-[10px] text-text-secondary uppercase font-medium cursor-pointer hover:text-text-primary"
                  onClick={() => handleSort('symbol')}
                >
                  Symbol{sortArrow('symbol')}
                </th>
                <th
                  className="text-right px-2 py-1 text-[10px] text-text-secondary uppercase font-medium cursor-pointer hover:text-text-primary"
                  onClick={() => handleSort('countdown')}
                >
                  Next{sortArrow('countdown')}
                </th>
                {EXCHANGES.map((ex) => (
                  <th key={ex} className="text-right px-2 py-1 text-[10px] text-text-secondary uppercase font-medium">
                    {ex === 'Hyperliquid' ? 'HL' : ex}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((row: FundingRow) => (
                <tr key={row.symbol} className="border-b border-border hover:bg-bg-titlebar transition-colors">
                  <td className="px-2 py-1 font-mono text-text-primary">{row.symbol}</td>
                  <td className="px-2 py-1 text-right font-mono text-[10px] text-text-secondary">
                    {formatCountdown(row.next_funding_ms)}
                  </td>
                  {EXCHANGES.map((ex) => (
                    <RateCell key={ex} rate={row.rates[ex]} />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
