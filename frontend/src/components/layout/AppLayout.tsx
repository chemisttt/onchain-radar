import { ReactNode, useState, useEffect } from 'react'
import { useAppStore, type ActiveTab } from '../../store/app'

interface AppLayoutProps {
  feed: ReactNode
  detail: ReactNode
  watchlist: ReactNode
  funding: ReactNode
  fundingArb: ReactNode
  analyzer: ReactNode
  derivatives: ReactNode
  trading: ReactNode
  docs: ReactNode
}

function Clock() {
  const [time, setTime] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return <span className="text-xs text-text-secondary font-mono">{time.toLocaleTimeString()}</span>
}

const TABS: { key: ActiveTab; label: string }[] = [
  { key: 'feed', label: 'Feed' },
  { key: 'funding', label: 'Funding Arb' },
  { key: 'analyzer', label: 'Token Analyzer' },
  { key: 'derivatives', label: 'Derivatives' },
  { key: 'trading', label: 'Trading' },
  { key: 'docs', label: 'Docs' },
]

export default function AppLayout({ feed, detail, watchlist, funding, fundingArb, analyzer, derivatives, trading, docs }: AppLayoutProps) {
  const { activeTab, setActiveTab } = useAppStore()

  return (
    <div className="h-screen flex flex-col bg-bg-app">
      <header className="flex items-center justify-between px-4 py-2 border-b border-border bg-bg-panel">
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-text-primary tracking-wide">ON-CHAIN RADAR</span>
          <span className="text-[10px] text-text-secondary border border-border px-1.5 py-0.5">LIVE</span>
          <div className="flex items-center gap-1 ml-4">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`text-[11px] px-3 py-1 font-mono transition-colors border ${
                  activeTab === tab.key
                    ? 'border-text-primary text-text-primary bg-bg-titlebar'
                    : 'border-border text-text-secondary hover:text-text-primary hover:border-text-secondary'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
        <Clock />
      </header>

      {activeTab === 'feed' && (
        <div className="flex-1 grid grid-cols-[1fr_1fr] grid-rows-[1fr_1fr] gap-px bg-bg-app p-px min-h-0">
          {feed}
          {detail}
          {watchlist}
          {funding}
        </div>
      )}

      {activeTab === 'funding' && (
        <div className="flex-1 min-h-0 p-px">
          {fundingArb}
        </div>
      )}

      {activeTab === 'analyzer' && (
        <div className="flex-1 min-h-0 p-px">
          {analyzer}
        </div>
      )}

      {activeTab === 'derivatives' && (
        <div className="flex-1 min-h-0 p-px">
          {derivatives}
        </div>
      )}

      {activeTab === 'trading' && (
        <div className="flex-1 min-h-0 p-px">
          {trading}
        </div>
      )}

      {activeTab === 'docs' && (
        <div className="flex-1 min-h-0 p-px">
          {docs}
        </div>
      )}
    </div>
  )
}
