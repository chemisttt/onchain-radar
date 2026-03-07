import {
  LineChart,
  Line,
  AreaChart,
  Area,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts'

interface MetricChartProps {
  data: Array<Record<string, unknown>>
  dataKey: string
  color?: string
  chartType?: 'line' | 'area' | 'bar'
  formatValue?: (v: number) => string
  formatY?: (v: number) => string
  barSize?: number
  label?: string
}

export default function MetricChart({
  data,
  dataKey,
  color = '#3B82F6',
  chartType = 'line',
  formatValue,
  formatY,
  barSize,
  label,
}: MetricChartProps) {
  if (!data.length) {
    return <div className="text-text-secondary text-xs p-2">No data</div>
  }

  const fmt = formatValue || ((v: number) => v.toLocaleString())
  const yFmt = formatY || ((v: number) => {
    if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(1)}B`
    if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`
    if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(0)}K`
    return v.toFixed(2)
  })

  const margin = { top: 4, right: 8, left: 0, bottom: 0 }

  const xAxisProps = {
    dataKey: 'date' as const,
    tick: { fontSize: 8, fill: '#555' },
    interval: 'preserveStartEnd' as const,
    tickLine: false,
    axisLine: { stroke: '#222' },
    tickFormatter: (v: string) => v.slice(5),
  }

  const yAxisProps = {
    tick: { fontSize: 8, fill: '#555' },
    tickFormatter: yFmt,
    width: 52,
    tickLine: false,
    axisLine: false,
  }

  const tooltipProps = {
    contentStyle: {
      background: '#222',
      border: '1px solid #444',
      borderRadius: 4,
      fontSize: 11,
      padding: '6px 10px',
      color: '#e2e8f0',
    },
    labelStyle: { color: '#999', fontSize: 10 },
    itemStyle: { color: '#e2e8f0' },
    labelFormatter: (v: any) => {
      const d = new Date(v)
      return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    },
    separator: ': ',
    formatter: (value: any) => [fmt(Number(value)), label || dataKey] as [string, string],
    cursor: chartType === 'bar' ? { fill: 'transparent' } : { stroke: '#333', strokeWidth: 1 },
  }

  const renderChart = () => {
    if (chartType === 'area') {
      return (
        <AreaChart data={data} margin={margin}>
          <defs>
            <linearGradient id={`grad-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.3} />
              <stop offset="100%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <XAxis {...xAxisProps} />
          <YAxis {...yAxisProps} />
          <Tooltip {...tooltipProps} />
          <Area
            type="monotone"
            dataKey={dataKey}
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#grad-${dataKey})`}
            connectNulls
          />
        </AreaChart>
      )
    }

    if (chartType === 'bar') {
      return (
        <BarChart data={data} margin={margin}>
          <XAxis {...xAxisProps} />
          <YAxis {...yAxisProps} />
          <Tooltip {...tooltipProps} />
          <ReferenceLine y={0} stroke="#222" />
          <Bar dataKey={dataKey} maxBarSize={barSize || 4} radius={[1, 1, 0, 0]}>
            {data.map((entry, i) => {
              const val = Number(entry[dataKey]) || 0
              return (
                <Cell
                  key={i}
                  fill={val >= 0 ? '#2dd4bf' : '#ef4444'}
                  fillOpacity={0.85}
                />
              )
            })}
          </Bar>
        </BarChart>
      )
    }

    // Default: line
    return (
      <LineChart data={data} margin={margin}>
        <XAxis {...xAxisProps} />
        <YAxis {...yAxisProps} />
        <Tooltip {...tooltipProps} />
        <ReferenceLine y={0} stroke="#222" />
        <Line
          type="monotone"
          dataKey={dataKey}
          stroke={color}
          dot={false}
          strokeWidth={1.5}
          connectNulls
        />
      </LineChart>
    )
  }

  return (
    <div className="h-full w-full flex flex-col">
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          {renderChart()}
        </ResponsiveContainer>
      </div>
    </div>
  )
}
