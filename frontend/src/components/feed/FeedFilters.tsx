import { useFeedStore } from '../../store/feed'
import { getChainInfo } from '../../utils/chains'

const CHAINS = ['ethereum', 'bsc', 'polygon', 'arbitrum', 'base', 'solana', 'avalanche', 'optimism']
const TYPES = ['NEW_PAIR', 'WHALE_TRANSFER', 'PRICE_PUMP', 'PRICE_DUMP', 'FUNDING_EXTREME', 'FUNDING_SPREAD', 'PROTOCOL_TVL_SPIKE', 'PROTOCOL_YIELD_NEW']
const TYPE_LABELS: Record<string, string> = {
  NEW_PAIR: 'NEW',
  WHALE_TRANSFER: 'WHALE',
  PRICE_PUMP: 'PUMP',
  PRICE_DUMP: 'DUMP',
  FUNDING_EXTREME: 'FUND',
  FUNDING_SPREAD: 'SPREAD',
  PROTOCOL_TVL_SPIKE: 'TVL',
  PROTOCOL_YIELD_NEW: 'YIELD',
}

function FilterBtn({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`text-[10px] px-1.5 py-0.5 border font-mono transition-colors ${
        active
          ? 'border-text-primary text-text-primary bg-bg-titlebar'
          : 'border-border text-text-secondary hover:text-text-primary'
      }`}
    >
      {label}
    </button>
  )
}

export default function FeedFilters() {
  const { chainFilter, typeFilter, setChainFilter, setTypeFilter } = useFeedStore()

  return (
    <div className="flex flex-wrap gap-1 mb-2">
      <FilterBtn label="ALL" active={!chainFilter} onClick={() => setChainFilter(null)} />
      {CHAINS.map((c) => (
        <FilterBtn key={c} label={getChainInfo(c).label} active={chainFilter === c} onClick={() => setChainFilter(chainFilter === c ? null : c)} />
      ))}
      <span className="w-px bg-border mx-1" />
      <FilterBtn label="ALL" active={!typeFilter} onClick={() => setTypeFilter(null)} />
      {TYPES.map((t) => (
        <FilterBtn key={t} label={TYPE_LABELS[t] || t} active={typeFilter === t} onClick={() => setTypeFilter(typeFilter === t ? null : t)} />
      ))}
    </div>
  )
}
