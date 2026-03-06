import {
  ResponsiveContainer,
  ComposedChart,
  LineChart,
  BarChart,
  Line,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
} from 'recharts'
import { useMomentum } from '../../hooks/useMomentum'

function fmtDateLabel(v: any): string {
  const d = new Date(v)
  return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function regimeColor(z: number): string {
  if (z <= -2) return '#22c55e'
  if (z <= -1) return '#2dd4bf'
  if (z <= 0) return '#a3e635'
  if (z <= 1) return '#eab308'
  if (z <= 2) return '#f97316'
  return '#ef4444'
}

interface MomentumTabProps {
  symbol: string | null
}

export default function MomentumTab({ symbol }: MomentumTabProps) {
  const { data, isLoading } = useMomentum(symbol)

  if (!symbol) {
    return (
      <div className="h-full flex items-center justify-center text-text-secondary text-xs">
        Select a symbol from the screener below
      </div>
    )
  }

  if (isLoading) {
    return <div className="text-text-secondary text-xs p-3">Loading momentum data...</div>
  }

  if (!data) {
    return <div className="text-text-secondary text-xs p-3">No momentum data for {symbol}</div>
  }

  const sym = symbol.replace('USDT', '')
  const hasOptions = data.has_options_data

  // Latest values for footers
  const latestIvRv = data.iv_rv.length > 0 ? data.iv_rv[data.iv_rv.length - 1] : null
  const latestSkew = data.skew_zscore.length > 0 ? data.skew_zscore[data.skew_zscore.length - 1] : null
  const latestRv = data.price_rv.length > 0 ? data.price_rv[data.price_rv.length - 1] : null

  return (
    <div className="h-full overflow-y-auto p-2 space-y-2">
      {/* Section header */}
      <div className="text-[11px] text-text-secondary uppercase tracking-wider font-medium px-1">
        Momentum — {sym}
      </div>

      {/* Price / IV / RV chart (BTC/ETH with IV, others RV-only) */}
      <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded flex flex-col">
        <div className="flex items-center gap-4 px-3 pt-2.5 pb-1">
          <span className="text-[11px] text-text-primary font-medium">
            {hasOptions ? 'Price / IV / RV' : 'Price / Realized Volatility'}
          </span>
          {hasOptions && latestIvRv && (
            <>
              <span className="text-[10px] font-mono text-[#eab308]">
                IV: {latestIvRv.iv_30d?.toFixed(1)}%
              </span>
              <span className="text-[10px] font-mono text-[#06b6d4]">
                RV: {latestIvRv.rv_30d?.toFixed(1)}%
              </span>
              {latestIvRv.iv_30d != null && latestIvRv.rv_30d != null && (
                <span className={`text-[10px] font-mono ${
                  latestIvRv.iv_30d - latestIvRv.rv_30d >= 0 ? 'text-[#eab308]' : 'text-[#06b6d4]'
                }`}>
                  Spread: {(latestIvRv.iv_30d - latestIvRv.rv_30d) > 0 ? '+' : ''}
                  {(latestIvRv.iv_30d - latestIvRv.rv_30d).toFixed(1)}%
                </span>
              )}
            </>
          )}
          {!hasOptions && latestRv && (
            <span className="text-[10px] font-mono text-[#06b6d4]">
              RV 30d: {latestRv.rv_30d?.toFixed(1)}%
            </span>
          )}
        </div>

        <div style={{ height: 280 }}>
          <ResponsiveContainer width="100%" height="100%">
            {hasOptions && data.iv_rv.length > 0 ? (
              <ComposedChart data={data.iv_rv} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 8, fill: '#555' }}
                  interval="preserveStartEnd"
                  tickLine={false}
                  axisLine={{ stroke: '#222' }}
                  tickFormatter={(v: string) => v.slice(5)}
                />
                <YAxis
                  yAxisId="price"
                  tick={{ fontSize: 8, fill: '#555' }}
                  tickFormatter={(v: number) => {
                    if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`
                    return `$${v.toFixed(0)}`
                  }}
                  width={52}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  yAxisId="vol"
                  orientation="right"
                  tick={{ fontSize: 8, fill: '#555' }}
                  tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                  width={42}
                  tickLine={false}
                  axisLine={false}
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
                  labelStyle={{ color: '#999', fontSize: 10 }}
                  itemStyle={{ color: '#e2e8f0' }}
                  labelFormatter={fmtDateLabel}
                  separator=": "
                  formatter={(v: any, name: any) => {
                    if (name === 'price') return [`$${Number(v).toLocaleString()}`, 'Price']
                    return [`${Number(v)?.toFixed(1)}%`, name === 'iv_30d' ? 'IV 30d' : 'RV 30d']
                  }}
                  cursor={{ stroke: '#333', strokeWidth: 1 }}
                />
                <Line
                  yAxisId="price"
                  type="monotone"
                  dataKey="price"
                  stroke="#e2e8f0"
                  strokeWidth={1.2}
                  dot={false}
                />
                <Line
                  yAxisId="vol"
                  type="monotone"
                  dataKey="iv_30d"
                  stroke="#eab308"
                  strokeWidth={1}
                  dot={false}
                  connectNulls
                />
                <Line
                  yAxisId="vol"
                  type="monotone"
                  dataKey="rv_30d"
                  stroke="#06b6d4"
                  strokeWidth={1}
                  dot={false}
                  connectNulls
                />
              </ComposedChart>
            ) : (
              <ComposedChart data={data.price_rv} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 8, fill: '#555' }}
                  interval="preserveStartEnd"
                  tickLine={false}
                  axisLine={{ stroke: '#222' }}
                  tickFormatter={(v: string) => v.slice(5)}
                />
                <YAxis
                  yAxisId="price"
                  tick={{ fontSize: 8, fill: '#555' }}
                  tickFormatter={(v: number) => {
                    if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`
                    return `$${v.toFixed(0)}`
                  }}
                  width={52}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  yAxisId="vol"
                  orientation="right"
                  tick={{ fontSize: 8, fill: '#555' }}
                  tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                  width={42}
                  tickLine={false}
                  axisLine={false}
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
                  labelStyle={{ color: '#999', fontSize: 10 }}
                  itemStyle={{ color: '#e2e8f0' }}
                  labelFormatter={fmtDateLabel}
                  separator=": "
                  formatter={(v: any, name: any) => {
                    if (name === 'price') return [`$${Number(v).toLocaleString()}`, 'Price']
                    return [`${Number(v)?.toFixed(1)}%`, 'RV 30d']
                  }}
                  cursor={{ stroke: '#333', strokeWidth: 1 }}
                />
                <Line
                  yAxisId="price"
                  type="monotone"
                  dataKey="price"
                  stroke="#e2e8f0"
                  strokeWidth={1.2}
                  dot={false}
                />
                <Line
                  yAxisId="vol"
                  type="monotone"
                  dataKey="rv_30d"
                  stroke="#06b6d4"
                  strokeWidth={1}
                  dot={false}
                  connectNulls
                />
              </ComposedChart>
            )}
          </ResponsiveContainer>
        </div>
      </div>

      {/* 25d Skew Z-Score (BTC/ETH only) */}
      {hasOptions && data.skew_zscore.length > 0 ? (
        <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded flex flex-col">
          <div className="flex items-center gap-4 px-3 pt-2.5 pb-1">
            <span className="text-[11px] text-text-primary font-medium">25d Skew Z-Score</span>
            {latestSkew && (
              <>
                <span className="text-[10px] font-mono text-text-secondary">
                  Skew: <span className="text-text-primary">{latestSkew.skew_25d?.toFixed(1)}</span>
                </span>
                <span className="text-[10px] font-mono text-text-secondary">
                  Z-Score: <span style={{ color: regimeColor(latestSkew.skew_zscore ?? 0) }}>
                    {latestSkew.skew_zscore?.toFixed(2)}
                  </span>
                </span>
              </>
            )}
          </div>

          {/* Price line — top */}
          <div style={{ height: 150 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.skew_zscore} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <XAxis dataKey="date" hide />
                <YAxis
                  tick={{ fontSize: 8, fill: '#555' }}
                  tickFormatter={(v: number) => {
                    if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`
                    return `$${v.toFixed(0)}`
                  }}
                  width={52}
                  tickLine={false}
                  axisLine={false}
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
                  labelStyle={{ color: '#999', fontSize: 10 }}
                  itemStyle={{ color: '#e2e8f0' }}
                  labelFormatter={fmtDateLabel}
                  separator=": "
                  formatter={(v: any) => [`$${Number(v).toLocaleString()}`, 'Price']}
                  cursor={{ stroke: '#333', strokeWidth: 1 }}
                />
                <Line
                  type="monotone"
                  dataKey="price"
                  stroke="#e2e8f0"
                  strokeWidth={1.2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Skew Z bars — bottom */}
          <div style={{ height: 90 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.skew_zscore} margin={{ top: 0, right: 8, left: 0, bottom: 0 }}>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 8, fill: '#555' }}
                  interval="preserveStartEnd"
                  tickLine={false}
                  axisLine={{ stroke: '#222' }}
                  tickFormatter={(v: string) => v.slice(5)}
                />
                <YAxis
                  tick={{ fontSize: 8, fill: '#555' }}
                  tickFormatter={(v: number) => v.toFixed(1)}
                  width={52}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  contentStyle={{
                    background: '#1c1c1c',
                    border: '1px solid #333',
                    borderRadius: 4,
                    fontSize: 11,
                    padding: '4px 8px',
                  }}
                  labelFormatter={fmtDateLabel}
                  separator=": "
                  formatter={(v: any) => [Number(v)?.toFixed(2), 'Skew Z']}
                  cursor={{ stroke: '#333', strokeWidth: 1 }}
                />
                <ReferenceLine y={0} stroke="#333" />
                <ReferenceLine y={2} stroke="#2a2a2a" strokeDasharray="4 4" />
                <ReferenceLine y={-2} stroke="#2a2a2a" strokeDasharray="4 4" />
                <Bar dataKey="skew_zscore" maxBarSize={3}>
                  {data.skew_zscore.map((entry, i) => (
                    <Cell key={i} fill={regimeColor(entry.skew_zscore ?? 0)} fillOpacity={0.85} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : !hasOptions ? (
        <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded px-3 py-4">
          <span className="text-[11px] text-[#555]">
            No options data available for {sym}. IV and Skew data is only available for BTC and ETH.
          </span>
        </div>
      ) : null}
    </div>
  )
}
