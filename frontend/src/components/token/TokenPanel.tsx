import Panel from '../layout/Panel'
import TokenMetrics from './TokenMetrics'
import SecurityScore from './SecurityScore'
import ClaudeAnalysis from './ClaudeAnalysis'
import { useFeedStore, type FeedEvent } from '../../store/feed'
import { useTokenAnalysis } from '../../hooks/useTokenAnalysis'
import { useSecurity } from '../../hooks/useSecurity'
import { useWatchlist } from '../../hooks/useWatchlist'
import { truncateAddress } from '../../utils/format'
import { getChainInfo, getExplorerUrl } from '../../utils/chains'

/** Events that have no token_address and need custom detail rendering */
const NON_TOKEN_EVENTS = new Set([
  'FUNDING_EXTREME', 'FUNDING_SPREAD',
  'PROTOCOL_TVL_SPIKE', 'PROTOCOL_YIELD_NEW',
])

function FundingExtremeDetail({ event }: { event: FeedEvent }) {
  const d = event.details
  return (
    <div className="flex flex-col gap-2 text-xs font-mono">
      <div className="grid grid-cols-2 gap-2">
        <div><span className="text-text-secondary">Exchange:</span> <span className="text-text-primary">{d.exchange as string}</span></div>
        <div><span className="text-text-secondary">Direction:</span> <span className={d.rate as number > 0 ? 'text-green' : 'text-red'}>{d.direction as string}</span></div>
        <div><span className="text-text-secondary">Rate (8h):</span> <span className="text-yellow">{((d.rate as number) * 100).toFixed(4)}%</span></div>
        <div><span className="text-text-secondary">APR:</span> <span className="text-text-primary">{((d.apr as number) * 100).toFixed(1)}%</span></div>
      </div>
      <div className="text-[10px] text-text-secondary">
        Extreme funding rate — {d.rate as number > 0 ? 'longs paying shorts' : 'shorts paying longs'}.
        Consider {d.rate as number > 0 ? 'shorting' : 'longing'} to collect funding.
      </div>
    </div>
  )
}

function FundingSpreadDetail({ event }: { event: FeedEvent }) {
  const d = event.details
  return (
    <div className="flex flex-col gap-2 text-xs font-mono">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <span className="text-text-secondary">Long @</span>{' '}
          <span className="text-green">{d.long_exchange as string}</span>{' '}
          <span className="text-text-primary">{((d.long_rate as number) * 100).toFixed(4)}%</span>
        </div>
        <div>
          <span className="text-text-secondary">Short @</span>{' '}
          <span className="text-red">{d.short_exchange as string}</span>{' '}
          <span className="text-text-primary">{((d.short_rate as number) * 100).toFixed(4)}%</span>
        </div>
        <div><span className="text-text-secondary">Spread:</span> <span className="text-yellow">{((d.spread as number) * 100).toFixed(4)}%</span></div>
        <div><span className="text-text-secondary">Est daily:</span> <span className="text-text-primary">{((d.est_daily_pct as number) * 100).toFixed(3)}%</span></div>
      </div>
      <div className="text-[10px] text-text-secondary">
        Funding rate arbitrage: go long on {d.long_exchange as string} (receive funding) and short on {d.short_exchange as string} (pay less).
      </div>
    </div>
  )
}

function ProtocolTvlDetail({ event }: { event: FeedEvent }) {
  const d = event.details
  const tvl = d.tvl as number
  const change = d.change_1d_pct as number
  const chains = d.chains as string[] | undefined
  return (
    <div className="flex flex-col gap-2 text-xs font-mono">
      <div className="grid grid-cols-2 gap-2">
        <div><span className="text-text-secondary">Protocol:</span> <span className="text-text-primary">{d.protocol as string}</span></div>
        <div><span className="text-text-secondary">Category:</span> <span className="text-text-primary">{d.category as string || '—'}</span></div>
        <div><span className="text-text-secondary">TVL:</span> <span className="text-text-primary">${(tvl / 1e6).toFixed(1)}M</span></div>
        <div>
          <span className="text-text-secondary">Change 24h:</span>{' '}
          <span className={change > 0 ? 'text-green' : 'text-red'}>{change > 0 ? '+' : ''}{change?.toFixed(1)}%</span>
        </div>
      </div>
      {chains && chains.length > 0 && (
        <div className="flex items-center gap-1">
          <span className="text-text-secondary text-[10px]">Chains:</span>
          {chains.map((c) => (
            <span key={c} className="text-[10px] px-1 py-0.5 border border-border" style={{ color: getChainInfo(c.toLowerCase()).color }}>
              {getChainInfo(c.toLowerCase()).label}
            </span>
          ))}
        </div>
      )}
      {d.url && (
        <a href={d.url as string} target="_blank" rel="noopener noreferrer" className="text-[10px] text-blue hover:underline">
          View on DefiLlama
        </a>
      )}
    </div>
  )
}

function YieldDetail({ event }: { event: FeedEvent }) {
  const d = event.details
  return (
    <div className="flex flex-col gap-2 text-xs font-mono">
      <div className="grid grid-cols-2 gap-2">
        <div><span className="text-text-secondary">Protocol:</span> <span className="text-text-primary">{d.protocol as string}</span></div>
        <div><span className="text-text-secondary">Pool:</span> <span className="text-text-primary">{d.symbol as string}</span></div>
        <div><span className="text-text-secondary">APY:</span> <span className="text-green">{(d.apy as number)?.toFixed(1)}%</span></div>
        <div><span className="text-text-secondary">TVL:</span> <span className="text-text-primary">${((d.tvl as number) / 1e3).toFixed(0)}k</span></div>
        <div><span className="text-text-secondary">APY +1d:</span> <span className="text-green">+{(d.apy_change_1d as number)?.toFixed(0)}%</span></div>
        <div><span className="text-text-secondary">Chain:</span> <span className="text-text-primary">{d.chain as string}</span></div>
      </div>
    </div>
  )
}

export default function TokenPanel() {
  const { selectedEvent } = useFeedStore()
  const chain = selectedEvent?.chain ?? null
  const address = selectedEvent?.token_address ?? null
  const pairAddress = selectedEvent?.pair_address ?? null

  const isNonTokenEvent = selectedEvent && NON_TOKEN_EVENTS.has(selectedEvent.event_type)

  const { data: result, isLoading, error } = useTokenAnalysis(
    isNonTokenEvent ? null : chain,
    isNonTokenEvent ? null : address,
    isNonTokenEvent ? null : pairAddress,
  )
  const { data: securityResult } = useSecurity(
    isNonTokenEvent ? null : chain,
    isNonTokenEvent ? null : address,
  )
  const { add: addToWatchlist } = useWatchlist()

  if (!selectedEvent) {
    return (
      <Panel title="Token Analysis">
        <div className="text-text-secondary text-xs">Select a token from the feed</div>
      </Panel>
    )
  }

  // Non-token events: render event-specific detail panel
  if (isNonTokenEvent) {
    return (
      <Panel title="Event Details">
        <div className="flex flex-col gap-3">
          {/* Header */}
          <div className="flex items-center gap-2">
            <span
              className="text-[10px] font-mono px-1 py-0.5 border border-border"
              style={{ color: getChainInfo(selectedEvent.chain).color }}
            >
              {getChainInfo(selectedEvent.chain).label}
            </span>
            <span className="text-sm font-medium">{selectedEvent.token_symbol || selectedEvent.event_type}</span>
            <span className="text-[10px] text-text-secondary font-mono uppercase">{selectedEvent.event_type}</span>
          </div>

          {/* Event-specific content */}
          {selectedEvent.event_type === 'FUNDING_EXTREME' && <FundingExtremeDetail event={selectedEvent} />}
          {selectedEvent.event_type === 'FUNDING_SPREAD' && <FundingSpreadDetail event={selectedEvent} />}
          {selectedEvent.event_type === 'PROTOCOL_TVL_SPIKE' && <ProtocolTvlDetail event={selectedEvent} />}
          {selectedEvent.event_type === 'PROTOCOL_YIELD_NEW' && <YieldDetail event={selectedEvent} />}
        </div>
      </Panel>
    )
  }

  // Regular token events
  const tokenData = result?.data ?? {}
  const baseToken = tokenData.base_token as Record<string, string> | undefined
  const symbol = baseToken?.symbol || selectedEvent.token_symbol || '???'
  const name = baseToken?.name || ''

  return (
    <Panel title="Token Analysis">
      <div className="flex flex-col gap-3">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span
              className="text-[10px] font-mono px-1 py-0.5 border border-border"
              style={{ color: getChainInfo(selectedEvent.chain).color }}
            >
              {getChainInfo(selectedEvent.chain).label}
            </span>
            <span className="text-sm font-medium">{symbol}</span>
            {name && <span className="text-xs text-text-secondary">{name}</span>}
          </div>
          <div className="flex items-center gap-2">
            {address && (
              <a
                href={getExplorerUrl(selectedEvent.chain, address, 'token')}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[10px] font-mono text-text-secondary hover:text-blue transition-colors"
              >
                {truncateAddress(address)}
              </a>
            )}
            {address && (
              <button
                onClick={() => addToWatchlist.mutate({ chain: selectedEvent.chain, address, symbol })}
                className="text-[10px] px-1.5 py-0.5 border border-border text-text-secondary hover:text-yellow hover:border-yellow/30 transition-colors"
              >
                +Watch
              </button>
            )}
          </div>
        </div>

        {/* Event details line for context */}
        {selectedEvent.event_type === 'NEW_PAIR' && selectedEvent.details.source && (
          <div className="text-[10px] text-text-secondary font-mono">
            Source: {selectedEvent.details.source as string}
            {selectedEvent.details.description && <> — {(selectedEvent.details.description as string).slice(0, 100)}</>}
          </div>
        )}
        {(selectedEvent.event_type === 'PRICE_PUMP' || selectedEvent.event_type === 'PRICE_DUMP') && (
          <div className="text-[10px] font-mono">
            <span className={selectedEvent.event_type === 'PRICE_PUMP' ? 'text-green' : 'text-red'}>
              {(selectedEvent.details.price_change_1h as number) > 0 ? '+' : ''}
              {(selectedEvent.details.price_change_1h as number)?.toFixed(1)}% 1h
            </span>
            {selectedEvent.details.liquidity_usd && (
              <span className="text-text-secondary ml-2">Liq: ${Number(selectedEvent.details.liquidity_usd).toLocaleString()}</span>
            )}
          </div>
        )}
        {selectedEvent.event_type === 'WHALE_TRANSFER' && (
          <div className="text-[10px] font-mono text-text-secondary">
            {selectedEvent.details.from_label || (selectedEvent.details.from as string)?.slice(0, 12)}
            {' → '}
            {selectedEvent.details.to_label || (selectedEvent.details.to as string)?.slice(0, 12)}
            {selectedEvent.details.tx_hash && (
              <a
                href={getExplorerUrl(selectedEvent.chain, selectedEvent.details.tx_hash as string, 'address').replace('/address/', '/tx/')}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue hover:underline ml-2"
              >
                [tx]
              </a>
            )}
          </div>
        )}

        {/* Metrics */}
        {isLoading && (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-3 bg-border/30 animate-pulse" style={{ width: `${80 - i * 15}%` }} />
            ))}
          </div>
        )}
        {error && <div className="text-red text-xs">Failed to load token data</div>}
        {!isLoading && tokenData.price_usd && <TokenMetrics data={tokenData} />}
        {!isLoading && !tokenData.price_usd && !error && address && (
          <div className="text-text-secondary text-xs">No pair data available</div>
        )}

        {/* Security */}
        {securityResult && (
          <>
            <div className="border-t border-border my-1" />
            <SecurityScore
              goplus={securityResult.goplus}
              honeypot={securityResult.honeypot}
              rugcheck={securityResult.rugcheck}
              chain={selectedEvent.chain}
            />
          </>
        )}

        {/* Claude Analysis */}
        {address && (
          <>
            <div className="border-t border-border my-1" />
            <ClaudeAnalysis chain={selectedEvent.chain} address={address} />
          </>
        )}

        {/* DexScreener link */}
        {tokenData.url && (
          <a
            href={tokenData.url as string}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-text-secondary hover:text-blue transition-colors"
          >
            View on DexScreener
          </a>
        )}
      </div>
    </Panel>
  )
}
