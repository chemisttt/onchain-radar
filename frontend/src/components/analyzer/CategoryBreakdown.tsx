import { useState } from 'react'

interface Flag {
  ok: boolean
  msg: string
}

interface Category {
  score: number
  max: number
  flags: Flag[]
}

interface CategoryBreakdownProps {
  categories: Record<string, Category>
}

const CATEGORY_LABELS: Record<string, string> = {
  contract: 'Contract',
  liquidity: 'Liquidity',
  holders: 'Holders',
  trading: 'Trading',
}

const CATEGORY_ORDER = ['contract', 'liquidity', 'holders', 'trading']

export default function CategoryBreakdown({ categories }: CategoryBreakdownProps) {
  const [expanded, setExpanded] = useState<string | null>('contract')

  return (
    <div className="space-y-1">
      {CATEGORY_ORDER.map((key) => {
        const cat = categories[key]
        if (!cat) return null

        const isOpen = expanded === key
        const pct = (cat.score / cat.max) * 100
        const barColor = pct >= 80 ? 'bg-green' : pct >= 50 ? 'bg-yellow' : 'bg-red'

        return (
          <div key={key} className="border border-border">
            <button
              onClick={() => setExpanded(isOpen ? null : key)}
              className="w-full flex items-center justify-between px-3 py-2 bg-bg-titlebar hover:bg-bg-panel transition-colors"
            >
              <span className="text-[11px] font-mono text-text-primary uppercase">
                {CATEGORY_LABELS[key]} ({cat.score}/{cat.max})
              </span>
              <div className="flex items-center gap-2">
                <div className="w-16 h-1.5 bg-bg-app rounded-full overflow-hidden">
                  <div className={`h-full ${barColor} transition-all`} style={{ width: `${pct}%` }} />
                </div>
                <span className="text-text-secondary text-[10px]">{isOpen ? '^' : 'v'}</span>
              </div>
            </button>
            {isOpen && (
              <div className="px-3 py-2 space-y-1 bg-bg-panel">
                {cat.flags.map((flag, i) => (
                  <div key={i} className="flex items-center gap-2 text-[11px] font-mono">
                    <span className={flag.ok ? 'text-green' : 'text-red'}>
                      {flag.ok ? '[+]' : '[-]'}
                    </span>
                    <span className={flag.ok ? 'text-text-primary' : 'text-text-secondary'}>
                      {flag.msg}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
