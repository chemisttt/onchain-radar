interface RiskScoreProps {
  score: number
  verdict: string
}

const VERDICT_COLORS: Record<string, string> = {
  LOW_RISK: 'text-green border-green',
  MEDIUM_RISK: 'text-yellow border-yellow',
  HIGH_RISK: 'text-red border-red',
  CRITICAL_RISK: 'text-red border-red',
}

const VERDICT_LABELS: Record<string, string> = {
  LOW_RISK: 'LOW RISK',
  MEDIUM_RISK: 'MEDIUM',
  HIGH_RISK: 'HIGH RISK',
  CRITICAL_RISK: 'CRITICAL',
}

export default function RiskScore({ score, verdict }: RiskScoreProps) {
  const colorClass = VERDICT_COLORS[verdict] || 'text-text-secondary border-border'
  const label = VERDICT_LABELS[verdict] || verdict

  // SVG circular progress
  const radius = 38
  const circumference = 2 * Math.PI * radius
  const progress = (score / 100) * circumference
  const strokeColor = verdict === 'LOW_RISK'
    ? '#22c55e'
    : verdict === 'MEDIUM_RISK'
      ? '#eab308'
      : '#ef4444'

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative w-24 h-24">
        <svg className="w-24 h-24 -rotate-90" viewBox="0 0 96 96">
          <circle
            cx="48" cy="48" r={radius}
            fill="none"
            stroke="#2a2a2a"
            strokeWidth="6"
          />
          <circle
            cx="48" cy="48" r={radius}
            fill="none"
            stroke={strokeColor}
            strokeWidth="6"
            strokeDasharray={circumference}
            strokeDashoffset={circumference - progress}
            strokeLinecap="round"
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-2xl font-mono font-bold text-text-primary">{score}</span>
        </div>
      </div>
      <span className={`text-[10px] font-mono font-bold px-2 py-0.5 border ${colorClass}`}>
        {label}
      </span>
    </div>
  )
}
