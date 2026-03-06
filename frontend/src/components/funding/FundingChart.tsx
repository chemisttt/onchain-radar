import { useMemo } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts'
import { useFundingHistory } from '../../hooks/useFundingHistory'

const EXCHANGE_COLORS: Record<string, string> = {
  Binance: '#F0B90B',
  Bybit: '#F7A600',
  OKX: '#000000',
  MEXC: '#1BA27A',
  Hyperliquid: '#3B82F6',
  Paradex: '#8B5CF6',
  Lighter: '#06B6D4',
  Extended: '#F472B6',
  EdgeX: '#A78BFA',
  Aster: '#FBBF24',
  Variational: '#34D399',
}

interface FundingChartProps {
  symbol: string | null
}

export default function FundingChart({ symbol }: FundingChartProps) {
  const { data: historyData, isLoading } = useFundingHistory(symbol)

  const { chartData, exchanges } = useMemo(() => {
    if (!historyData?.data?.length) return { chartData: [], exchanges: [] }

    const exSet = new Set<string>()
    const chartData = historyData.data.map((point) => {
      const entry: Record<string, unknown> = {
        time: point.time.slice(5, 16), // "03-04 12:00"
      }
      for (const [ex, rate] of Object.entries(point.rates)) {
        // Clamp outliers to ±5% for readable chart
        const pct = rate * 100
        if (Math.abs(pct) > 5) continue
        entry[ex] = +pct.toFixed(5)
        exSet.add(ex)
      }
      return entry
    })

    return { chartData, exchanges: Array.from(exSet) }
  }, [historyData])

  if (!symbol) {
    return (
      <div className="text-text-secondary text-xs p-2">
        Select a symbol to view history
      </div>
    )
  }

  if (isLoading) {
    return <div className="text-text-secondary text-xs p-2">Loading chart...</div>
  }

  if (!chartData.length) {
    return <div className="text-text-secondary text-xs p-2">No history data for {symbol}</div>
  }

  return (
    <div className="h-full w-full">
      <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1">
        {symbol} — 7-Day Funding History (%)
      </div>
      <ResponsiveContainer width="100%" height="90%">
        <LineChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <XAxis
            dataKey="time"
            tick={{ fontSize: 9, fill: '#808080' }}
            interval="preserveStartEnd"
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 9, fill: '#808080' }}
            tickFormatter={(v) => `${v}%`}
            width={50}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{
              background: '#141414',
              border: '1px solid #2a2a2a',
              borderRadius: 2,
              fontSize: 11,
            }}
            labelStyle={{ color: '#808080' }}
            formatter={(value: any) => [`${Number(value).toFixed(4)}%`]}
          />
          <Legend
            wrapperStyle={{ fontSize: 10 }}
            iconSize={8}
          />
          <ReferenceLine y={0} stroke="#2a2a2a" />
          {exchanges.map((ex) => (
            <Line
              key={ex}
              type="monotone"
              dataKey={ex}
              stroke={EXCHANGE_COLORS[ex] || '#808080'}
              dot={chartData.length < 10}
              strokeWidth={1.5}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
