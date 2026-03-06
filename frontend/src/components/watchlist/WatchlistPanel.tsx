import { useState } from 'react'
import Panel from '../layout/Panel'
import { useWatchlist } from '../../hooks/useWatchlist'
import { useFeedStore } from '../../store/feed'
import { getChainInfo, CHAINS } from '../../utils/chains'
import { formatUsd, formatPercent, truncateAddress } from '../../utils/format'

const CHAIN_OPTIONS = Object.keys(CHAINS).filter((c) => c !== 'perp')

export default function WatchlistPanel() {
  const { list, prices, add, remove } = useWatchlist()
  const { selectEvent, selectedEvent } = useFeedStore()
  const [addChain, setAddChain] = useState('ethereum')
  const [addAddress, setAddAddress] = useState('')

  const items = list.data ?? []
  const priceData = prices.data ?? {}

  const handleClick = (item: { chain: string; address: string; symbol: string | null }) => {
    selectEvent({
      id: 0,
      event_type: 'WATCHLIST',
      chain: item.chain,
      token_address: item.address,
      pair_address: null,
      token_symbol: item.symbol,
      details: {},
      severity: 'info',
      created_at: '',
    })
  }

  const handleAdd = () => {
    const addr = addAddress.trim()
    if (!addr) return
    add.mutate({ chain: addChain, address: addr })
    setAddAddress('')
  }

  return (
    <Panel title="Watchlist">
      {/* Manual add form */}
      <div className="flex items-center gap-1 mb-2">
        <select
          value={addChain}
          onChange={(e) => setAddChain(e.target.value)}
          className="text-[10px] bg-bg-primary border border-border text-text-primary px-1 py-0.5 font-mono"
        >
          {CHAIN_OPTIONS.map((c) => (
            <option key={c} value={c}>{getChainInfo(c).label}</option>
          ))}
        </select>
        <input
          type="text"
          value={addAddress}
          onChange={(e) => setAddAddress(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
          placeholder="Token address..."
          className="flex-1 text-[10px] bg-bg-primary border border-border text-text-primary px-1.5 py-0.5 font-mono placeholder:text-text-secondary/50 min-w-0"
        />
        <button
          onClick={handleAdd}
          disabled={!addAddress.trim()}
          className="text-[10px] px-1.5 py-0.5 border border-border text-text-secondary hover:text-yellow hover:border-yellow/30 transition-colors disabled:opacity-30"
        >
          Add
        </button>
      </div>

      {items.length === 0 ? (
        <div className="text-text-secondary text-xs">No tokens in watchlist</div>
      ) : (
        <div className="flex flex-col -mx-3">
          {items.map((item) => {
            const chain = getChainInfo(item.chain)
            const price = priceData[item.address]
            const isSelected = selectedEvent?.token_address === item.address
            return (
              <div
                key={item.id}
                className={`flex items-center gap-2 px-3 py-1.5 cursor-pointer border-b border-border hover:bg-bg-titlebar transition-colors ${
                  isSelected ? 'bg-bg-titlebar' : ''
                }`}
                onClick={() => handleClick(item)}
              >
                <span
                  className="text-[10px] font-mono px-1 py-0.5 border border-border min-w-[36px] text-center"
                  style={{ color: chain.color }}
                >
                  {chain.label}
                </span>
                <span className="text-xs text-text-primary flex-1 truncate">
                  {item.symbol || truncateAddress(item.address)}
                </span>
                {price?.price_usd && (
                  <span className="text-xs font-mono text-text-primary">
                    {formatUsd(Number(price.price_usd))}
                  </span>
                )}
                {price?.price_change_24h != null && (
                  <span className={`text-[10px] font-mono ${Number(price.price_change_24h) >= 0 ? 'text-green' : 'text-red'}`}>
                    {formatPercent(Number(price.price_change_24h))}
                  </span>
                )}
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    remove.mutate(item.id)
                  }}
                  className="text-[10px] text-text-secondary hover:text-red px-1"
                >
                  x
                </button>
              </div>
            )
          })}
        </div>
      )}
    </Panel>
  )
}
