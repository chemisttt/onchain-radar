import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  Cell,
} from 'recharts'
import { useLiquidationMap } from '../../hooks/useLiquidationMap'

const LEVERAGE_COLORS: Record<number, string> = {
  5: '#6366f1',
  10: '#3b82f6',
  25: '#06b6d4',
  50: '#eab308',
  100: '#ef4444',
}

function fmtUsd(v: number): string {
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(1)}B`
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

function fmtPrice(v: number): string {
  if (v >= 100) return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
  if (v >= 1) return `$${v.toFixed(2)}`
  return `$${v.toPrecision(4)}`
}

interface LiquidationMapProps {
  symbol: string | null
}

export default function LiquidationMap({ symbol }: LiquidationMapProps) {
  const { data, isLoading } = useLiquidationMap(symbol)

  if (!symbol) return null
  if (isLoading) return <div className="text-text-secondary text-xs p-2">Loading liquidation map...</div>
  if (!data || !data.levels.length) {
    return <div className="text-text-secondary text-xs p-2">No liquidation data for {symbol}</div>
  }

  const currentPrice = data.current_price

  // Build chart data: each level becomes a row
  // Short liquidations go negative (left), long liquidations go positive (right)
  const chartData = data.levels.map((level) => ({
    price: fmtPrice(level.price),
    rawPrice: level.price,
    long_vol: level.long_vol > 0 ? level.long_vol : 0,
    short_vol: level.short_vol > 0 ? -level.short_vol : 0,
    leverage: level.leverage,
    color: LEVERAGE_COLORS[level.leverage] || '#888',
  }))

  return (
    <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded flex flex-col h-full">
      <div className="flex items-center gap-4 px-3 pt-2.5 pb-1 flex-shrink-0">
        <span className="text-[11px] text-text-primary font-medium">Liquidation Map</span>
        <span className="text-[10px] font-mono text-text-secondary">
          Current: <span className="text-text-primary">{fmtPrice(currentPrice)}</span>
        </span>
        <div className="ml-auto flex items-center gap-2">
          {Object.entries(LEVERAGE_COLORS).map(([lev, color]) => (
            <span key={lev} className="flex items-center gap-0.5 text-[9px] font-mono text-text-secondary">
              <span className="w-2 h-2 rounded-sm inline-block" style={{ backgroundColor: color }} />
              {lev}x
            </span>
          ))}
        </div>
      </div>

      <div className="flex-1 min-h-0 px-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={chartData}
            layout="vertical"
            margin={{ top: 4, right: 8, left: 4, bottom: 0 }}
          >
            <XAxis
              type="number"
              tick={{ fontSize: 8, fill: '#555' }}
              tickFormatter={(v: number) => fmtUsd(Math.abs(v))}
              tickLine={false}
              axisLine={{ stroke: '#222' }}
            />
            <YAxis
              type="category"
              dataKey="price"
              tick={{ fontSize: 8, fill: '#555' }}
              width={64}
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
              separator=": "
              labelFormatter={(label: any) => `Price: ${label}`}
              formatter={(v: any, name: any, props: any) => {
                const side = name === 'long_vol' ? 'Long Liqs' : 'Short Liqs'
                const lev = props?.payload?.leverage ? ` (${props.payload.leverage}x)` : ''
                return [fmtUsd(Math.abs(Number(v))), `${side}${lev}`]
              }}
              cursor={{ fill: 'rgba(255,255,255,0.02)' }}
            />
            <ReferenceLine
              x={0}
              stroke="#444"
              strokeWidth={1}
            />
            {/* Short liquidations (negative = left side) */}
            <Bar dataKey="short_vol" maxBarSize={14}>
              {chartData.map((entry, i) => (
                <Cell key={i} fill={entry.color} fillOpacity={0.75} />
              ))}
            </Bar>
            {/* Long liquidations (positive = right side) */}
            <Bar dataKey="long_vol" maxBarSize={14}>
              {chartData.map((entry, i) => (
                <Cell key={i} fill={entry.color} fillOpacity={0.75} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Recent events feed */}
      {data.recent_events.length > 0 && (
        <div className="px-3 pb-2 pt-1 flex-shrink-0 border-t border-[#1a1a1a]">
          <div className="flex items-center gap-2 overflow-x-auto">
            <span className="text-[9px] text-[#555] uppercase flex-shrink-0">Recent:</span>
            {data.recent_events.slice(0, 8).map((evt, i) => (
              <span
                key={i}
                className={`text-[9px] font-mono flex-shrink-0 ${
                  evt.side === 'long' ? 'text-[#ef4444]' : 'text-[#22c55e]'
                }`}
              >
                {evt.side === 'long' ? '\u25BC' : '\u25B2'} {fmtPrice(evt.price)} ({fmtUsd(evt.usd_value)})
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
