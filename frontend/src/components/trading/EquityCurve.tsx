import { useMemo } from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import type { Trade } from '../../hooks/useTrading'

interface DataPoint {
  closed_at: string
  cumPnl: number
}

export default function EquityCurve({ trades }: { trades: Trade[] }) {
  const data = useMemo<DataPoint[]>(() => {
    const closed = trades
      .filter((t) => t.closed_at && t.pnl_usd != null)
      .sort((a, b) => new Date(a.closed_at!).getTime() - new Date(b.closed_at!).getTime())

    const result: DataPoint[] = []
    let cum = 0
    for (const t of closed) {
      cum += t.pnl_usd!
      result.push({ closed_at: t.closed_at!, cumPnl: Math.round(cum * 100) / 100 })
    }
    return result
  }, [trades])

  if (!data.length) {
    return <div className="text-text-secondary text-xs py-6 text-center">No closed trades — equity curve will appear here</div>
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
        <defs>
          <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#22c55e" stopOpacity={0.3} />
            <stop offset="100%" stopColor="#22c55e" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="closed_at"
          tickFormatter={(v: string) => new Date(v).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
          tick={{ fill: '#555', fontSize: 9, fontFamily: 'monospace' }}
          axisLine={{ stroke: '#1a1a1a' }}
          tickLine={false}
        />
        <YAxis
          tickFormatter={(v: number) => `$${v}`}
          tick={{ fill: '#555', fontSize: 9, fontFamily: 'monospace' }}
          axisLine={false}
          tickLine={false}
          width={60}
        />
        <Tooltip
          contentStyle={{ background: '#222', border: '1px solid #444', fontSize: 11, fontFamily: 'monospace' }}
          labelStyle={{ color: '#999' }}
          labelFormatter={(v: any) => new Date(v).toLocaleString()}
          formatter={(v: any) => [`$${Number(v).toFixed(2)}`, 'Cumulative PnL']}
        />
        <Area
          type="monotone"
          dataKey="cumPnl"
          stroke="#22c55e"
          strokeWidth={1.5}
          fill="url(#eqFill)"
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
