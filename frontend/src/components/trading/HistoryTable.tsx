import { useState, useMemo } from 'react'
import type { Trade } from '../../hooks/useTrading'

type SortKey = 'closed_at' | 'pnl_pct' | 'pnl_usd' | 'symbol'

function fmtPrice(v: number): string {
  if (v >= 100) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (v >= 1) return v.toFixed(2)
  return v.toPrecision(4)
}

function daysHeld(opened: string, closed: string | null): string {
  if (!closed) return '—'
  const diff = new Date(closed).getTime() - new Date(opened).getTime()
  const days = diff / 86400000
  if (days < 1) return `${Math.round(days * 24)}h`
  return `${days.toFixed(1)}d`
}

export default function HistoryTable({ trades }: { trades: Trade[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('closed_at')
  const [sortAsc, setSortAsc] = useState(false)

  const sorted = useMemo(() => {
    const arr = [...trades]
    arr.sort((a, b) => {
      let av: string | number, bv: string | number
      if (sortKey === 'closed_at') { av = a.closed_at || ''; bv = b.closed_at || '' }
      else if (sortKey === 'pnl_pct') { av = a.pnl_pct || 0; bv = b.pnl_pct || 0 }
      else if (sortKey === 'pnl_usd') { av = a.pnl_usd || 0; bv = b.pnl_usd || 0 }
      else { av = a.symbol; bv = b.symbol }
      if (av < bv) return sortAsc ? -1 : 1
      if (av > bv) return sortAsc ? 1 : -1
      return 0
    })
    return arr
  }, [trades, sortKey, sortAsc])

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortAsc(!sortAsc)
    else { setSortKey(key); setSortAsc(false) }
  }

  const sortIcon = (key: SortKey) =>
    sortKey === key ? (sortAsc ? ' ▲' : ' ▼') : ''

  if (!trades.length) {
    return <div className="text-text-secondary text-xs py-8 text-center">No closed trades yet</div>
  }

  return (
    <div className="overflow-auto">
      <table className="w-full text-[11px] font-mono">
        <thead>
          <tr className="text-[#555] text-left border-b border-[#1a1a1a]">
            <th className="px-3 py-1.5 cursor-pointer hover:text-text-secondary" onClick={() => toggleSort('symbol')}>
              Symbol{sortIcon('symbol')}
            </th>
            <th className="px-3 py-1.5">Dir</th>
            <th className="px-3 py-1.5">Signal</th>
            <th className="px-3 py-1.5 text-right">Entry</th>
            <th className="px-3 py-1.5 text-right">Exit</th>
            <th className="px-3 py-1.5 text-right cursor-pointer hover:text-text-secondary" onClick={() => toggleSort('pnl_pct')}>
              PnL%{sortIcon('pnl_pct')}
            </th>
            <th className="px-3 py-1.5 text-right cursor-pointer hover:text-text-secondary" onClick={() => toggleSort('pnl_usd')}>
              PnL${sortIcon('pnl_usd')}
            </th>
            <th className="px-3 py-1.5">Exit Reason</th>
            <th className="px-3 py-1.5 text-right">Held</th>
            <th className="px-3 py-1.5 cursor-pointer hover:text-text-secondary" onClick={() => toggleSort('closed_at')}>
              Closed{sortIcon('closed_at')}
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((t) => {
            const pnlPct = t.pnl_pct || 0
            const pnlUsd = t.pnl_usd || 0
            const isLong = t.direction === 'long'
            const pnlColor = pnlPct >= 0 ? 'text-green' : 'text-red'
            return (
              <tr key={t.id} className="border-b border-[#111] hover:bg-[#111]">
                <td className="px-3 py-1.5 text-text-primary font-medium">{t.symbol}</td>
                <td className={`px-3 py-1.5 font-medium ${isLong ? 'text-green' : 'text-red'}`}>
                  {t.direction.toUpperCase()}
                </td>
                <td className="px-3 py-1.5 text-text-secondary">{t.signal_type}</td>
                <td className="px-3 py-1.5 text-right text-text-primary">{fmtPrice(t.entry_price)}</td>
                <td className="px-3 py-1.5 text-right text-text-primary">
                  {t.exit_price ? fmtPrice(t.exit_price) : '—'}
                </td>
                <td className={`px-3 py-1.5 text-right font-medium ${pnlColor}`}>
                  {pnlPct > 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                </td>
                <td className={`px-3 py-1.5 text-right font-medium ${pnlColor}`}>
                  {pnlUsd > 0 ? '+' : ''}${pnlUsd.toFixed(2)}
                </td>
                <td className="px-3 py-1.5 text-text-secondary">{t.exit_reason || '—'}</td>
                <td className="px-3 py-1.5 text-right text-text-secondary">{daysHeld(t.opened_at, t.closed_at)}</td>
                <td className="px-3 py-1.5 text-text-secondary">
                  {t.closed_at ? new Date(t.closed_at).toLocaleDateString() : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
