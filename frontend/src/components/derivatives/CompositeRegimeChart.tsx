import {
  ResponsiveContainer,
  ComposedChart,
  LineChart,
  Line,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
} from 'recharts'

interface CompositeRegimeChartProps {
  data: Array<{
    date: string
    price: number
    composite: number
    composite_sma5?: number
  }>
  symbol: string
  currentComposite: number
  currentPercentile: number
}

function regimeColor(z: number): string {
  if (z <= -2) return '#22c55e'
  if (z <= -1) return '#2dd4bf'
  if (z <= 0) return '#a3e635'
  if (z <= 1) return '#eab308'
  if (z <= 2) return '#f97316'
  return '#ef4444'
}

export default function CompositeRegimeChart({
  data,
  symbol,
  currentComposite,
  currentPercentile,
}: CompositeRegimeChartProps) {
  if (!data.length) return null

  return (
    <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-4 px-3 pt-2.5 pb-1">
        <span className="text-[11px] text-text-primary font-medium">
          {symbol} Composite Regime
        </span>
        <span className="text-[10px] font-mono text-text-secondary">
          {symbol}: ${data[data.length - 1]?.price.toLocaleString()}
        </span>
        <span className="text-[10px] font-mono text-text-secondary">
          Composite:{' '}
          <span style={{ color: currentComposite >= 0 ? '#ef4444' : '#22c55e' }}>
            {currentComposite.toFixed(2)}
          </span>
        </span>
        <span className="text-[10px] font-mono text-text-secondary">
          Percentile: {currentPercentile.toFixed(0)}%
        </span>
      </div>

      {/* Price chart — top */}
      <div style={{ height: 150 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
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
              labelFormatter={(v: any) => {
                const d = new Date(v)
                return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
              }}
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

      {/* Composite bars + SMA-5 — bottom */}
      <div style={{ height: 90 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 0, right: 8, left: 0, bottom: 0 }}>
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
                background: '#222',
                border: '1px solid #444',
                borderRadius: 4,
                fontSize: 11,
                padding: '6px 10px',
                color: '#e2e8f0',
              }}
              labelStyle={{ color: '#999', fontSize: 10 }}
              itemStyle={{ color: '#e2e8f0' }}
              labelFormatter={(v: any) => {
                const d = new Date(v)
                return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
              }}
              separator=": "
              formatter={(v: any, name: any) => [Number(v).toFixed(2), name === 'composite_sma5' ? 'SMA-5' : 'Composite Z']}
              cursor={{ stroke: '#333', strokeWidth: 1 }}
            />
            <ReferenceLine y={0} stroke="#333" />
            <ReferenceLine y={2} stroke="#2a2a2a" strokeDasharray="4 4" />
            <ReferenceLine y={-2} stroke="#2a2a2a" strokeDasharray="4 4" />
            <Bar dataKey="composite" maxBarSize={3}>
              {data.map((entry, i) => (
                <Cell key={i} fill={regimeColor(entry.composite)} fillOpacity={0.85} />
              ))}
            </Bar>
            {data.some((d) => d.composite_sma5 != null) && (
              <Line
                type="monotone"
                dataKey="composite_sma5"
                stroke="#eab308"
                strokeWidth={1}
                dot={false}
                connectNulls
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
