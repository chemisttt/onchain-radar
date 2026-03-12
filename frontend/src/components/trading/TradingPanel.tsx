import { useState } from 'react'
import { useTradingPositions, useTradingHistory, useTradingStats } from '../../hooks/useTrading'
import PositionsTable from './PositionsTable'
import HistoryTable from './HistoryTable'
import EquityCurve from './EquityCurve'

type SubTab = 'positions' | 'history'

export default function TradingPanel() {
  const [tab, setTab] = useState<SubTab>('positions')
  const { data: positions } = useTradingPositions()
  const { data: history } = useTradingHistory()
  const { data: stats } = useTradingStats()

  const s = stats || { open_count: 0, closed_count: 0, win_count: 0, win_rate: 0, total_pnl_usd: 0, avg_pnl_pct: 0 }

  return (
    <div className="h-full flex flex-col">
      {/* Stats bar */}
      <div className="flex items-center gap-4 px-4 py-2 bg-[#0a0a0a] border-b border-[#1a1a1a] flex-shrink-0">
        {/* Sub-tabs */}
        <div className="flex items-center gap-1 mr-4">
          {(['positions', 'history'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3 py-1 text-[11px] font-medium rounded transition-colors ${
                tab === t
                  ? 'bg-[#1a1a1a] text-text-primary'
                  : 'text-[#555] hover:text-text-secondary'
              }`}
            >
              {t === 'positions' ? 'Positions' : 'History'}
            </button>
          ))}
        </div>

        {/* Stats */}
        <div className="flex items-center gap-4 text-[11px] font-mono">
          <div className="flex items-center gap-1.5">
            <span className="text-[#555]">Open:</span>
            <span className="text-text-primary">{s.open_count}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[#555]">Closed:</span>
            <span className="text-text-primary">{s.closed_count}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[#555]">WR:</span>
            <span className={s.win_rate >= 50 ? 'text-green' : 'text-red'}>{s.win_rate}%</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[#555]">Total PnL:</span>
            <span className={s.total_pnl_usd >= 0 ? 'text-green' : 'text-red'}>
              {s.total_pnl_usd >= 0 ? '+' : ''}${s.total_pnl_usd.toFixed(2)}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[#555]">Avg PnL:</span>
            <span className={s.avg_pnl_pct >= 0 ? 'text-green' : 'text-red'}>
              {s.avg_pnl_pct >= 0 ? '+' : ''}{s.avg_pnl_pct.toFixed(2)}%
            </span>
          </div>
        </div>

        <span className="ml-auto text-[10px] text-[#555] font-mono">15s refresh</span>
      </div>

      {/* Table content */}
      <div className="flex-1 min-h-0 overflow-auto bg-[#0a0a0a]">
        {tab === 'positions' ? (
          <PositionsTable trades={positions || []} />
        ) : (
          <HistoryTable trades={history || []} />
        )}
      </div>

      {/* Equity curve */}
      <div className="h-[200px] flex-shrink-0 border-t border-[#1a1a1a] bg-[#0a0a0a] px-2 py-1">
        <div className="text-[9px] text-[#555] uppercase tracking-wider mb-1 px-2">Equity Curve</div>
        <div className="h-[170px]">
          <EquityCurve trades={history || []} />
        </div>
      </div>
    </div>
  )
}
