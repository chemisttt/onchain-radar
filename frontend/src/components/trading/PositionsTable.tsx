import { useState } from 'react'
import type { Trade } from '../../hooks/useTrading'
import { useClosePosition } from '../../hooks/useTrading'

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function fmtPrice(v: number): string {
  if (v >= 100) return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  if (v >= 1) return `$${v.toFixed(2)}`
  return `$${v.toPrecision(4)}`
}

function parseMeta(meta: string): Record<string, string> {
  try { return JSON.parse(meta) } catch { return {} }
}

export default function PositionsTable({ trades }: { trades: Trade[] }) {
  const [confirming, setConfirming] = useState<number | null>(null)
  const closeMut = useClosePosition()

  if (!trades.length) {
    return <div className="text-text-secondary text-xs py-8 text-center">No open positions</div>
  }

  return (
    <div className="overflow-auto">
      <table className="w-full text-[11px] font-mono whitespace-nowrap">
        <thead>
          <tr className="text-[#555] text-left border-b border-[#1a1a1a]">
            <th className="px-3 py-1.5">Symbol</th>
            <th className="px-3 py-1.5">Dir</th>
            <th className="px-3 py-1.5">Signal</th>
            <th className="px-3 py-1.5">Exit</th>
            <th className="px-3 py-1.5 text-right">Entry</th>
            <th className="px-3 py-1.5 text-right">Size</th>
            <th className="px-3 py-1.5 text-right">Lev</th>
            <th className="px-3 py-1.5 text-right">SL</th>
            <th className="px-3 py-1.5">Opened</th>
            <th className="px-3 py-1.5"></th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => {
            const meta = parseMeta(t.meta)
            const isLong = t.direction === 'long'
            return (
              <tr key={t.id} className="border-b border-[#111] hover:bg-[#111]">
                <td className="px-3 py-1.5 text-text-primary font-medium">{t.symbol}</td>
                <td className={`px-3 py-1.5 font-medium ${isLong ? 'text-green' : 'text-red'}`}>
                  {t.direction.toUpperCase()}
                </td>
                <td className="px-3 py-1.5 text-text-secondary">{t.signal_type}</td>
                <td className="px-3 py-1.5 text-text-secondary">{meta.exit_strategy || '—'}</td>
                <td className="px-3 py-1.5 text-right text-text-primary">{fmtPrice(t.entry_price)}</td>
                <td className="px-3 py-1.5 text-right text-text-primary">${t.entry_size_usd.toFixed(0)}</td>
                <td className="px-3 py-1.5 text-right text-text-secondary">{t.leverage}x</td>
                <td className="px-3 py-1.5 text-right text-text-secondary">
                  {t.sl_price ? fmtPrice(t.sl_price) : '—'}
                </td>
                <td className="px-3 py-1.5 text-text-secondary">{timeAgo(t.opened_at)}</td>
                <td className="px-3 py-1.5">
                  {confirming === t.id ? (
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => { closeMut.mutate(t.id); setConfirming(null) }}
                        disabled={closeMut.isPending}
                        className="px-2 py-0.5 text-[10px] bg-red/20 text-red border border-red/30 hover:bg-red/30 transition-colors"
                      >
                        Confirm
                      </button>
                      <button
                        onClick={() => setConfirming(null)}
                        className="px-2 py-0.5 text-[10px] text-[#555] border border-[#222] hover:text-text-secondary transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setConfirming(t.id)}
                      className="px-2 py-0.5 text-[10px] text-[#555] border border-[#222] hover:text-red hover:border-red/30 transition-colors"
                    >
                      Close
                    </button>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
