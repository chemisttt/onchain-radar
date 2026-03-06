import { useState, useMemo } from 'react'
import { type ScreenerRow } from '../../hooks/useDerivativesScreener'

function zColor(z: number): string {
  if (z >= 2) return 'text-red'
  if (z >= 1) return 'text-yellow'
  if (z <= -2) return 'text-green'
  if (z <= -1) return 'text-blue-400'
  return 'text-text-secondary'
}

function pctColor(v: number): string {
  if (v >= 80) return 'text-red'
  if (v >= 60) return 'text-yellow'
  return 'text-text-secondary'
}

function fmtUsd(v: number): string {
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

function fmtPrice(v: number): string {
  if (v >= 100) return `$${v.toFixed(0)}`
  if (v >= 1) return `$${v.toFixed(2)}`
  if (v >= 0.01) return `$${v.toFixed(4)}`
  return `$${v.toPrecision(4)}`
}

type SortKey =
  | 'symbol' | 'price' | 'open_interest_usd' | 'oi_change_24h_pct'
  | 'oi_zscore' | 'oi_percentile'
  | 'funding_rate' | 'funding_zscore' | 'funding_percentile'
  | 'liq_zscore' | 'liq_percentile'
  | 'volume_usd' | 'volume_zscore'
  | 'ob_depth_usd' | 'ob_skew_zscore'
  | 'percentile_avg'

interface Column {
  key: SortKey
  label: string
  align: string
}

const COLUMNS: Column[] = [
  { key: 'symbol', label: 'Symbol', align: 'text-left' },
  { key: 'price', label: 'Price', align: 'text-right' },
  { key: 'open_interest_usd', label: 'OI', align: 'text-right' },
  { key: 'oi_change_24h_pct', label: '24h OI %', align: 'text-right' },
  { key: 'oi_zscore', label: 'OI Z', align: 'text-right' },
  { key: 'oi_percentile', label: 'OI %ile', align: 'text-right' },
  { key: 'funding_rate', label: 'Fund (Ann.)', align: 'text-right' },
  { key: 'funding_zscore', label: 'Fund Z', align: 'text-right' },
  { key: 'funding_percentile', label: 'Fund %ile', align: 'text-right' },
  { key: 'liq_zscore', label: 'Liq Z', align: 'text-right' },
  { key: 'liq_percentile', label: 'Liq %ile', align: 'text-right' },
  { key: 'volume_usd', label: 'Volume', align: 'text-right' },
  { key: 'volume_zscore', label: 'Vol Z', align: 'text-right' },
  { key: 'ob_depth_usd', label: 'OB Depth', align: 'text-right' },
  { key: 'ob_skew_zscore', label: 'OB Skew Z', align: 'text-right' },
  { key: 'percentile_avg', label: '%ile Avg', align: 'text-right' },
]

interface ScreenerTableProps {
  data: ScreenerRow[]
  selectedSymbol: string | null
  onSelectSymbol: (symbol: string) => void
  searchFilter?: string
  minOiThreshold?: number
}

export default function ScreenerTable({
  data,
  selectedSymbol,
  onSelectSymbol,
  searchFilter = '',
  minOiThreshold = 0,
}: ScreenerTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('oi_zscore')
  const [sortAsc, setSortAsc] = useState(false)

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc)
    } else {
      setSortKey(key)
      setSortAsc(false)
    }
  }

  const filtered = useMemo(() => {
    let result = data
    if (searchFilter) {
      const q = searchFilter.toUpperCase()
      result = result.filter((r) => r.symbol.includes(q))
    }
    if (minOiThreshold > 0) {
      result = result.filter((r) => r.open_interest_usd >= minOiThreshold)
    }
    return result
  }, [data, searchFilter, minOiThreshold])

  const sorted = [...filtered].sort((a, b) => {
    const av = a[sortKey as keyof ScreenerRow]
    const bv = b[sortKey as keyof ScreenerRow]
    if (sortKey === 'symbol') {
      return sortAsc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av))
    }
    if (sortKey.endsWith('_zscore')) {
      const diff = Math.abs(Number(bv)) - Math.abs(Number(av))
      return sortAsc ? -diff : diff
    }
    const diff = Number(bv) - Number(av)
    return sortAsc ? -diff : diff
  })

  if (!data.length) {
    return <div className="text-text-secondary text-xs p-4">No derivatives data yet. Waiting for backfill...</div>
  }

  return (
    <div className="overflow-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border">
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                onClick={() => handleSort(col.key)}
                className={`${col.align} px-2 py-1.5 text-[10px] text-text-secondary uppercase font-medium cursor-pointer hover:text-text-primary select-none whitespace-nowrap`}
              >
                {col.label}
                {sortKey === col.key && (sortAsc ? ' \u25B2' : ' \u25BC')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const isSelected = selectedSymbol === row.symbol
            const fundingAnn = row.funding_rate * 3 * 365 * 100
            return (
              <tr
                key={row.symbol}
                onClick={() => onSelectSymbol(row.symbol)}
                className={`border-b border-border cursor-pointer transition-colors ${
                  isSelected ? 'bg-bg-titlebar' : 'hover:bg-bg-titlebar/50'
                }`}
              >
                {/* Symbol */}
                <td className="px-2 py-1 font-mono text-text-primary font-medium whitespace-nowrap">
                  {row.symbol.replace('USDT', '')}
                </td>
                {/* Price */}
                <td className="px-2 py-1 text-right font-mono text-text-primary">
                  {fmtPrice(row.price)}
                </td>
                {/* OI */}
                <td className="px-2 py-1 text-right font-mono text-text-secondary text-[10px]">
                  {fmtUsd(row.open_interest_usd)}
                </td>
                {/* 24h OI % */}
                <td className={`px-2 py-1 text-right font-mono text-[10px] ${
                  row.oi_change_24h_pct > 0 ? 'text-green' : row.oi_change_24h_pct < 0 ? 'text-red' : 'text-text-secondary'
                }`}>
                  {row.oi_change_24h_pct > 0 ? '+' : ''}{row.oi_change_24h_pct.toFixed(1)}%
                </td>
                {/* OI Z */}
                <td className={`px-2 py-1 text-right font-mono font-medium ${zColor(row.oi_zscore)}`}>
                  {row.oi_zscore.toFixed(2)}
                </td>
                {/* OI %ile */}
                <td className={`px-2 py-1 text-right font-mono text-[10px] ${pctColor(row.oi_percentile)}`}>
                  {row.oi_percentile.toFixed(0)}%
                </td>
                {/* Funding (Ann.) */}
                <td className={`px-2 py-1 text-right font-mono text-[10px] ${
                  fundingAnn > 0 ? 'text-green' : fundingAnn < 0 ? 'text-red' : 'text-text-secondary'
                }`}>
                  {fundingAnn > 0 ? '+' : ''}{fundingAnn.toFixed(1)}%
                </td>
                {/* Fund Z */}
                <td className={`px-2 py-1 text-right font-mono font-medium ${zColor(row.funding_zscore)}`}>
                  {row.funding_zscore.toFixed(2)}
                </td>
                {/* Fund %ile */}
                <td className={`px-2 py-1 text-right font-mono text-[10px] ${pctColor(row.funding_percentile)}`}>
                  {row.funding_percentile.toFixed(0)}%
                </td>
                {/* Liq Z */}
                <td className={`px-2 py-1 text-right font-mono font-medium ${zColor(row.liq_zscore)}`}>
                  {row.liq_zscore.toFixed(2)}
                </td>
                {/* Liq %ile */}
                <td className={`px-2 py-1 text-right font-mono text-[10px] ${pctColor(row.liq_percentile)}`}>
                  {row.liq_percentile.toFixed(0)}%
                </td>
                {/* Volume */}
                <td className="px-2 py-1 text-right font-mono text-text-secondary text-[10px]">
                  {fmtUsd(row.volume_usd)}
                </td>
                {/* Vol Z */}
                <td className={`px-2 py-1 text-right font-mono font-medium ${zColor(row.volume_zscore)}`}>
                  {row.volume_zscore.toFixed(2)}
                </td>
                {/* OB Depth */}
                <td className="px-2 py-1 text-right font-mono text-text-secondary text-[10px]">
                  {row.ob_depth_usd > 0 ? fmtUsd(row.ob_depth_usd) : '-'}
                </td>
                {/* OB Skew Z */}
                <td className={`px-2 py-1 text-right font-mono font-medium ${zColor(row.ob_skew_zscore)}`}>
                  {row.ob_skew_zscore !== 0 ? row.ob_skew_zscore.toFixed(2) : '-'}
                </td>
                {/* %ile Avg */}
                <td className={`px-2 py-1 text-right font-mono text-[10px] font-medium ${
                  row.percentile_avg >= 80 ? 'text-red' : row.percentile_avg >= 60 ? 'text-yellow' : 'text-text-secondary'
                }`}>
                  {row.percentile_avg.toFixed(0)}%
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
