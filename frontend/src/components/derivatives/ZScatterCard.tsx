import { useState, useMemo } from 'react'
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts'

/* ── Linear regression helper ──────────────────────────── */

function linReg(pts: { z: number; ret: number }[]) {
  const n = pts.length
  if (n < 2) return { r2: 0, slope: 0, intercept: 0 }

  let sx = 0, sy = 0, sxy = 0, sx2 = 0
  for (const p of pts) {
    sx += p.z
    sy += p.ret
    sxy += p.z * p.ret
    sx2 += p.z * p.z
  }

  const d = n * sx2 - sx * sx
  if (Math.abs(d) < 1e-12) return { r2: 0, slope: 0, intercept: 0 }

  const slope = (n * sxy - sx * sy) / d
  const intercept = (sy - slope * sx) / n
  const my = sy / n

  let ssTot = 0, ssRes = 0
  for (const p of pts) {
    ssRes += (p.ret - (slope * p.z + intercept)) ** 2
    ssTot += (p.ret - my) ** 2
  }

  return { r2: ssTot === 0 ? 0 : 1 - ssRes / ssTot, slope, intercept }
}

/* ── Component ─────────────────────────────────────────── */

const PERIODS = [10, 30, 60] as const

interface ZScatterCardProps {
  title: string
  history: Array<{ date: string; price: number; [k: string]: unknown }>
  zKey: string
  currentZ: number
}

export default function ZScatterCard({ title, history, zKey, currentZ }: ZScatterCardProps) {
  const [period, setPeriod] = useState<10 | 30 | 60>(30)

  const { historical, current, stats } = useMemo(() => {
    const pts: { z: number; ret: number }[] = []

    for (let i = 0; i < history.length - period; i++) {
      const z = Number(history[i][zKey]) || 0
      const ret = ((history[i + period].price - history[i].price) / history[i].price) * 100
      pts.push({ z, ret })
    }

    const reg = linReg(pts)
    const avgAtCurrent = reg.slope * currentZ + reg.intercept

    // Current point = last computed, highlighted
    const last = pts.length > 0 ? [pts[pts.length - 1]] : []

    return {
      historical: pts,
      current: last,
      stats: { r2: reg.r2, n: pts.length, avgAtCurrent },
    }
  }, [history, zKey, period, currentZ])

  if (!historical.length) {
    return (
      <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded p-3">
        <span className="text-text-secondary text-xs">Not enough data</span>
      </div>
    )
  }

  return (
    <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded flex flex-col min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between px-3 pt-2.5 pb-1 flex-shrink-0">
        <span className="text-[11px] text-text-primary font-medium">{title}</span>
        <div className="flex items-center gap-1">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-1.5 py-0.5 text-[9px] font-mono rounded transition-colors ${
                period === p
                  ? 'bg-[#2a2a2a] text-text-primary'
                  : 'text-[#555] hover:text-text-secondary'
              }`}
            >
              {p}d
            </button>
          ))}
          <svg className="w-3.5 h-3.5 text-[#444] hover:text-text-primary cursor-pointer transition-colors ml-1" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
          </svg>
        </div>
      </div>

      {/* Chart */}
      <div className="flex-1 min-h-0 px-1">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <XAxis
              dataKey="z"
              type="number"
              tick={{ fontSize: 8, fill: '#555' }}
              tickLine={false}
              axisLine={{ stroke: '#222' }}
              tickFormatter={(v: number) => v.toFixed(1)}
              name="Z"
            />
            <YAxis
              dataKey="ret"
              type="number"
              tick={{ fontSize: 8, fill: '#555' }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v: number) => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`}
              width={40}
              name="Return"
            />
            <Tooltip
              contentStyle={{
                background: '#222',
                border: '1px solid #444',
                borderRadius: 4,
                fontSize: 11,
                padding: '6px 10px',
                color: '#e2e8f0',
              }}
              itemStyle={{ color: '#e2e8f0' }}
              separator=": "
              formatter={(value: any, name: any) => {
                const v = Number(value)
                if (name === 'Z') return [v.toFixed(2), 'Z-Score']
                return [`${v > 0 ? '+' : ''}${v.toFixed(1)}%`, `${period}d Return`]
              }}
            />
            <ReferenceLine y={0} stroke="#222" />
            <ReferenceLine x={0} stroke="#222" />
            <Scatter
              data={historical}
              fill="#4da8da"
              fillOpacity={0.12}
              isAnimationActive={false}
              shape={(props: any) => (
                <circle cx={props.cx} cy={props.cy} r={1.5} fill="#4da8da" fillOpacity={0.15} />
              )}
            />
            <Scatter
              data={current}
              fill="#eab308"
              isAnimationActive={false}
              shape={(props: any) => (
                <circle cx={props.cx} cy={props.cy} r={5} fill="#eab308" />
              )}
            />
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      {/* Footer stats */}
      <div className="px-3 pb-2 pt-1 flex-shrink-0">
        <div className="flex items-center gap-3 text-[10px] font-mono">
          <span className="text-text-secondary">R²:</span>
          <span className="text-text-primary">{stats.r2.toFixed(3)}</span>
          <span className="text-text-secondary">n:</span>
          <span className="text-text-primary">{stats.n.toLocaleString()}</span>
          <span className="text-text-secondary">Avg at current:</span>
          <span style={{ color: stats.avgAtCurrent >= 0 ? '#22c55e' : '#ef4444' }}>
            {stats.avgAtCurrent > 0 ? '+' : ''}{stats.avgAtCurrent.toFixed(2)}%
          </span>
        </div>
      </div>
    </div>
  )
}
