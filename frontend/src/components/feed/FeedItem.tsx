import { type FeedEvent, useFeedStore } from '../../store/feed'
import { getChainInfo } from '../../utils/chains'
import { timeAgo } from '../../utils/format'
import { useWatchlist } from '../../hooks/useWatchlist'

const EVENT_COLORS: Record<string, string> = {
  NEW_PAIR: 'text-blue',
  WHALE_TRANSFER: 'text-yellow',
  VOLUME_SPIKE: 'text-green',
  PRICE_PUMP: 'text-green',
  PRICE_DUMP: 'text-red',
  FUNDING_EXTREME: 'text-yellow',
  FUNDING_SPREAD: 'text-green',
  LIQUIDITY_CHANGE: 'text-yellow',
  PROTOCOL_TVL_SPIKE: 'text-blue',
  PROTOCOL_YIELD_NEW: 'text-green',
}

const EVENT_LABELS: Record<string, string> = {
  NEW_PAIR: 'NEW',
  WHALE_TRANSFER: 'WHALE',
  VOLUME_SPIKE: 'VOL',
  PRICE_PUMP: 'PUMP',
  PRICE_DUMP: 'DUMP',
  FUNDING_EXTREME: 'FUND',
  FUNDING_SPREAD: 'SPREAD',
  LIQUIDITY_CHANGE: 'LIQ',
  PROTOCOL_TVL_SPIKE: 'TVL',
  PROTOCOL_YIELD_NEW: 'YIELD',
}

function WhaleDetail({ details }: { details: Record<string, unknown> }) {
  const fromLabel = (details.from_label as string) || (details.from as string)?.slice(0, 8) + '...'
  const toLabel = (details.to_label as string) || (details.to as string)?.slice(0, 8) + '...'
  const amount = details.value_eth ?? details.amount_sol
  const unit = details.value_eth ? 'ETH' : 'SOL'
  const source = details.source as string
  return (
    <span className="truncate">
      <span className="text-text-primary">{Number(amount).toLocaleString()}</span>
      <span className="text-text-secondary"> {unit} </span>
      <span className="text-text-secondary">{fromLabel}</span>
      <span className="text-text-secondary"> → </span>
      <span className="text-text-primary">{toLabel}</span>
      {source && <span className="text-text-secondary/60 ml-1">[{source}]</span>}
    </span>
  )
}

function PriceDetail({ details }: { details: Record<string, unknown> }) {
  const pct = details.price_change_1h as number
  if (!pct) return null
  return (
    <span className="text-text-secondary ml-1">
      {pct > 0 ? '+' : ''}{pct.toFixed(1)}% 1h
    </span>
  )
}

function FundingDetail({ details }: { details: Record<string, unknown> }) {
  const rate = details.rate as number
  const exchange = details.exchange as string
  const direction = details.direction as string
  if (!rate) return null
  return (
    <span className="text-text-secondary ml-1">
      {(rate * 100).toFixed(3)}% ({exchange}) {direction}
    </span>
  )
}

function SpreadDetail({ details }: { details: Record<string, unknown> }) {
  const spread = details.spread as number
  const longEx = details.long_exchange as string
  const shortEx = details.short_exchange as string
  if (!spread) return null
  return (
    <span className="truncate">
      <span className="text-green">{longEx}</span>
      <span className="text-text-secondary"> → </span>
      <span className="text-red">{shortEx}</span>
      <span className="text-yellow ml-1">{(spread * 100).toFixed(3)}%</span>
    </span>
  )
}

function ProtocolTvlDetail({ details }: { details: Record<string, unknown> }) {
  const protocol = details.protocol as string
  const change = details.change_1d_pct as number
  const tvl = details.tvl as number
  const direction = details.direction as string
  return (
    <span className="truncate">
      <span className="text-text-primary">{protocol}</span>
      <span className={`ml-1 ${direction === 'up' ? 'text-green' : 'text-red'}`}>
        {change > 0 ? '+' : ''}{change?.toFixed(1)}%
      </span>
      {tvl && <span className="text-text-secondary ml-1">${(tvl / 1e6).toFixed(1)}M</span>}
    </span>
  )
}

function YieldDetail({ details }: { details: Record<string, unknown> }) {
  const protocol = details.protocol as string
  const symbol = details.symbol as string
  const apy = details.apy as number
  const tvl = details.tvl as number
  return (
    <span className="truncate">
      <span className="text-text-primary">{protocol}</span>
      <span className="text-text-secondary">/{symbol}</span>
      <span className="text-green ml-1">{apy?.toFixed(0)}% APY</span>
      {tvl && <span className="text-text-secondary ml-1">${(tvl / 1e3).toFixed(0)}k</span>}
    </span>
  )
}

interface Props {
  event: FeedEvent
}

export default function FeedItem({ event }: Props) {
  const { selectedEvent, selectEvent } = useFeedStore()
  const { add: addToWatchlist } = useWatchlist()
  const chain = getChainInfo(event.chain)
  const isSelected = selectedEvent?.id === event.id
  const canWatch = !!(event.token_address || event.pair_address)

  const handleAdd = (e: React.MouseEvent) => {
    e.stopPropagation()
    addToWatchlist.mutate({
      chain: event.chain,
      address: event.token_address || event.pair_address || '',
      symbol: event.token_symbol || undefined,
    })
  }

  function renderDetail() {
    switch (event.event_type) {
      case 'WHALE_TRANSFER':
        return <WhaleDetail details={event.details} />
      case 'FUNDING_SPREAD':
        return <SpreadDetail details={event.details} />
      case 'PROTOCOL_TVL_SPIKE':
        return <ProtocolTvlDetail details={event.details} />
      case 'PROTOCOL_YIELD_NEW':
        return <YieldDetail details={event.details} />
      default: {
        const proto = event.details.protocol as string | undefined
        return (
          <>
            <span className="text-text-primary">
              {event.token_symbol || event.token_address?.slice(0, 10) || '???'}
            </span>
            {proto && (
              <span className="text-[9px] px-1 py-0 border border-border text-text-secondary/70 rounded-sm">{proto}</span>
            )}
            {(event.event_type === 'PRICE_PUMP' || event.event_type === 'PRICE_DUMP') && (
              <PriceDetail details={event.details} />
            )}
            {event.event_type === 'FUNDING_EXTREME' && <FundingDetail details={event.details} />}
          </>
        )
      }
    }
  }

  return (
    <div
      className={`group flex items-center gap-2 px-2 py-1.5 cursor-pointer border-b border-border hover:bg-bg-titlebar transition-colors ${
        isSelected ? 'bg-bg-titlebar' : ''
      }`}
      onClick={() => selectEvent(event)}
    >
      <span
        className="text-[10px] font-mono px-1 py-0.5 border border-border min-w-[36px] text-center"
        style={{ color: chain.color }}
      >
        {chain.label}
      </span>
      <span className={`text-[10px] font-mono min-w-[36px] text-center ${EVENT_COLORS[event.event_type] || 'text-text-secondary'}`}>
        {EVENT_LABELS[event.event_type] || event.event_type}
      </span>
      <span className="text-xs truncate flex-1 flex items-center gap-1">
        {renderDetail()}
      </span>
      {canWatch && (
        <button
          onClick={handleAdd}
          className="text-[10px] px-1 text-text-secondary hover:text-yellow opacity-0 group-hover:opacity-100 transition-opacity"
          title="Add to watchlist"
        >
          +
        </button>
      )}
      <span className="text-[10px] text-text-secondary font-mono whitespace-nowrap">
        {event.created_at ? timeAgo(event.created_at) : ''}
      </span>
    </div>
  )
}
