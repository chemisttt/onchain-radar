import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts'

interface ExpandedChartModalProps {
  title: string
  onClose: () => void
  data: Array<Record<string, unknown>>
  metricKey: string
  metricColor: string
  metricFormatY?: (v: number) => string
}

export default function ExpandedChartModal({
  title,
  onClose,
  data,
  metricKey,
  metricColor,
  metricFormatY,
}: ExpandedChartModalProps) {
  const yFmt = metricFormatY || ((v: number) => v.toFixed(2))
  const isZscore = metricKey.includes('zscore')

  return (
    <div
      className="fixed inset-0 z-50 bg-[#080808]/95 flex flex-col"
      onClick={onClose}
    >
      <div
        className="flex flex-col h-full max-w-[1400px] w-full mx-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-3 flex-shrink-0">
          <span className="text-sm text-text-primary font-medium">{title}</span>
          <button
            onClick={onClose}
            className="text-[#555] hover:text-text-primary transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Price chart — top 55% */}
        <div className="flex-[55] min-h-0 px-6">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
              <XAxis dataKey="date" hide />
              <YAxis
                tick={{ fontSize: 9, fill: '#555' }}
                tickFormatter={(v: number) => {
                  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`
                  return `$${v.toFixed(2)}`
                }}
                width={58}
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

        {/* Metric chart — bottom 45% */}
        <div className="flex-[45] min-h-0 px-6 pb-6">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
              <XAxis
                dataKey="date"
                tick={{ fontSize: 9, fill: '#555' }}
                interval="preserveStartEnd"
                tickLine={false}
                axisLine={{ stroke: '#222' }}
                tickFormatter={(v: string) => {
                  // Show full date in expanded view
                  const d = new Date(v)
                  return `${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}, ${d.getFullYear().toString().slice(2)}`
                }}
              />
              <YAxis
                tick={{ fontSize: 9, fill: '#555' }}
                tickFormatter={yFmt}
                width={58}
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
                formatter={(v: any) => [yFmt(Number(v)), metricKey.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())]}
                cursor={{ stroke: '#333', strokeWidth: 1 }}
              />
              {isZscore && (
                <>
                  <ReferenceLine y={2} stroke="#333" strokeDasharray="4 4" />
                  <ReferenceLine y={1} stroke="#2a2a2a" strokeDasharray="4 4" />
                  <ReferenceLine y={0} stroke="#333" />
                  <ReferenceLine y={-1} stroke="#2a2a2a" strokeDasharray="4 4" />
                  <ReferenceLine y={-2} stroke="#333" strokeDasharray="4 4" />
                </>
              )}
              {!isZscore && <ReferenceLine y={0} stroke="#222" />}
              <Line
                type="monotone"
                dataKey={metricKey}
                stroke={metricColor}
                strokeWidth={1.5}
                dot={false}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}
