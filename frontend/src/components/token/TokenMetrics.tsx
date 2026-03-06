import { formatUsd, formatPercent } from '../../utils/format'

interface Props {
  data: Record<string, unknown>
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] text-text-secondary uppercase">{label}</span>
      <span className={`text-xs font-mono ${color || 'text-text-primary'}`}>{value}</span>
    </div>
  )
}

export default function TokenMetrics({ data }: Props) {
  const price = data.price_usd ? formatUsd(Number(data.price_usd)) : '—'
  const liq = (data.liquidity as Record<string, unknown>)?.usd
    ? formatUsd(Number((data.liquidity as Record<string, unknown>).usd))
    : '—'
  const vol24 = (data.volume as Record<string, unknown>)?.h24
    ? formatUsd(Number((data.volume as Record<string, unknown>).h24))
    : '—'
  const fdv = data.fdv ? formatUsd(Number(data.fdv)) : '—'
  const mc = data.market_cap ? formatUsd(Number(data.market_cap)) : '—'

  const pc = data.price_change as Record<string, unknown> | undefined
  const change5m = pc?.m5 != null ? Number(pc.m5) : null
  const change1h = pc?.h1 != null ? Number(pc.h1) : null
  const change24h = pc?.h24 != null ? Number(pc.h24) : null

  const txns = data.txns as Record<string, Record<string, number>> | undefined
  const buys24 = txns?.h24?.buys ?? 0
  const sells24 = txns?.h24?.sells ?? 0

  return (
    <div className="grid grid-cols-3 gap-3">
      <Metric label="Price" value={price} />
      <Metric label="Liquidity" value={liq} />
      <Metric label="Vol 24h" value={vol24} />
      <Metric label="FDV" value={fdv} />
      <Metric label="MCap" value={mc} />
      <Metric label="Pairs" value={String(data.all_pairs_count ?? '—')} />
      {change5m != null && (
        <Metric label="5m" value={formatPercent(change5m)} color={change5m >= 0 ? 'text-green' : 'text-red'} />
      )}
      {change1h != null && (
        <Metric label="1h" value={formatPercent(change1h)} color={change1h >= 0 ? 'text-green' : 'text-red'} />
      )}
      {change24h != null && (
        <Metric label="24h" value={formatPercent(change24h)} color={change24h >= 0 ? 'text-green' : 'text-red'} />
      )}
      <Metric label="Buys 24h" value={String(buys24)} color="text-green" />
      <Metric label="Sells 24h" value={String(sells24)} color="text-red" />
      <Metric label="DEX" value={String(data.dex ?? '—')} />
    </div>
  )
}
