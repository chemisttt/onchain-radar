import { useState } from 'react'
import { useClaudeStream } from '../../hooks/useClaudeStream'

interface Props {
  chain: string
  address: string
}

export default function ClaudeAnalysis({ chain, address }: Props) {
  const { text, isStreaming, analyze, stop, reset } = useClaudeStream()
  const [expanded, setExpanded] = useState(false)

  const handleAnalyze = () => {
    setExpanded(true)
    analyze(chain, address)
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-secondary uppercase">Claude Analysis</span>
        {!isStreaming && !text && (
          <button
            onClick={handleAnalyze}
            className="text-[10px] px-2 py-0.5 border border-border text-blue hover:bg-bg-titlebar transition-colors"
          >
            Analyze
          </button>
        )}
        {isStreaming && (
          <button
            onClick={stop}
            className="text-[10px] px-2 py-0.5 border border-red/30 text-red hover:bg-red/5 transition-colors"
          >
            Stop
          </button>
        )}
        {text && !isStreaming && (
          <button
            onClick={reset}
            className="text-[10px] px-2 py-0.5 border border-border text-text-secondary hover:bg-bg-titlebar transition-colors"
          >
            Clear
          </button>
        )}
        {text && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-[10px] px-1 text-text-secondary hover:text-text-primary"
          >
            {expanded ? '[-]' : '[+]'}
          </button>
        )}
      </div>

      {expanded && text && (
        <div className="text-xs text-text-primary whitespace-pre-wrap font-mono leading-relaxed border border-border bg-bg-app p-2 max-h-[300px] overflow-auto">
          {text}
          {isStreaming && <span className="animate-pulse text-blue">|</span>}
        </div>
      )}
    </div>
  )
}
