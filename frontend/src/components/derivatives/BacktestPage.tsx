import { useState, useRef, useEffect, useCallback } from 'react'
import { createChart, createSeriesMarkers, CandlestickSeries, LineSeries, type IChartApi, type ISeriesApi, type Time, type UTCTimestamp, type SeriesMarker } from 'lightweight-charts'
import { useBacktest, type BacktestAlert } from '../../hooks/useBacktest'

type Range = '1W' | '1M' | '3M' | '6M' | '1Y'
type Timeframe = '1d' | '4h' | 'mtf'

const TIER_COLORS: Record<string, string> = {
  TRIGGER: '#ef4444',
  SIGNAL: '#f97316',
  SETUP: '#eab308',
}

const TYPE_SHORT: Record<string, string> = {
  overheat: 'OVH',
  capitulation: 'CAP',
  divergence_squeeze: 'DIV\u2193',
  divergence_top: 'DIV\u2191',
  liq_flush: 'FLUSH',
  div_squeeze_1d: 'DS1',
  div_squeeze_3d: 'DS3',
  div_squeeze_5d: 'DS5',
  div_top_1d: 'DT1',
  div_top_3d: 'DT3',
  liq_flush_3d: 'FL3',
  vol_divergence: 'VD',
  liq_long_flush: 'L\u2193FL',
  liq_short_squeeze: 'S\u2191SQ',
  fund_reversal: 'F\u21BB',
  vol_expansion: 'V\u2197',
  oi_flush_vol: 'OI\u2193V',
  fund_spike: 'F\u2191',
  distribution: 'DIST',
  overextension: 'OVX',
  oi_buildup_stall: 'OI\u2197\u23F8',
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
  const [timeframe, setTimeframe] = useState<Timeframe>('1d')
  const { data, isLoading } = useBacktest(symbol, range, timeframe)
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const entryLineRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']> | null>(null)

  const scrollToAlert = useCallback((alert: BacktestAlert) => {
    const chart = chartRef.current
    const series = candleSeriesRef.current
    if (!chart || !series) return

    // Remove previous entry line
    if (entryLineRef.current) {
      series.removePriceLine(entryLineRef.current)
    }

    entryLineRef.current = series.createPriceLine({
      price: alert.entry_price,
      color: alert.direction === 'long' ? '#22c55e' : '#ef4444',
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: true,
      title: `Entry ${fmtPrice(alert.entry_price)}`,
    })

    chart.timeScale().setVisibleRange({
      from: ((alert.time - 86400 * 3) as UTCTimestamp) as Time,
      to: ((alert.time + 86400 * 5) as UTCTimestamp) as Time,
    })
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

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    })
    candleSeries.setData(data.candles.map((c) => ({
      time: c.time as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    })))
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
        .map((t, i) => (emaValues[i] != null ? { time: (t as UTCTimestamp) as Time, value: emaValues[i]! } : null))
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

    // S/R levels
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

    // Alert markers — different shapes for real vs simulated, diamonds for 4h
    if (data.alerts.length > 0) {
      const markers: SeriesMarker<Time>[] = data.alerts.map((a) => {
        const is4h = a.timeframe === '4h'
        const upgraded = a.tier_upgraded
        const prefix = a.simulated ? (is4h ? '4h ' : 'sim ') : ''
        const suffix = upgraded ? ' \u2B06' : ''
        return {
          time: (a.time as UTCTimestamp) as Time,
          position: a.direction === 'long' ? 'belowBar' as const : 'aboveBar' as const,
          color: a.simulated
            ? (TIER_COLORS[a.tier] || '#888') + (is4h ? '77' : '99')
            : TIER_COLORS[a.tier] || '#888',
          shape: is4h
            ? 'square' as const
            : a.direction === 'long' ? 'arrowUp' as const : 'arrowDown' as const,
          text: `${prefix}${TYPE_SHORT[a.type] || a.type}${suffix}`,
        }
      })
      createSeriesMarkers(candleSeries, markers)
    }

    chart.timeScale().fitContent()

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

  const stats = data?.stats

  return (
    <div className="h-full flex flex-col">
      {/* Controls + Stats */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-[#0a0a0a] border-b border-[#1a1a1a] flex-shrink-0">
        <span className="text-[10px] text-[#555] mr-1">Range:</span>
        {(['1W', '1M', '3M', '6M', '1Y'] as Range[]).map((r) => (
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

        <span className="text-[#333] mx-1">|</span>
        <span className="text-[10px] text-[#555] mr-1">TF:</span>
        {(['1d', '4h', 'mtf'] as Timeframe[]).map((tf) => (
          <button
            key={tf}
            onClick={() => setTimeframe(tf)}
            className={`px-2 py-0.5 text-[10px] font-mono rounded transition-colors ${
              timeframe === tf
                ? 'bg-[#1a1a1a] text-text-primary'
                : 'text-[#555] hover:text-text-secondary'
            }`}
          >
            {tf.toUpperCase()}
          </button>
        ))}

        {stats && stats.total_signals > 0 && (
          <div className="ml-auto flex items-center gap-3 text-[10px] font-mono">
            <span className="text-[#555]">
              Signals: <span className="text-text-primary">{stats.total_signals}</span>
              {stats.simulated_signals > 0 && (
                <span className="text-[#555]"> ({stats.real_signals} real + {stats.simulated_signals} sim)</span>
              )}
            </span>
            {stats.with_returns > 0 && (
              <>
                <span className="text-[#555]">WR:</span>
                <span className={stats.win_rate >= 50 ? 'text-green' : 'text-red'}>
                  {stats.win_rate}%
                </span>
                {stats.mfe_wr != null && (
                  <>
                    <span className="text-[#555]">MFE:</span>
                    <span className={stats.mfe_wr >= 60 ? 'text-green' : stats.mfe_wr >= 40 ? 'text-yellow' : 'text-red'}>
                      {stats.mfe_wr}%
                    </span>
                  </>
                )}
                <span className="text-[#555]">Avg:</span>
                <span className={stats.avg_return >= 0 ? 'text-green' : 'text-red'}>
                  {stats.avg_return > 0 ? '+' : ''}{stats.avg_return}%
                </span>
              </>
            )}
          </div>
        )}

        {data?.structure && !stats?.total_signals && (
          <span className={`ml-auto text-[10px] font-mono ${
            data.structure.trend === 'up' ? 'text-green' : data.structure.trend === 'down' ? 'text-red' : 'text-[#555]'
          }`}>
            Trend: {data.structure.trend}
          </span>
        )}
      </div>

      {/* Per-type stats */}
      {stats?.by_type && Object.keys(stats.by_type).length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 px-3 py-1 bg-[#0a0a0a] border-b border-[#1a1a1a] flex-shrink-0">
          {Object.entries(stats.by_type).sort((a, b) => b[1].count - a[1].count).map(([type, ts]) => (
            <span key={type} className="text-[9px] font-mono text-[#555]">
              <span className="text-text-secondary">{TYPE_SHORT[type] || type}</span>
              {' '}{ts.count}x{' '}
              <span className={ts.win_rate >= 50 ? 'text-green' : 'text-red'}>{ts.win_rate}%</span>
              {ts.mfe_wr != null && (
                <>{' MFE:'}<span className={ts.mfe_wr >= 60 ? 'text-green' : ts.mfe_wr >= 40 ? 'text-yellow' : 'text-red'}>{ts.mfe_wr}%</span></>
              )}
              {' '}
              <span className={ts.avg_return >= 0 ? 'text-green' : 'text-red'}>
                {ts.avg_return > 0 ? '+' : ''}{ts.avg_return}%
              </span>
              {' PF:'}
              <span className={ts.pf >= 1 ? 'text-green' : 'text-red'}>{ts.pf}</span>
            </span>
          ))}
        </div>
      )}

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
                <th className="text-left px-2 py-1">Conf</th>
                <th className="text-right px-2 py-1">Entry</th>
                <th className="text-left px-2 py-1">Dir</th>
                <th className="text-right px-2 py-1">1D</th>
                <th className="text-right px-2 py-1">3D</th>
                <th className="text-right px-2 py-1">7D</th>
                <th className="text-right px-2 py-1">MFE</th>
                <th className="text-center px-2 py-1">Result</th>
                <th className="text-center px-2 py-1">Src</th>
              </tr>
            </thead>
            <tbody>
              {data.alerts.map((a, i) => {
                const rawRet = a.return_7d ?? a.return_3d ?? a.return_1d ?? 0
                const bestReturn = a.direction === 'long' ? rawRet : -rawRet
                const hasReturn = a.return_7d != null || a.return_3d != null || a.return_1d != null
                const won = hasReturn && bestReturn > 0
                const mfeHit = (a.mfe_return ?? 0) >= 3
                return (
                  <tr
                    key={i}
                    onClick={() => scrollToAlert(a)}
                    className={`hover:bg-[#111] cursor-pointer border-t border-[#111] ${a.simulated ? 'opacity-80' : ''}`}
                  >
                    <td className="px-2 py-1 text-text-secondary">{a.fired_at.slice(0, 10)}</td>
                    <td className="px-2 py-1 text-text-primary">{a.type}</td>
                    <td className="px-2 py-1" style={{ color: TIER_COLORS[a.tier] || '#888' }}>
                      {a.tier}
                      {a.tier_upgraded && <span title={`Upgraded from ${a.original_tier}`}> ⬆</span>}
                    </td>
                    <td className="px-2 py-1 text-text-secondary">{a.confluence}/10</td>
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
                    <td className={`px-2 py-1 text-right ${(a.mfe_return ?? 0) >= 0 ? 'text-green' : 'text-[#555]'}`}>
                      {a.mfe_return != null ? `+${a.mfe_return.toFixed(1)}%` : '-'}
                    </td>
                    <td className="px-2 py-1 text-center">{hasReturn ? (won ? '\u2705' : mfeHit ? '\u25C6' : '\u274C') : '-'}</td>
                    <td className="px-2 py-1 text-center flex gap-0.5 justify-center">
                      <span className={`text-[8px] px-1 py-0.5 rounded ${
                        a.simulated ? 'bg-[#1a1a1a] text-[#888]' : 'bg-[#22c55e22] text-green'
                      }`}>
                        {a.simulated ? 'SIM' : 'LIVE'}
                      </span>
                      {a.timeframe === '4h' && (
                        <span className="text-[8px] px-1 py-0.5 rounded bg-[#06b6d422] text-[#06b6d4]">4H</span>
                      )}
                    </td>
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
