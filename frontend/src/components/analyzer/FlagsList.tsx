interface RedFlag {
  severity: string
  msg: string
}

interface FlagsListProps {
  flags: RedFlag[]
}

const SEVERITY_STYLES: Record<string, { icon: string; color: string }> = {
  critical: { icon: '!!', color: 'text-red' },
  high: { icon: '!', color: 'text-red' },
  medium: { icon: '~', color: 'text-yellow' },
  low: { icon: '-', color: 'text-text-secondary' },
}

export default function FlagsList({ flags }: FlagsListProps) {
  if (!flags.length) {
    return (
      <div className="text-green text-xs font-mono">No red flags detected</div>
    )
  }

  return (
    <div className="space-y-1">
      {flags.map((flag, i) => {
        const style = SEVERITY_STYLES[flag.severity] || SEVERITY_STYLES.low
        return (
          <div key={i} className="flex items-start gap-2 text-xs font-mono">
            <span className={`${style.color} font-bold text-[10px] uppercase w-16 flex-shrink-0`}>
              [{style.icon}] {flag.severity}
            </span>
            <span className={style.color}>{flag.msg}</span>
          </div>
        )
      })}
    </div>
  )
}
