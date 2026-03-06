import { useMemo } from 'react'
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
  Legend,
} from 'recharts'
import { useDerivativesGlobal } from '../../hooks/useDerivativesGlobal'

/* ── Helpers ────────────────────────────────────────────── */

function fmtUsd(v: number): string {
  if (Math.abs(v) >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

function fmtDate(d: string): string {
  const parts = d.split('-')
  return `${parts[1]}/${parts[2]}`
}

const GRID = { stroke: '#1a1a1a', strokeDasharray: '3 3' }
const AXIS = { tick: { fontSize: 9, fill: '#555' }, axisLine: false, tickLine: false }

const PERF_COLORS = [
  '#3b82f6', '#ef4444', '#22c55e', '#eab308', '#a78bfa',
  '#f97316', '#ec4899', '#06b6d4', '#84cc16', '#6366f1',
]

/* ── Card wrapper ────────────────────────────────────────── */

function DashCard({
  title,
  footer,
  children,
  className = '',
}: {
  title: string
  footer?: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={`bg-[#0c0c0c] border border-[#1a1a1a] rounded flex flex-col ${className}`}>
      <div className="px-3 pt-2.5 pb-1 flex-shrink-0">
        <span className="text-[11px] text-text-primary font-medium">{title}</span>
      </div>
      <div className="flex-1 min-h-0 px-1">{children}</div>
      {footer && (
        <div className="px-3 pb-2 pt-1 flex-shrink-0">
          <div className="flex items-center gap-3 text-[10px] font-mono">{footer}</div>
        </div>
      )}
    </div>
  )
}

/* ── Funding Heatmap ─────────────────────────────────────── */

function FundingHeatmap({
  heatmap,
  dates,
}: {
  heatmap: { symbol: string; data: { date: string; rate: number }[] }[]
  dates: string[]
}) {
  const cellW = Math.max(12, Math.min(24, 600 / (dates.length || 1)))
  const cellH = 18

  // Build rate matrix
  const matrix = useMemo(() => {
    const m: Record<string, Record<string, number>> = {}
    for (const entry of heatmap) {
      m[entry.symbol] = {}
      for (const d of entry.data) {
        m[entry.symbol][d.date] = d.rate
      }
    }
    return m
  }, [heatmap])

  // Sort symbols by avg OI (use order from backend which is already sorted)
  const symbols = heatmap.map((h) => h.symbol)

  function rateColor(rate: number): string {
    const ann = rate * 3 * 365 * 100
    if (ann > 50) return '#dc2626'
    if (ann > 20) return '#f97316'
    if (ann > 5) return '#eab308'
    if (ann > 0) return '#16a34a'
    if (ann > -5) return '#0ea5e9'
    if (ann > -20) return '#6366f1'
    return '#7c3aed'
  }

  return (
    <div className="overflow-x-auto overflow-y-auto max-h-full">
      <div style={{ display: 'grid', gridTemplateColumns: `80px repeat(${dates.length}, ${cellW}px)` }}>
        {/* Header row: dates */}
        <div />
        {dates.map((d) => (
          <div
            key={d}
            className="text-[8px] text-[#555] text-center"
            style={{ height: 20, lineHeight: '20px' }}
          >
            {d.slice(5)}
          </div>
        ))}

        {/* Data rows */}
        {symbols.map((sym) => (
          <div key={sym} style={{ display: 'contents' }}>
            <div
              className="text-[9px] text-text-secondary font-mono pr-1 text-right"
              style={{ height: cellH, lineHeight: `${cellH}px` }}
            >
              {sym.replace('USDT', '')}
            </div>
            {dates.map((d) => {
              const rate = matrix[sym]?.[d] ?? 0
              return (
                <div
                  key={d}
                  style={{
                    width: cellW,
                    height: cellH,
                    backgroundColor: rateColor(rate),
                    opacity: 0.8,
                    borderRadius: 1,
                    margin: '0.5px',
                  }}
                  title={`${sym} ${d}: ${(rate * 100).toFixed(4)}%`}
                />
              )
            })}
          </div>
        ))}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-2 mt-2 px-2">
        {[
          { label: '<-20%', color: '#7c3aed' },
          { label: '-20%', color: '#6366f1' },
          { label: '-5%', color: '#0ea5e9' },
          { label: '0%', color: '#16a34a' },
          { label: '+5%', color: '#eab308' },
          { label: '+20%', color: '#f97316' },
          { label: '>50%', color: '#dc2626' },
        ].map((l) => (
          <div key={l.label} className="flex items-center gap-1">
            <div style={{ width: 10, height: 10, backgroundColor: l.color, borderRadius: 1 }} />
            <span className="text-[8px] text-[#555]">{l.label}</span>
          </div>
        ))}
        <span className="text-[8px] text-[#444] ml-1">(annualized)</span>
      </div>
    </div>
  )
}

/* ── Main component ────────────────────────────────────────── */

export default function GlobalDashboard() {
  const { data, isLoading } = useDerivativesGlobal()

  if (isLoading) {
    return <div className="text-text-secondary text-xs p-4">Loading global data...</div>
  }
  if (!data) {
    return <div className="text-text-secondary text-xs p-4">No global data</div>
  }

  const lastOI = data.global_oi[data.global_oi.length - 1]
  const lastRA = data.risk_appetite[data.risk_appetite.length - 1]
  const lastZ = data.global_oi_zscore[data.global_oi_zscore.length - 1]

  // Slim dates for x-axis
  const dateFormatter = (d: string) => fmtDate(d)

  return (
    <div className="h-full overflow-y-auto p-2 space-y-2">
      {/* ── Risk Appetite Index (full width) ──────────────── */}
      <DashCard
        title="Risk Appetite Index"
        className="h-[200px]"
        footer={
          <>
            <span className="text-text-secondary">Current:</span>
            <span className={lastRA?.value >= 0 ? 'text-green' : 'text-red'}>
              {lastRA?.value.toFixed(2)}
            </span>
          </>
        }
      >
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data.risk_appetite}>
            <defs>
              <linearGradient id="raGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#f97316" stopOpacity={0.3} />
                <stop offset="50%" stopColor="#22c55e" stopOpacity={0.05} />
                <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.3} />
              </linearGradient>
            </defs>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="date" tickFormatter={dateFormatter} {...AXIS} minTickGap={50} />
            <YAxis {...AXIS} tickFormatter={(v: number) => v.toFixed(1)} />
            <ReferenceLine y={0} stroke="#333" strokeDasharray="3 3" />
            <ReferenceLine y={2} stroke="#ef4444" strokeDasharray="3 3" strokeOpacity={0.3} />
            <ReferenceLine y={-2} stroke="#22c55e" strokeDasharray="3 3" strokeOpacity={0.3} />
            <Area
              type="monotone"
              dataKey="value"
              stroke="#f97316"
              fill="url(#raGrad)"
              strokeWidth={1.5}
              dot={false}
            />
            <Tooltip
              contentStyle={{ backgroundColor: '#222', border: '1px solid #444', fontSize: 11, color: '#e2e8f0' }} itemStyle={{ color: '#e2e8f0' }}
              formatter={(v: any) => [Number(v).toFixed(4), 'Risk Index']}
              separator=": "
              labelFormatter={(v: any) => {
                const d = new Date(v)
                return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
              }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </DashCard>

      {/* ── Row: Global Liq | Global OI | Global OI Z-Score ─ */}
      <div className="grid grid-cols-3 gap-2" style={{ height: 230 }}>
        {/* Global Liquidations */}
        <DashCard
          title="Global Liquidations"
          footer={
            <>
              <span className="text-text-secondary">Latest:</span>
              <span className="text-text-primary">
                {fmtUsd(data.global_liquidations[data.global_liquidations.length - 1]?.value || 0)}
              </span>
            </>
          }
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.global_liquidations}>
              <CartesianGrid {...GRID} />
              <XAxis dataKey="date" tickFormatter={dateFormatter} {...AXIS} minTickGap={40} />
              <YAxis {...AXIS} tickFormatter={(v: number) => fmtUsd(v)} />
              <ReferenceLine y={0} stroke="#333" />
              <Bar dataKey="value" fill="#ef4444" opacity={0.7} radius={[1, 1, 0, 0]} />
              <Tooltip
                contentStyle={{ backgroundColor: '#222', border: '1px solid #444', fontSize: 11, color: '#e2e8f0' }} itemStyle={{ color: '#e2e8f0' }}
                formatter={(v: any) => [fmtUsd(Number(v)), 'Liq Delta']}
                separator=": "
              labelFormatter={(v: any) => {
                const d = new Date(v)
                return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
              }}
              />
            </BarChart>
          </ResponsiveContainer>
        </DashCard>

        {/* Global OI (stacked) */}
        <DashCard
          title="Global OI"
          footer={
            lastOI && (
              <>
                <span className="text-text-secondary">Global:</span>
                <span className="text-text-primary">{fmtUsd(lastOI.total)}</span>
                <span className="text-[#3b82f6]">BTC: {fmtUsd(lastOI.btc)}</span>
                <span className="text-[#a78bfa]">ETH: {fmtUsd(lastOI.eth)}</span>
              </>
            )
          }
        >
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data.global_oi}>
              <defs>
                <linearGradient id="btcG" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.6} />
                  <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.1} />
                </linearGradient>
                <linearGradient id="ethG" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#a78bfa" stopOpacity={0.6} />
                  <stop offset="100%" stopColor="#a78bfa" stopOpacity={0.1} />
                </linearGradient>
                <linearGradient id="othG" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#555" stopOpacity={0.4} />
                  <stop offset="100%" stopColor="#555" stopOpacity={0.05} />
                </linearGradient>
              </defs>
              <CartesianGrid {...GRID} />
              <XAxis dataKey="date" tickFormatter={dateFormatter} {...AXIS} minTickGap={40} />
              <YAxis {...AXIS} tickFormatter={(v: number) => fmtUsd(v)} />
              <Area
                type="monotone"
                dataKey="btc"
                stackId="1"
                stroke="#3b82f6"
                fill="url(#btcG)"
                strokeWidth={1}
              />
              <Area
                type="monotone"
                dataKey="eth"
                stackId="1"
                stroke="#a78bfa"
                fill="url(#ethG)"
                strokeWidth={1}
              />
              <Area
                type="monotone"
                dataKey="others"
                stackId="1"
                stroke="#555"
                fill="url(#othG)"
                strokeWidth={1}
              />
              <Tooltip
                contentStyle={{ backgroundColor: '#222', border: '1px solid #444', fontSize: 11, color: '#e2e8f0' }} itemStyle={{ color: '#e2e8f0' }}
                formatter={(v: any, name: any) => [fmtUsd(Number(v)), String(name).toUpperCase()]}
                separator=": "
              labelFormatter={(v: any) => {
                const d = new Date(v)
                return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
              }}
              />
              <Legend
                iconSize={8}
                wrapperStyle={{ fontSize: 9, color: '#888' }}
                formatter={(v: any) => String(v).toUpperCase()}
              />
            </AreaChart>
          </ResponsiveContainer>
        </DashCard>

        {/* Global OI Z-Score */}
        <DashCard
          title="Global OI Z-Score"
          footer={
            <>
              <span className="text-text-secondary">Z-Score:</span>
              <span className={lastZ?.zscore >= 0 ? 'text-[#ef4444]' : 'text-[#22c55e]'}>
                {lastZ?.zscore.toFixed(2)}
              </span>
            </>
          }
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data.global_oi_zscore}>
              <CartesianGrid {...GRID} />
              <XAxis dataKey="date" tickFormatter={dateFormatter} {...AXIS} minTickGap={40} />
              <YAxis {...AXIS} tickFormatter={(v: number) => v.toFixed(1)} />
              <ReferenceLine y={0} stroke="#333" strokeDasharray="3 3" />
              <ReferenceLine y={2} stroke="#333" strokeDasharray="3 3" strokeOpacity={0.4} />
              <ReferenceLine y={-2} stroke="#333" strokeDasharray="3 3" strokeOpacity={0.4} />
              <Line
                type="monotone"
                dataKey="zscore"
                stroke="#5ba3ad"
                strokeWidth={1.5}
                dot={false}
              />
              <Tooltip
                contentStyle={{ backgroundColor: '#222', border: '1px solid #444', fontSize: 11, color: '#e2e8f0' }} itemStyle={{ color: '#e2e8f0' }}
                formatter={(v: any) => [Number(v).toFixed(4), 'Z-Score']}
                separator=": "
              labelFormatter={(v: any) => {
                const d = new Date(v)
                return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
              }}
              />
            </LineChart>
          </ResponsiveContainer>
        </DashCard>
      </div>

      {/* ── Row: Performance | Funding Heatmap ──────────────── */}
      <div className="grid grid-cols-2 gap-2" style={{ height: 340 }}>
        {/* Performance chart (multi-line) */}
        <DashCard
          title="Performance"
          footer={
            <div className="flex flex-wrap gap-x-3 gap-y-0.5">
              {Object.keys(data.performance)
                .slice(0, 10)
                .map((sym, i) => {
                  const pts = data.performance[sym]
                  const last = pts?.[pts.length - 1]?.pct ?? 0
                  return (
                    <span key={sym} style={{ color: PERF_COLORS[i % PERF_COLORS.length] }}>
                      {sym.replace('USDT', '')}: {last > 0 ? '+' : ''}
                      {last.toFixed(1)}%
                    </span>
                  )
                })}
            </div>
          }
        >
          <PerformanceChart performance={data.performance} />
        </DashCard>

        {/* Funding Heatmap */}
        <DashCard title="Funding Rate Heatmap">
          <FundingHeatmap heatmap={data.funding_heatmap} dates={data.heatmap_dates} />
        </DashCard>
      </div>
    </div>
  )
}

/* ── Performance Chart (merged multi-line) ────────────────── */

function PerformanceChart({
  performance,
}: {
  performance: Record<string, { date: string; pct: number }[]>
}) {
  const merged = useMemo(() => {
    const symbols = Object.keys(performance).slice(0, 10)
    if (!symbols.length) return []
    const dateMap: Record<string, Record<string, number>> = {}
    for (const sym of symbols) {
      for (const p of performance[sym]) {
        if (!dateMap[p.date]) dateMap[p.date] = { _date: 0 } as unknown as Record<string, number>
        ;(dateMap[p.date] as Record<string, number>)[sym] = p.pct
        ;(dateMap[p.date] as Record<string, number>)['_date'] = 1
      }
    }
    return Object.entries(dateMap)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, vals]) => ({ date, ...vals }))
  }, [performance])

  const symbols = Object.keys(performance).slice(0, 10)

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={merged}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="date" tickFormatter={fmtDate} {...AXIS} minTickGap={40} />
        <YAxis {...AXIS} tickFormatter={(v: number) => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`} />
        <ReferenceLine y={0} stroke="#444" strokeDasharray="3 3" />
        {symbols.map((sym, i) => (
          <Line
            key={sym}
            type="monotone"
            dataKey={sym}
            stroke={PERF_COLORS[i % PERF_COLORS.length]}
            strokeWidth={1}
            dot={false}
            name={sym.replace('USDT', '')}
          />
        ))}
        <Tooltip
          contentStyle={{ backgroundColor: '#1c1c1c', border: '1px solid #333', fontSize: 10 }}
          formatter={(v: any, name: any) => [`${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(2)}%`, name]}
          labelFormatter={(l: any) => String(l)}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
