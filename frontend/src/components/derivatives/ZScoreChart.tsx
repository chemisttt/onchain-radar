import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts'

interface ZScoreChartProps {
  data: Array<Record<string, unknown>>
  dataKey: string
  color?: string
}

export default function ZScoreChart({
  data,
  dataKey,
  color = '#5ba3ad',
}: ZScoreChartProps) {
  if (!data.length) {
    return <div className="text-text-secondary text-xs p-2">No data</div>
  }

  // Limit to last 180 days for z-score charts
  const sliced = data.slice(-180)

  return (
    <div className="h-full w-full flex flex-col">
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={sliced} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
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
              width={32}
              tickLine={false}
              axisLine={false}
              domain={['auto', 'auto']}
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
              formatter={(value: any) => [Number(value).toFixed(2), `${dataKey.replace('_zscore', '').replace('_', ' ').toUpperCase()} Z-Score`]}
              cursor={{ stroke: '#333', strokeWidth: 1 }}
            />
            {/* Reference lines — dimmer, no labels */}
            <ReferenceLine y={2} stroke="#333" strokeDasharray="4 4" />
            <ReferenceLine y={1} stroke="#2a2a2a" strokeDasharray="4 4" />
            <ReferenceLine y={0} stroke="#333" />
            <ReferenceLine y={-1} stroke="#2a2a2a" strokeDasharray="4 4" />
            <ReferenceLine y={-2} stroke="#333" strokeDasharray="4 4" />
            <Line
              type="monotone"
              dataKey={dataKey}
              stroke={color}
              dot={false}
              strokeWidth={1.5}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
