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
    <div className="flex flex-col gap-2 p-3">
      {trades.map((t) => {
        const meta = parseMeta(t.meta)
        const isLong = t.direction === 'long'
        return (
          <div key={t.id} className="flex items-center gap-6 px-3 py-2 bg-[#111] border border-[#1a1a1a] rounded text-[11px] font-mono">
            {/* Symbol + Direction */}
            <div className="flex items-center gap-2 min-w-[120px]">
              <span className="text-text-primary font-medium">{t.symbol.replace('USDT', '')}</span>
              <span className={`font-medium ${isLong ? 'text-green' : 'text-red'}`}>
                {t.direction.toUpperCase()}
              </span>
              <span className="text-[#555]">{t.leverage}x</span>
            </div>

            {/* Signal + Exit */}
            <div className="min-w-[120px]">
              <div className="text-text-secondary">{t.signal_type}</div>
              <div className="text-[10px] text-[#555]">{meta.exit_strategy || '—'}</div>
            </div>

            {/* Entry + SL */}
            <div className="min-w-[100px] text-right">
              <div className="text-text-primary">{fmtPrice(t.entry_price)}</div>
              <div className="text-[10px] text-[#555]">SL {t.sl_price ? fmtPrice(t.sl_price) : '—'}</div>
            </div>

            {/* Size */}
            <div className="min-w-[60px] text-right text-text-primary">
              ${t.entry_size_usd.toFixed(0)}
            </div>

            {/* Opened */}
            <div className="text-text-secondary">
              {timeAgo(t.opened_at)}
            </div>

            {/* Close button */}
            <div className="ml-auto">
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
            </div>
          </div>
        )
      })}
    </div>
  )
}
