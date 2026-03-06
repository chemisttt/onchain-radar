import { useState, useCallback } from 'react'
import Panel from '../layout/Panel'
import RiskScore from './RiskScore'
import FlagsList from './FlagsList'
import CategoryBreakdown from './CategoryBreakdown'
import { useAnalyze } from '../../hooks/useAnalyze'

const CHAINS = [
  { value: 'ethereum', label: 'Ethereum' },
  { value: 'bsc', label: 'BSC' },
  { value: 'polygon', label: 'Polygon' },
  { value: 'arbitrum', label: 'Arbitrum' },
  { value: 'base', label: 'Base' },
  { value: 'solana', label: 'Solana' },
  { value: 'avalanche', label: 'Avalanche' },
  { value: 'optimism', label: 'Optimism' },
]

const RECENT_KEY = 'analyzer_recent'

function getRecent(): Array<{ chain: string; address: string; symbol?: string }> {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]').slice(0, 8)
  } catch {
    return []
  }
}

function saveRecent(chain: string, address: string, symbol?: string) {
  const recent = getRecent().filter((r) => r.address !== address)
  recent.unshift({ chain, address, symbol })
  localStorage.setItem(RECENT_KEY, JSON.stringify(recent.slice(0, 8)))
}

export default function AnalyzerPanel() {
  const [chain, setChain] = useState('ethereum')
  const [address, setAddress] = useState('')
  const [queryChain, setQueryChain] = useState<string | null>(null)
  const [queryAddress, setQueryAddress] = useState<string | null>(null)

  const { data, isLoading, error } = useAnalyze(queryChain, queryAddress)

  const handleAnalyze = useCallback(() => {
    if (!address || address.length < 5) return
    setQueryChain(chain)
    setQueryAddress(address)
  }, [chain, address])

  // Save to recent when data arrives
  if (data && queryAddress && !data.cached) {
    const symbol = (data.token_data as Record<string, unknown>)?.base_token
      ? ((data.token_data as Record<string, Record<string, string>>).base_token?.symbol || '')
      : ''
    saveRecent(data.chain, data.address, symbol)
  }

  const recent = getRecent()

  return (
    <div className="h-full flex flex-col gap-px">
      {/* Input bar */}
      <div className="bg-bg-panel border border-border px-4 py-2 flex items-center gap-3 flex-shrink-0">
        <select
          value={chain}
          onChange={(e) => setChain(e.target.value)}
          className="text-[11px] bg-bg-primary border border-border text-text-primary px-2 py-1 font-mono"
        >
          {CHAINS.map((c) => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </select>
        <input
          type="text"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAnalyze()}
          placeholder="Contract address (0x... or Solana mint)"
          className="flex-1 text-[11px] bg-bg-primary border border-border text-text-primary px-2 py-1 font-mono placeholder:text-text-secondary/50"
        />
        <button
          onClick={handleAnalyze}
          disabled={isLoading || address.length < 5}
          className={`text-[11px] px-4 py-1 border font-mono transition-colors ${
            isLoading
              ? 'border-border text-text-secondary animate-pulse'
              : 'border-blue text-blue hover:bg-blue/10'
          }`}
        >
          {isLoading ? 'Analyzing...' : 'Analyze'}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 grid grid-cols-[1fr_1fr] gap-px min-h-0">
        {/* Left: Score + Flags */}
        <Panel title="Analysis Result" className="min-h-0">
          {!data && !isLoading && !error && (
            <div className="flex flex-col items-center justify-center h-full text-text-secondary text-xs gap-4">
              <div className="text-center">
                Enter a contract address and click Analyze
              </div>
              {recent.length > 0 && (
                <div className="w-full max-w-md">
                  <div className="text-[10px] text-text-secondary uppercase mb-2">Recent</div>
                  <div className="flex flex-wrap gap-1">
                    {recent.map((r, i) => (
                      <button
                        key={i}
                        onClick={() => {
                          setChain(r.chain)
                          setAddress(r.address)
                          setQueryChain(r.chain)
                          setQueryAddress(r.address)
                        }}
                        className="text-[10px] font-mono px-2 py-0.5 border border-border text-text-secondary hover:text-text-primary hover:border-text-secondary transition-colors"
                      >
                        {r.symbol || r.address.slice(0, 8) + '...'}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {error && (
            <div className="text-red text-xs font-mono p-2">
              Error: {(error as Error).message}
            </div>
          )}

          {data && (
            <div className="flex flex-col gap-4">
              <div className="flex items-start gap-6">
                <RiskScore score={data.score} verdict={data.verdict} />
                <div className="flex-1">
                  <div className="text-[10px] text-text-secondary uppercase mb-2">Red Flags</div>
                  <FlagsList flags={data.red_flags} />
                </div>
              </div>

              {/* Token info */}
              {data.token_data && Object.keys(data.token_data).length > 0 && (
                <div className="border-t border-border pt-3">
                  <div className="grid grid-cols-3 gap-2 text-[10px] font-mono">
                    {(data.token_data as Record<string, unknown>).price_usd && (
                      <div>
                        <div className="text-text-secondary">Price</div>
                        <div className="text-text-primary">${String((data.token_data as Record<string, unknown>).price_usd)}</div>
                      </div>
                    )}
                    {(data.token_data as Record<string, Record<string, unknown>>).liquidity?.usd && (
                      <div>
                        <div className="text-text-secondary">Liquidity</div>
                        <div className="text-text-primary">
                          ${Number((data.token_data as Record<string, Record<string, unknown>>).liquidity.usd).toLocaleString()}
                        </div>
                      </div>
                    )}
                    {(data.token_data as Record<string, Record<string, unknown>>).volume?.h24 && (
                      <div>
                        <div className="text-text-secondary">Vol 24h</div>
                        <div className="text-text-primary">
                          ${Number((data.token_data as Record<string, Record<string, unknown>>).volume.h24).toLocaleString()}
                        </div>
                      </div>
                    )}
                    {(data.token_data as Record<string, unknown>).fdv && (
                      <div>
                        <div className="text-text-secondary">FDV</div>
                        <div className="text-text-primary">
                          ${Number((data.token_data as Record<string, unknown>).fdv).toLocaleString()}
                        </div>
                      </div>
                    )}
                    {(data.token_data as Record<string, unknown>).dex && (
                      <div>
                        <div className="text-text-secondary">DEX</div>
                        <div className="text-text-primary">{String((data.token_data as Record<string, unknown>).dex)}</div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Links */}
              <div className="flex items-center gap-2 border-t border-border pt-2">
                {(data.token_data as Record<string, unknown>)?.url && (
                  <a
                    href={String((data.token_data as Record<string, unknown>).url)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[10px] font-mono text-blue hover:underline"
                  >
                    [DexScreener]
                  </a>
                )}
                <a
                  href={
                    data.chain === 'solana'
                      ? `https://solscan.io/token/${data.address}`
                      : `https://etherscan.io/address/${data.address}`
                  }
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[10px] font-mono text-blue hover:underline"
                >
                  [Explorer]
                </a>
                <button
                  onClick={() => navigator.clipboard.writeText(data.address)}
                  className="text-[10px] font-mono text-text-secondary hover:text-text-primary"
                >
                  [Copy CA]
                </button>
              </div>
            </div>
          )}
        </Panel>

        {/* Right: Category breakdown */}
        <Panel title="Category Breakdown" className="min-h-0">
          {data ? (
            <CategoryBreakdown categories={data.categories} />
          ) : (
            <div className="text-text-secondary text-xs p-2">
              Analyze a token to see detailed breakdown
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}
