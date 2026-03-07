import { useState } from 'react'
import {
  ResponsiveContainer,
  ComposedChart,
  LineChart,
  BarChart,
  ScatterChart,
  Scatter,
  Line,
  Bar,
  Area,
  Cell,
  XAxis,
  YAxis,
  ZAxis,
  Tooltip,
  ReferenceLine,
  CartesianGrid,
} from 'recharts'
import { useMomentumPage, type ScatterPeriod, type PriceDistHorizon } from '../../hooks/useMomentumPage'

/* ── Helpers ──────────────────────────────────────────── */

const GRID = { stroke: '#1a1a1a', strokeDasharray: '3 3' }
const AXIS = { tick: { fontSize: 8, fill: '#555' }, axisLine: false, tickLine: false }

const TOOLTIP_STYLE = {
  background: '#222',
  border: '1px solid #444',
  borderRadius: 4,
  fontSize: 11,
  padding: '6px 10px',
  color: '#e2e8f0',
}

function fmtDate(d: string): string {
  return d.slice(5)
}

function fmtDateLabel(v: any): string {
  const d = new Date(v)
  return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function momentumBarColor(v: number): string {
  if (v > 70) return '#3b82f6'   // overbought
  if (v > 10) return '#22c55e'   // bullish
  if (v > -10) return '#888'     // neutral
  if (v > -70) return '#ef4444'  // bearish
  return '#eab308'               // oversold
}

function metricStatus(value: number | null, thresholds: [number, number]): { label: string; color: string } {
  if (value == null) return { label: 'N/A', color: '#555' }
  if (value >= thresholds[1]) return { label: 'Positive', color: '#22c55e' }
  if (value <= thresholds[0]) return { label: 'Negative', color: '#ef4444' }
  return { label: 'Neutral', color: '#888' }
}

/* ── Main Component ───────────────────────────────────── */

interface MomentumPageProps {
  symbol: string | null
}

export default function MomentumPage({ symbol }: MomentumPageProps) {
  const { data, isLoading } = useMomentumPage(symbol)
  const [scatterPeriod, setScatterPeriod] = useState('30')
  const [distHorizon, setDistHorizon] = useState('30')

  if (!symbol) {
    return (
      <div className="h-full flex items-center justify-center text-text-secondary text-xs">
        Select a symbol from the screener below
      </div>
    )
  }

  if (isLoading) return <div className="text-text-secondary text-xs p-3">Loading momentum page...</div>
  if (!data) return <div className="text-text-secondary text-xs p-3">No momentum data for {symbol}</div>

  const sym = symbol.replace('USDT', '')
  const m = data.metrics
  const hasMetrics = m.momentum_value != null

  // Metric cards data
  const csStatus = metricStatus(m.cs_decile, [3, 7])
  const tsReturn = m.ts_decile != null ? (m.ts_decile >= 7 ? 'Pos' : m.ts_decile <= 3 ? 'Neg' : 'Neut') : null
  const relVol = m.relative_volume
  const prox52w = m.proximity_52w_high

  const relVolStatus = metricStatus(
    relVol != null ? (relVol > 1.5 ? 2 : relVol < 0.8 ? 0 : 1) : null,
    [0.5, 1.5]
  )
  const proxStatus = metricStatus(
    prox52w != null ? (prox52w < 5 ? 2 : prox52w > 20 ? 0 : 1) : null,
    [0.5, 1.5]
  )

  return (
    <div className="h-full overflow-y-auto p-2 space-y-2">
      {/* ── Header ─────────────────────────────────────── */}
      <div className="flex items-center gap-4 px-1">
        <span className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">
          Momentum — {sym}
        </span>
        <span className={`text-[10px] font-mono px-2 py-0.5 rounded ${
          data.regime === 'Bullish' ? 'bg-[#22c55e]/15 text-[#22c55e]' :
          data.regime === 'Bearish' ? 'bg-[#ef4444]/15 text-[#ef4444]' :
          'bg-[#333] text-[#888]'
        }`}>
          {data.regime}
        </span>
        {hasMetrics && (
          <span className="text-[10px] font-mono text-text-primary">
            Score: {m.momentum_value! > 0 ? '+' : ''}{m.momentum_value?.toFixed(1)}
          </span>
        )}
      </div>

      {/* ── Metric Cards ───────────────────────────────── */}
      {hasMetrics && (
        <div className="grid grid-cols-4 gap-2">
          <MetricCard
            title="Cross-Sectional"
            status={csStatus}
            value={`Decile ${m.cs_decile}`}
          />
          <MetricCard
            title="Time Series"
            status={metricStatus(m.ts_decile, [3, 7])}
            value={`Decile ${m.ts_decile}`}
          />
          <MetricCard
            title="Relative Volume"
            status={relVol != null
              ? relVol > 1.5 ? { label: 'Positive', color: '#22c55e' }
                : relVol < 0.8 ? { label: 'Negative', color: '#ef4444' }
                : { label: 'Neutral', color: '#888' }
              : { label: 'N/A', color: '#555' }
            }
            value={relVol != null ? `${relVol.toFixed(1)}x` : 'N/A'}
          />
          <MetricCard
            title="52W High Prox"
            status={prox52w != null
              ? prox52w < 5 ? { label: 'Positive', color: '#22c55e' }
                : prox52w > 20 ? { label: 'Negative', color: '#ef4444' }
                : { label: 'Neutral', color: '#888' }
              : { label: 'N/A', color: '#555' }
            }
            value={prox52w != null ? `${prox52w.toFixed(1)}% away` : 'N/A'}
          />
        </div>
      )}

      {/* ── Price + Momentum Histogram ─────────────────── */}
      {data.history.length > 0 && (
        <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded flex flex-col">
          <div className="px-3 pt-2.5 pb-1">
            <span className="text-[11px] text-text-primary font-medium">Price + Momentum</span>
          </div>
          <div style={{ height: 260 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={data.history} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="date" tickFormatter={fmtDate} {...AXIS} minTickGap={40} />
                <YAxis
                  yAxisId="price"
                  {...AXIS}
                  tickFormatter={(v: number) => v >= 1e3 ? `$${(v / 1e3).toFixed(1)}K` : `$${v.toFixed(0)}`}
                  width={52}
                />
                <YAxis
                  yAxisId="mom"
                  orientation="right"
                  {...AXIS}
                  tickFormatter={(v: number) => v.toFixed(0)}
                  width={32}
                  domain={[-100, 100]}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelFormatter={fmtDateLabel}
                  separator=": "
                  formatter={(v: any, name: any) => {
                    if (name === 'price') return [`$${Number(v).toLocaleString()}`, 'Price']
                    return [Number(v)?.toFixed(1), 'Momentum']
                  }}
                />
                <ReferenceLine yAxisId="mom" y={0} stroke="#333" />
                <ReferenceLine yAxisId="mom" y={10} stroke="#22c55e" strokeDasharray="3 3" strokeOpacity={0.2} />
                <ReferenceLine yAxisId="mom" y={-10} stroke="#ef4444" strokeDasharray="3 3" strokeOpacity={0.2} />
                <ReferenceLine yAxisId="mom" y={70} stroke="#3b82f6" strokeDasharray="3 3" strokeOpacity={0.2} />
                <ReferenceLine yAxisId="mom" y={-70} stroke="#eab308" strokeDasharray="3 3" strokeOpacity={0.2} />
                <Bar yAxisId="mom" dataKey="momentum" maxBarSize={3}>
                  {data.history.map((entry, i) => (
                    <Cell key={i} fill={momentumBarColor(entry.momentum ?? 0)} fillOpacity={0.8} />
                  ))}
                </Bar>
                <Line
                  yAxisId="price"
                  type="monotone"
                  dataKey="price"
                  stroke="#e2e8f0"
                  strokeWidth={1.2}
                  dot={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* ── DI + VR Time Series ────────────────────────── */}
      {data.history.length > 0 && (
        <div className="grid grid-cols-2 gap-2" style={{ height: 180 }}>
          {/* Directional Intensity */}
          <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded flex flex-col">
            <div className="flex items-center gap-3 px-3 pt-2 pb-1">
              <span className="text-[11px] text-text-primary font-medium">Directional Intensity</span>
              {m.di != null && (
                <span className={`text-[10px] font-mono ${m.di > 0 ? 'text-[#22c55e]' : m.di < 0 ? 'text-[#ef4444]' : 'text-[#888]'}`}>
                  {m.di > 0 ? '+' : ''}{m.di.toFixed(2)}
                </span>
              )}
            </div>
            <div className="flex-1 min-h-0">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.history} margin={{ top: 0, right: 8, left: 0, bottom: 0 }}>
                  <XAxis dataKey="date" tickFormatter={fmtDate} {...AXIS} minTickGap={40} />
                  <YAxis {...AXIS} domain={[-1, 1]} tickFormatter={(v: number) => v.toFixed(1)} width={32} />
                  <ReferenceLine y={0} stroke="#333" />
                  <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={fmtDateLabel} formatter={(v: any) => [Number(v)?.toFixed(3), 'DI']} />
                  <Bar dataKey="di" maxBarSize={2}>
                    {data.history.map((entry, i) => (
                      <Cell key={i} fill={(entry.di ?? 0) >= 0 ? '#22c55e' : '#ef4444'} fillOpacity={0.7} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Volatility Regime */}
          <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded flex flex-col">
            <div className="flex items-center gap-3 px-3 pt-2 pb-1">
              <span className="text-[11px] text-text-primary font-medium">Volatility Regime</span>
              {m.vol_regime != null && (
                <span className={`text-[10px] font-mono ${m.vol_regime > 0 ? 'text-[#ef4444]' : 'text-[#22c55e]'}`}>
                  {m.vol_regime > 0 ? 'Expanding' : 'Contracting'}
                </span>
              )}
            </div>
            <div className="flex-1 min-h-0">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data.history} margin={{ top: 0, right: 8, left: 0, bottom: 0 }}>
                  <XAxis dataKey="date" tickFormatter={fmtDate} {...AXIS} minTickGap={40} />
                  <YAxis {...AXIS} tickFormatter={(v: number) => v.toFixed(3)} width={42} />
                  <ReferenceLine y={0} stroke="#333" />
                  <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={fmtDateLabel} formatter={(v: any) => [Number(v)?.toFixed(4), 'VR']} />
                  <Line type="monotone" dataKey="vr" stroke="#a78bfa" strokeWidth={1} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* ── Scatter Plots: DI + VR vs Forward Return ──── */}
      {(Object.keys(data.di_scatter).length > 0 || Object.keys(data.vr_scatter).length > 0) && (
        <>
          <div className="flex items-center gap-2 px-1">
            <span className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">
              Forward Return Analysis
            </span>
            <div className="flex items-center gap-1">
              {['10', '30', '60'].map((p) => (
                <button
                  key={p}
                  onClick={() => setScatterPeriod(p)}
                  className={`px-2 py-0.5 text-[10px] font-mono rounded ${
                    scatterPeriod === p
                      ? 'bg-[#1a1a1a] text-text-primary'
                      : 'text-[#555] hover:text-text-secondary'
                  }`}
                >
                  {p}d
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2" style={{ height: 220 }}>
            <ScatterCard
              title="DI vs Forward Return"
              scatter={data.di_scatter[scatterPeriod]}
              period={scatterPeriod}
              xLabel="DI"
            />
            <ScatterCard
              title="VR vs Forward Return"
              scatter={data.vr_scatter[scatterPeriod]}
              period={scatterPeriod}
              xLabel="VR"
            />
          </div>
        </>
      )}

      {/* ── Signal Gauges ──────────────────────────────── */}
      {(data.momentum_stats && 'score' in data.momentum_stats) && (
        <div className="grid grid-cols-2 gap-2">
          <SignalGauge
            label="Momentum Indicator"
            zones={[
              { from: -100, to: -70, color: '#eab308', label: 'Oversold' },
              { from: -70, to: -10, color: '#ef4444', label: 'Bearish' },
              { from: -10, to: 10, color: '#888', label: 'Neutral' },
              { from: 10, to: 70, color: '#22c55e', label: 'Bullish' },
              { from: 70, to: 100, color: '#3b82f6', label: 'Overbought' },
            ]}
            value={data.momentum_stats.score}
            min={-100}
            max={100}
            stats={data.momentum_stats}
          />
          {data.skew_stats && 'score' in data.skew_stats && (
            <SignalGauge
              label="Volatility Skew"
              zones={[
                { from: 0, to: 30, color: '#ef4444', label: 'Bearish' },
                { from: 30, to: 70, color: '#888', label: 'Neutral' },
                { from: 70, to: 100, color: '#22c55e', label: 'Bullish' },
              ]}
              value={data.skew_stats.score}
              min={0}
              max={100}
              stats={{
                score: data.skew_stats.score,
                zscore: data.skew_stats.zscore,
                avg: data.skew_stats.avg,
                change_30d: data.skew_stats.change_30d,
              }}
              extraStats={[
                { label: 'Skew', value: data.skew_stats.skew.toFixed(2) },
              ]}
            />
          )}
        </div>
      )}

      {/* ── Price Distribution ─────────────────────────── */}
      {Object.keys(data.price_distribution).length > 0 && (
        <PriceDistributionCard
          distribution={data.price_distribution}
          horizon={distHorizon}
          onHorizonChange={setDistHorizon}
          symbol={sym}
          price={data.history[data.history.length - 1]?.price ?? 0}
        />
      )}
    </div>
  )
}

/* ── Metric Card ──────────────────────────────────────── */

function MetricCard({
  title,
  status,
  value,
}: {
  title: string
  status: { label: string; color: string }
  value: string
}) {
  return (
    <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded px-3 py-2">
      <div className="text-[9px] text-[#555] uppercase tracking-wider mb-1">{title}</div>
      <div className="flex items-center justify-between">
        <span
          className="text-[10px] font-mono font-medium px-1.5 py-0.5 rounded"
          style={{ backgroundColor: `${status.color}15`, color: status.color }}
        >
          {status.label}
        </span>
        <span className="text-[11px] font-mono text-text-primary">{value}</span>
      </div>
    </div>
  )
}

/* ── Scatter Card ─────────────────────────────────────── */

function ScatterCard({
  title,
  scatter,
  period,
  xLabel,
}: {
  title: string
  scatter: ScatterPeriod | undefined
  period: string
  xLabel: string
}) {
  if (!scatter || scatter.n < 10) {
    return (
      <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded flex items-center justify-center">
        <span className="text-[10px] text-[#555]">Insufficient data</span>
      </div>
    )
  }

  const regressionLine = [
    { x: Math.min(...scatter.points.map((p) => p.x)), y: 0 },
    { x: Math.max(...scatter.points.map((p) => p.x)), y: 0 },
  ]
  // Calculate regression endpoints
  const xMin = Math.min(...scatter.points.map((p) => p.x))
  const xMax = Math.max(...scatter.points.map((p) => p.x))

  return (
    <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded flex flex-col">
      <div className="flex items-center gap-3 px-3 pt-2 pb-1">
        <span className="text-[11px] text-text-primary font-medium">{title} ({period}d)</span>
        <span className="text-[9px] font-mono text-[#555]">
          R²: {scatter.r2.toFixed(4)} | n={scatter.n}
        </span>
        <span className="text-[9px] font-mono text-[#eab308]">
          Avg: {scatter.avg_at_current > 0 ? '+' : ''}{scatter.avg_at_current.toFixed(1)}%
        </span>
      </div>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
            <CartesianGrid {...GRID} />
            <XAxis
              type="number"
              dataKey="x"
              name={xLabel}
              {...AXIS}
              tickFormatter={(v: number) => v.toFixed(2)}
              label={{ value: xLabel, position: 'bottom', fontSize: 8, fill: '#555', offset: -2 }}
            />
            <YAxis
              type="number"
              dataKey="y"
              name="Return"
              {...AXIS}
              tickFormatter={(v: number) => `${v.toFixed(0)}%`}
              width={36}
            />
            <ZAxis range={[8, 8]} />
            <ReferenceLine y={0} stroke="#333" />
            <Scatter data={scatter.points} fill="#555" fillOpacity={0.4} />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              formatter={(v: any, name: any) => {
                if (name === xLabel) return [Number(v).toFixed(4), xLabel]
                return [`${Number(v).toFixed(2)}%`, `${period}d Return`]
              }}
            />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

/* ── Signal Gauge ─────────────────────────────────────── */

interface GaugeZone {
  from: number
  to: number
  color: string
  label: string
}

function SignalGauge({
  label,
  zones,
  value,
  min,
  max,
  stats,
  extraStats,
}: {
  label: string
  zones: GaugeZone[]
  value: number
  min: number
  max: number
  stats: { score: number; zscore: number; avg: number; change_30d: number }
  extraStats?: { label: string; value: string }[]
}) {
  const range = max - min
  const pct = Math.max(0, Math.min(100, ((value - min) / range) * 100))

  return (
    <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded px-3 py-2.5">
      <div className="text-[10px] text-text-primary font-medium mb-2">{label}</div>

      {/* Gauge bar */}
      <div className="relative h-3 rounded-full overflow-hidden flex mb-1.5">
        {zones.map((z) => {
          const width = ((z.to - z.from) / range) * 100
          return (
            <div
              key={z.from}
              style={{ width: `${width}%`, backgroundColor: z.color }}
              className="h-full opacity-30"
            />
          )
        })}
        {/* Indicator */}
        <div
          className="absolute top-0 h-full w-0.5 bg-white"
          style={{ left: `${pct}%`, transform: 'translateX(-50%)' }}
        />
        <div
          className="absolute -top-1 w-2 h-2 bg-white rounded-full border border-[#333]"
          style={{ left: `${pct}%`, transform: 'translateX(-50%)' }}
        />
      </div>

      {/* Zone labels */}
      <div className="flex justify-between mb-2">
        {zones.map((z) => {
          const width = ((z.to - z.from) / range) * 100
          return (
            <div key={z.from} style={{ width: `${width}%` }} className="text-center">
              <span className="text-[7px] uppercase tracking-wider" style={{ color: z.color }}>
                {z.label}
              </span>
            </div>
          )
        })}
      </div>

      {/* Stats */}
      <div className="flex items-center gap-3 text-[9px] font-mono text-[#888]">
        <span>Score: <span className="text-text-primary">{stats.score > 0 ? '+' : ''}{stats.score.toFixed(1)}</span></span>
        <span>Z: <span className="text-text-primary">{stats.zscore.toFixed(2)}</span></span>
        <span>Avg: <span className="text-text-primary">{stats.avg.toFixed(1)}</span></span>
        <span>30d: <span className={stats.change_30d >= 0 ? 'text-[#22c55e]' : 'text-[#ef4444]'}>
          {stats.change_30d > 0 ? '+' : ''}{stats.change_30d.toFixed(1)}
        </span></span>
        {extraStats?.map((s) => (
          <span key={s.label}>{s.label}: <span className="text-text-primary">{s.value}</span></span>
        ))}
      </div>
    </div>
  )
}

/* ── Price Distribution Card ──────────────────────────── */

function PriceDistributionCard({
  distribution,
  horizon,
  onHorizonChange,
  symbol,
  price,
}: {
  distribution: Record<string, PriceDistHorizon>
  horizon: string
  onHorizonChange: (h: string) => void
  symbol: string
  price: number
}) {
  const horizons = ['7', '10', '14', '30', '60']
  const dist = distribution[horizon]
  if (!dist) return null

  const impl = dist.implied
  const adj = dist.adjusted

  // Build bar chart data showing ranges
  const barData = [
    { name: 'Implied 2σ', low: impl.low_2s, high: impl.high_2s, fill: '#555', opacity: 0.2 },
    { name: 'Implied 1σ', low: impl.low_1s, high: impl.high_1s, fill: '#a78bfa', opacity: 0.3 },
    { name: 'Adjusted 1σ', low: adj.low_1s, high: adj.high_1s, fill: '#22c55e', opacity: 0.4 },
    { name: 'Adjusted 2σ', low: adj.low_2s, high: adj.high_2s, fill: '#22c55e', opacity: 0.15 },
  ]

  function fmtPrice(v: number): string {
    if (v >= 1000) return `$${(v / 1000).toFixed(1)}K`
    return `$${v.toFixed(0)}`
  }

  return (
    <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded px-3 py-2.5">
      <div className="flex items-center gap-3 mb-2">
        <span className="text-[11px] text-text-primary font-medium">
          Price Distribution — {symbol}
        </span>
        <div className="flex items-center gap-1">
          {horizons.map((h) => (
            <button
              key={h}
              onClick={() => onHorizonChange(h)}
              className={`px-2 py-0.5 text-[10px] font-mono rounded ${
                horizon === h
                  ? 'bg-[#1a1a1a] text-text-primary'
                  : 'text-[#555] hover:text-text-secondary'
              }`}
            >
              {h}d
            </button>
          ))}
        </div>
      </div>

      {/* Distribution visualization */}
      <div className="grid grid-cols-2 gap-4 mb-2">
        {/* Implied */}
        <div>
          <div className="text-[9px] text-[#888] uppercase tracking-wider mb-1">Implied</div>
          <div className="text-[10px] font-mono text-text-secondary mb-0.5">
            <span className="text-[#a78bfa]">±{impl.vol_pct.toFixed(1)}%</span>
          </div>
          <div className="text-[10px] font-mono text-text-secondary">
            1σ: <span className="text-text-primary">{fmtPrice(impl.low_1s)}</span>
            {' – '}
            <span className="text-text-primary">{fmtPrice(impl.high_1s)}</span>
          </div>
          <div className="text-[10px] font-mono text-text-secondary">
            2σ: <span className="text-text-primary">{fmtPrice(impl.low_2s)}</span>
            {' – '}
            <span className="text-text-primary">{fmtPrice(impl.high_2s)}</span>
          </div>
        </div>

        {/* Adjusted */}
        <div>
          <div className="text-[9px] text-[#888] uppercase tracking-wider mb-1">Momentum-Adjusted</div>
          <div className="text-[10px] font-mono text-text-secondary mb-0.5">
            <span className="text-[#22c55e]">±{adj.vol_pct.toFixed(1)}%</span>
            {adj.center !== Math.round(price) && (
              <span className="text-[9px] text-[#555] ml-2">
                center: {fmtPrice(adj.center)}
              </span>
            )}
          </div>
          <div className="text-[10px] font-mono text-text-secondary">
            1σ: <span className="text-text-primary">{fmtPrice(adj.low_1s)}</span>
            {' – '}
            <span className="text-text-primary">{fmtPrice(adj.high_1s)}</span>
          </div>
          <div className="text-[10px] font-mono text-text-secondary">
            2σ: <span className="text-text-primary">{fmtPrice(adj.low_2s)}</span>
            {' – '}
            <span className="text-text-primary">{fmtPrice(adj.high_2s)}</span>
          </div>
        </div>
      </div>

      {/* Range bars */}
      <div className="space-y-1">
        {barData.map((b) => {
          const allVals = barData.flatMap((d) => [d.low, d.high])
          const globalMin = Math.min(...allVals)
          const globalMax = Math.max(...allVals)
          const range = globalMax - globalMin
          const left = ((b.low - globalMin) / range) * 100
          const width = ((b.high - b.low) / range) * 100
          const pricePos = ((price - globalMin) / range) * 100

          return (
            <div key={b.name} className="relative h-4">
              <div
                className="absolute h-full rounded-sm"
                style={{
                  left: `${left}%`,
                  width: `${width}%`,
                  backgroundColor: b.fill,
                  opacity: b.opacity,
                }}
              />
              <div
                className="absolute h-full w-px bg-white/40"
                style={{ left: `${pricePos}%` }}
              />
              <span className="absolute text-[7px] text-[#555] right-0 top-0 leading-4">
                {b.name}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
