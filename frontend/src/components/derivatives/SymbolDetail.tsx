import { useMemo, useState, type ReactNode } from 'react'
import { useDerivativesDetail } from '../../hooks/useDerivativesDetail'
import MetricChart from './MetricChart'
import ZScoreChart from './ZScoreChart'
import CompositeRegimeChart from './CompositeRegimeChart'
import ZScatterCard from './ZScatterCard'
import ExpandedChartModal from './ExpandedChartModal'
import LiquidationMap from './LiquidationMap'

/* ── Helpers ────────────────────────────────────────────── */

function fmtUsd(v: number): string {
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

function zColorHex(z: number): string {
  if (z >= 2) return '#ef4444'
  if (z >= 1) return '#eab308'
  if (z <= -2) return '#22c55e'
  if (z <= -1) return '#60a5fa'
  return '#888'
}

/* ── ChartCard wrapper (TR style) ──────────────────────── */

interface ChartCardProps {
  title: string
  footer: ReactNode
  children: ReactNode
  onExpand?: () => void
}

function ChartCard({ title, footer, children, onExpand }: ChartCardProps) {
  return (
    <div className="bg-[#0c0c0c] border border-[#1a1a1a] rounded flex flex-col min-h-0">
      <div className="flex items-center justify-between px-3 pt-2.5 pb-1 flex-shrink-0">
        <span className="text-[11px] text-text-primary font-medium">{title}</span>
        {onExpand && (
          <svg
            onClick={onExpand}
            className="w-3.5 h-3.5 text-[#444] hover:text-text-primary cursor-pointer transition-colors"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"
            />
          </svg>
        )}
      </div>
      <div className="flex-1 min-h-0 px-1">{children}</div>
      <div className="px-3 pb-2 pt-1 flex-shrink-0">
        <div className="flex items-center gap-3 text-[10px] font-mono">{footer}</div>
      </div>
    </div>
  )
}

/* ── Colored value ─────────────────────────────────────── */

function CV({ value, fmt }: { value: number; fmt: (v: number) => string }) {
  return (
    <span style={{ color: value >= 0 ? '#2dd4bf' : '#ef4444' }}>{fmt(value)}</span>
  )
}

/* ── Expanded chart config ─────────────────────────────── */

interface ExpandConfig {
  title: string
  metricKey: string
  color: string
  formatY?: (v: number) => string
}

/* ── Main component ────────────────────────────────────── */

interface SymbolDetailProps {
  symbol: string | null
}

export default function SymbolDetail({ symbol }: SymbolDetailProps) {
  const { data, isLoading } = useDerivativesDetail(symbol)
  const [expanded, setExpanded] = useState<ExpandConfig | null>(null)

  const chartData = useMemo(() => {
    if (!data?.history?.length) return []
    return data.history.map((h) => ({
      date: h.date,
      price: h.price,
      oi: h.oi,
      funding: h.funding * 100,
      liq_delta: h.liq_delta,
      volume: h.volume,
      oi_zscore: h.oi_zscore,
      funding_zscore: h.funding_zscore,
      liq_zscore: h.liq_zscore,
      composite: (h.oi_zscore + h.funding_zscore + h.liq_zscore) / 3,
    }))
  }, [data])

  if (!symbol) {
    return (
      <div className="h-full flex items-center justify-center text-text-secondary text-xs">
        Select a symbol from the screener below
      </div>
    )
  }

  if (isLoading) {
    return <div className="text-text-secondary text-xs p-3">Loading {symbol}...</div>
  }

  if (!data) {
    return <div className="text-text-secondary text-xs p-3">No data for {symbol}</div>
  }

  const l = data.latest
  const sym = symbol.replace('USDT', '')
  const fundingAnn = l.funding_rate * 3 * 365 * 100
  const compositeZ = (l.oi_zscore + l.funding_zscore + l.liq_zscore) / 3
  const compositePct = (l.oi_percentile + l.funding_percentile + l.liq_percentile) / 3

  return (
    <>
      <div className="h-full overflow-y-auto p-2 space-y-2">
        {/* ── Composite Regime ─────────────────────────── */}
        <CompositeRegimeChart
          data={chartData}
          symbol={sym}
          currentComposite={compositeZ}
          currentPercentile={compositePct}
        />

        {/* ── Perpetuals Data ──────────────────────────── */}
        <div className="text-[11px] text-text-secondary uppercase tracking-wider font-medium px-1">
          Perpetuals Data
        </div>

        {/* Row 1: Raw metrics */}
        <div className="grid grid-cols-3 gap-2 h-[200px]">
          <ChartCard
            title="Open Interest"
            onExpand={() =>
              setExpanded({ title: `${sym} Open Interest`, metricKey: 'oi', color: '#5ba3ad' })
            }
            footer={
              <>
                <span className="text-text-secondary">Current:</span>
                <span className="text-text-primary">{fmtUsd(l.open_interest_usd)}</span>
                <span className="text-text-secondary">24h:</span>
                <CV value={l.oi_change_24h_pct} fmt={(v) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%`} />
              </>
            }
          >
            <MetricChart
              data={chartData}
              dataKey="oi"
              chartType="area"
              color="#5ba3ad"
              formatValue={(v) => fmtUsd(v)}
              label="Open Interest"
            />
          </ChartCard>

          <ChartCard
            title="Funding Rate"
            onExpand={() =>
              setExpanded({
                title: `${sym} Funding Rate`,
                metricKey: 'funding',
                color: '#2dd4bf',
                formatY: (v) => `${v.toFixed(3)}%`,
              })
            }
            footer={
              <>
                <span className="text-text-secondary">Current:</span>
                <CV value={l.funding_rate * 100} fmt={(v) => `${v.toFixed(4)}%`} />
                <span className="text-text-secondary">Ann:</span>
                <CV value={fundingAnn} fmt={(v) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%`} />
              </>
            }
          >
            <MetricChart
              data={chartData}
              dataKey="funding"
              chartType="bar"
              color="#2dd4bf"
              formatValue={(v) => `${v.toFixed(4)}%`}
              formatY={(v) => `${v.toFixed(3)}%`}
              label="Funding Rate"
            />
          </ChartCard>

          <ChartCard
            title="Liquidations Delta"
            onExpand={() =>
              setExpanded({ title: `${sym} Liquidations Delta`, metricKey: 'liq_delta', color: '#ef4444' })
            }
            footer={
              <>
                <span className="text-text-secondary">Delta:</span>
                <CV
                  value={l.liquidations_delta}
                  fmt={(v) => `${v >= 0 ? '' : '-'}${fmtUsd(Math.abs(v))}`}
                />
              </>
            }
          >
            <MetricChart
              data={chartData}
              dataKey="liq_delta"
              chartType="bar"
              color="#ef4444"
              formatValue={(v) => fmtUsd(v)}
              barSize={2}
              label="Liq Delta"
            />
          </ChartCard>
        </div>

        {/* Row 2: Volume */}
        <div className="grid grid-cols-3 gap-2 h-[200px]">
          <ChartCard
            title="Volume"
            onExpand={() =>
              setExpanded({ title: `${sym} Volume`, metricKey: 'volume', color: '#6366f1' })
            }
            footer={
              <>
                <span className="text-text-secondary">Current:</span>
                <span className="text-text-primary">{fmtUsd(l.volume_usd)}</span>
              </>
            }
          >
            <MetricChart
              data={chartData}
              dataKey="volume"
              chartType="bar"
              color="#6366f1"
              formatValue={(v) => fmtUsd(v)}
              barSize={3}
              label="Volume"
            />
          </ChartCard>

          {/* Liq Long vs Short */}
          <ChartCard
            title="Liq Long"
            footer={
              <>
                <span className="text-text-secondary">Long:</span>
                <span className="text-[#22c55e]">{fmtUsd(l.liquidations_long)}</span>
              </>
            }
          >
            <MetricChart
              data={chartData}
              dataKey="liq_delta"
              chartType="bar"
              color="#22c55e"
              formatValue={(v) => fmtUsd(v)}
              barSize={2}
              label="Liq Long"
            />
          </ChartCard>

          <ChartCard
            title="Liq Short"
            footer={
              <>
                <span className="text-text-secondary">Short:</span>
                <span className="text-[#ef4444]">{fmtUsd(l.liquidations_short)}</span>
              </>
            }
          >
            <MetricChart
              data={chartData}
              dataKey="liq_delta"
              chartType="bar"
              color="#ef4444"
              formatValue={(v) => fmtUsd(Math.abs(v))}
              barSize={2}
              label="Liq Short"
            />
          </ChartCard>
        </div>

        {/* Row 3: Z-Scores */}
        <div className="grid grid-cols-3 gap-2 h-[200px]">
          <ChartCard
            title="OI Z-Score"
            onExpand={() =>
              setExpanded({
                title: `${sym} OI Z-Score`,
                metricKey: 'oi_zscore',
                color: '#5ba3ad',
                formatY: (v) => v.toFixed(1),
              })
            }
            footer={
              <>
                <span className="text-text-secondary">Z:</span>
                <span style={{ color: zColorHex(l.oi_zscore) }}>{l.oi_zscore.toFixed(2)}</span>
                <span className="text-text-secondary">Percentile:</span>
                <span className="text-text-primary">{l.oi_percentile.toFixed(0)}%</span>
              </>
            }
          >
            <ZScoreChart data={chartData} dataKey="oi_zscore" color="#5ba3ad" />
          </ChartCard>

          <ChartCard
            title="Funding Z-Score"
            onExpand={() =>
              setExpanded({
                title: `${sym} Funding Z-Score`,
                metricKey: 'funding_zscore',
                color: '#a78bfa',
                formatY: (v) => v.toFixed(1),
              })
            }
            footer={
              <>
                <span className="text-text-secondary">Z:</span>
                <span style={{ color: zColorHex(l.funding_zscore) }}>
                  {l.funding_zscore.toFixed(2)}
                </span>
                <span className="text-text-secondary">Percentile:</span>
                <span className="text-text-primary">{l.funding_percentile.toFixed(0)}%</span>
              </>
            }
          >
            <ZScoreChart data={chartData} dataKey="funding_zscore" color="#a78bfa" />
          </ChartCard>

          <ChartCard
            title="Liquidations Z-Score"
            onExpand={() =>
              setExpanded({
                title: `${sym} Liq Z-Score`,
                metricKey: 'liq_zscore',
                color: '#f97316',
                formatY: (v) => v.toFixed(1),
              })
            }
            footer={
              <>
                <span className="text-text-secondary">Z:</span>
                <span style={{ color: zColorHex(l.liq_zscore) }}>{l.liq_zscore.toFixed(2)}</span>
                <span className="text-text-secondary">Percentile:</span>
                <span className="text-text-primary">{l.liq_percentile.toFixed(0)}%</span>
              </>
            }
          >
            <ZScoreChart data={chartData} dataKey="liq_zscore" color="#f97316" />
          </ChartCard>
        </div>

        {/* Row 4: Z vs Forward Return (scatter) */}
        <div className="grid grid-cols-3 gap-2 h-[240px]">
          <ZScatterCard
            title="OI Z vs Fwd Return"
            history={chartData}
            zKey="oi_zscore"
            currentZ={l.oi_zscore}
          />
          <ZScatterCard
            title="Funding Z vs Fwd Return"
            history={chartData}
            zKey="funding_zscore"
            currentZ={l.funding_zscore}
          />
          <ZScatterCard
            title="Liq Z vs Fwd Return"
            history={chartData}
            zKey="liq_zscore"
            currentZ={l.liq_zscore}
          />
        </div>

        {/* Row 5: Liquidation Map */}
        <div className="h-[300px]">
          <LiquidationMap symbol={symbol} />
        </div>
      </div>

      {/* ── Expanded modal ─────────────────────────────── */}
      {expanded && (
        <ExpandedChartModal
          title={expanded.title}
          onClose={() => setExpanded(null)}
          data={chartData}
          metricKey={expanded.metricKey}
          metricColor={expanded.color}
          metricFormatY={expanded.formatY}
        />
      )}
    </>
  )
}
