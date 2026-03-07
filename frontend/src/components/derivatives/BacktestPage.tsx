import { useState, useRef, useEffect, useCallback } from 'react'
import { createChart, CandlestickSeries, LineSeries, type IChartApi, type ISeriesApi, type SeriesMarker, type CandlestickData, type Time } from 'lightweight-charts'
import { useBacktest, type BacktestAlert } from '../../hooks/useBacktest'

type Range = '1W' | '1M' | '3M'

const TIER_COLORS: Record<string, string> = {
  S: '#ef4444',
  A: '#f97316',
  B: '#eab308',
  C: '#22c55e',
}

function computeEma(closes: number[], period: number): (number | null)[] {
  const result: (number | null)[] = []
  if (closes.length < period) return closes.map(() => null)
  const k = 2 / (period + 1)
  let ema = 0
  for (let i = 0; i < period; i++) {
    ema += closes[i]
    result.push(null)
  }
  ema /= period
  result[period - 1] = ema
  for (let i = period; i < closes.length; i++) {
    ema = closes[i] * k + ema * (1 - k)
    result.push(ema)
  }
  return result
}

function fmtReturn(v: number | null): string {
  if (v == null) return '-'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

function fmtPrice(v: number): string {
  if (v >= 100) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (v >= 1) return v.toFixed(2)
  return v.toPrecision(4)
}

export default function BacktestPage({ symbol }: { symbol: string | null }) {
  const [range, setRange] = useState<Range>('1M')
  const { data, isLoading } = useBacktest(symbol, range)
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  const scrollToAlert = useCallback((alert: BacktestAlert) => {
    const chart = chartRef.current
    const series = candleSeriesRef.current
    if (!chart || !series) return

    // Add entry price line
    series.createPriceLine({
      price: alert.entry_price,
      color: alert.direction === 'long' ? '#22c55e' : '#ef4444',
      lineWidth: 1,
      lineStyle: 2, // dashed
      axisLabelVisible: true,
      title: `Entry ${fmtPrice(alert.entry_price)}`,
    })

    // Scroll to alert time
    chart.timeScale().scrollToPosition(-5, false)
    const timeRange = chart.timeScale().getVisibleRange()
    if (timeRange) {
      const alertTime = alert.time as Time
      chart.timeScale().setVisibleRange({
        from: (alert.time - 86400 * 3) as Time,
        to: (alert.time + 86400 * 5) as Time,
      })
      void alertTime // suppress unused
    }
  }, [])

  // Chart setup
  useEffect(() => {
    if (!containerRef.current || !data?.candles.length) return

    const container = containerRef.current

    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: {
        background: { color: '#0c0c0c' },
        textColor: '#555',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: '#1a1a1a' },
        horzLines: { color: '#1a1a1a' },
      },
      crosshair: {
        vertLine: { color: '#333', labelBackgroundColor: '#222' },
        horzLine: { color: '#333', labelBackgroundColor: '#222' },
      },
      timeScale: {
        borderColor: '#1a1a1a',
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: '#1a1a1a',
      },
    })
    chartRef.current = chart

    // Candlestick series
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    })
    candleSeries.setData(data.candles as CandlestickData<Time>[])
    candleSeriesRef.current = candleSeries

    // EMA lines
    const closes = data.candles.map((c) => c.close)
    const times = data.candles.map((c) => c.time)

    const emaConfigs = [
      { period: 21, color: '#eab308' },
      { period: 50, color: '#3b82f6' },
      { period: 200, color: '#a855f7' },
    ]

    for (const { period, color } of emaConfigs) {
      const emaValues = computeEma(closes, period)
      const emaData = times
        .map((t, i) => (emaValues[i] != null ? { time: t as Time, value: emaValues[i]! } : null))
        .filter(Boolean) as { time: Time; value: number }[]

      if (emaData.length > 0) {
        const emaSeries = chart.addSeries(LineSeries, {
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        })
        emaSeries.setData(emaData)
      }
    }

    // S/R levels from structure
    if (data.structure?.key_levels) {
      for (const level of data.structure.key_levels.slice(0, 8)) {
        const label = level.type === 'support'
          ? `S (${level.touches}t)`
          : `R (${level.touches}t)`
        candleSeries.createPriceLine({
          price: level.price,
          color: level.type === 'support' ? '#22c55e44' : '#ef444444',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: label,
        })
      }
    }

    // Alert markers
    if (data.alerts.length > 0) {
      const markers: SeriesMarker<Time>[] = data.alerts.map((a) => ({
        time: a.time as Time,
        position: a.direction === 'long' ? 'belowBar' as const : 'aboveBar' as const,
        color: TIER_COLORS[a.tier] || '#888',
        shape: a.direction === 'long' ? 'arrowUp' as const : 'arrowDown' as const,
        text: `${a.tier} ${a.type}`,
      }))
      candleSeries.setMarkers(markers)
    }

    chart.timeScale().fitContent()

    // Resize observer
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        chart.applyOptions({ width, height })
      }
    })
    ro.observe(container)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
    }
  }, [data])

  if (!symbol) {
    return <div className="flex items-center justify-center h-full text-[#555] text-xs">Select a symbol</div>
  }

  return (
    <div className="h-full flex flex-col">
      {/* Controls */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-[#0a0a0a] border-b border-[#1a1a1a] flex-shrink-0">
        <span className="text-[10px] text-[#555] mr-1">Range:</span>
        {(['1W', '1M', '3M'] as Range[]).map((r) => (
          <button
            key={r}
            onClick={() => setRange(r)}
            className={`px-2 py-0.5 text-[10px] font-mono rounded transition-colors ${
              range === r
                ? 'bg-[#1a1a1a] text-text-primary'
                : 'text-[#555] hover:text-text-secondary'
            }`}
          >
            {r}
          </button>
        ))}
        {data?.structure && (
          <span className={`ml-auto text-[10px] font-mono ${
            data.structure.trend === 'up' ? 'text-green' : data.structure.trend === 'down' ? 'text-red' : 'text-[#555]'
          }`}>
            Trend: {data.structure.trend}
          </span>
        )}
      </div>

      {/* Chart */}
      <div ref={containerRef} className="flex-1 min-h-0">
        {isLoading && (
          <div className="flex items-center justify-center h-full text-[#555] text-xs">Loading candles...</div>
        )}
      </div>

      {/* Alert table */}
      {data?.alerts && data.alerts.length > 0 && (
        <div className="flex-shrink-0 max-h-[200px] overflow-auto border-t border-[#1a1a1a]">
          <table className="w-full text-[10px] font-mono">
            <thead className="sticky top-0 bg-[#0a0a0a]">
              <tr className="text-[#555]">
                <th className="text-left px-2 py-1">Date</th>
                <th className="text-left px-2 py-1">Type</th>
                <th className="text-left px-2 py-1">Tier</th>
                <th className="text-right px-2 py-1">Entry</th>
                <th className="text-left px-2 py-1">Dir</th>
                <th className="text-right px-2 py-1">1D</th>
                <th className="text-right px-2 py-1">3D</th>
                <th className="text-right px-2 py-1">7D</th>
                <th className="text-center px-2 py-1">Result</th>
              </tr>
            </thead>
            <tbody>
              {data.alerts.map((a, i) => {
                const won = a.direction === 'long'
                  ? (a.return_7d ?? a.return_3d ?? a.return_1d ?? 0) > 0
                  : (a.return_7d ?? a.return_3d ?? a.return_1d ?? 0) < 0
                return (
                  <tr
                    key={i}
                    onClick={() => scrollToAlert(a)}
                    className="hover:bg-[#111] cursor-pointer border-t border-[#111]"
                  >
                    <td className="px-2 py-1 text-text-secondary">{a.fired_at.slice(0, 16)}</td>
                    <td className="px-2 py-1 text-text-primary">{a.type}</td>
                    <td className="px-2 py-1" style={{ color: TIER_COLORS[a.tier] || '#888' }}>{a.tier}</td>
                    <td className="px-2 py-1 text-right text-text-primary">{fmtPrice(a.entry_price)}</td>
                    <td className={`px-2 py-1 ${a.direction === 'long' ? 'text-green' : 'text-red'}`}>
                      {a.direction || '-'}
                    </td>
                    <td className={`px-2 py-1 text-right ${(a.return_1d ?? 0) >= 0 ? 'text-green' : 'text-red'}`}>
                      {fmtReturn(a.return_1d)}
                    </td>
                    <td className={`px-2 py-1 text-right ${(a.return_3d ?? 0) >= 0 ? 'text-green' : 'text-red'}`}>
                      {fmtReturn(a.return_3d)}
                    </td>
                    <td className={`px-2 py-1 text-right ${(a.return_7d ?? 0) >= 0 ? 'text-green' : 'text-red'}`}>
                      {fmtReturn(a.return_7d)}
                    </td>
                    <td className="px-2 py-1 text-center">{won ? '\u2705' : '\u274C'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
