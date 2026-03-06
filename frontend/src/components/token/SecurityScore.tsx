interface Props {
  goplus: Record<string, unknown>
  honeypot: Record<string, unknown>
  rugcheck: Record<string, unknown>
  chain: string
}

function Flag({ label, danger }: { label: string; danger: boolean }) {
  return (
    <span className={`text-[10px] px-1.5 py-0.5 border font-mono ${
      danger ? 'text-red border-red/30 bg-red/5' : 'text-green border-green/30 bg-green/5'
    }`}>
      {label}
    </span>
  )
}

export default function SecurityScore({ goplus, honeypot, rugcheck, chain }: Props) {
  if (!goplus || Object.keys(goplus).length === 0) {
    return <div className="text-text-secondary text-xs">No security data available</div>
  }

  const isHoneypot = goplus.is_honeypot || honeypot?.is_honeypot
  const buyTax = Number(goplus.buy_tax || honeypot?.buy_tax || 0)
  const sellTax = Number(goplus.sell_tax || honeypot?.sell_tax || 0)

  // Calculate score (0-100, higher = safer)
  let score = 100
  if (isHoneypot) score -= 50
  if (goplus.is_mintable) score -= 15
  if (goplus.hidden_owner) score -= 10
  if (goplus.can_take_back_ownership) score -= 15
  if (goplus.is_blacklisted) score -= 10
  if (goplus.cannot_sell_all) score -= 20
  if (goplus.is_proxy) score -= 5
  if (buyTax > 5) score -= 10
  if (sellTax > 5) score -= 10
  if (!goplus.is_open_source) score -= 10
  score = Math.max(0, score)

  const scoreColor = score >= 70 ? 'text-green' : score >= 40 ? 'text-yellow' : 'text-red'

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-secondary uppercase">Safety Score</span>
        <span className={`text-lg font-mono font-bold ${scoreColor}`}>{score}</span>
      </div>

      <div className="flex flex-wrap gap-1">
        <Flag label={isHoneypot ? 'HONEYPOT' : 'NOT HONEYPOT'} danger={!!isHoneypot} />
        {goplus.is_mintable != null && <Flag label={goplus.is_mintable ? 'MINTABLE' : 'NOT MINTABLE'} danger={!!goplus.is_mintable} />}
        {goplus.is_proxy != null && <Flag label={goplus.is_proxy ? 'PROXY' : 'NO PROXY'} danger={!!goplus.is_proxy} />}
        {goplus.is_open_source != null && <Flag label={goplus.is_open_source ? 'VERIFIED' : 'UNVERIFIED'} danger={!goplus.is_open_source} />}
        {goplus.hidden_owner != null && goplus.hidden_owner && <Flag label="HIDDEN OWNER" danger={true} />}
        {goplus.can_take_back_ownership != null && goplus.can_take_back_ownership && <Flag label="CAN RECLAIM" danger={true} />}
        {goplus.is_blacklisted != null && goplus.is_blacklisted && <Flag label="BLACKLIST" danger={true} />}
        {goplus.cannot_sell_all != null && goplus.cannot_sell_all && <Flag label="SELL LIMIT" danger={true} />}
      </div>

      {(buyTax > 0 || sellTax > 0) && (
        <div className="flex gap-4 text-xs font-mono">
          <span>Buy tax: <span className={buyTax > 5 ? 'text-red' : 'text-text-primary'}>{(buyTax * 100).toFixed(1)}%</span></span>
          <span>Sell tax: <span className={sellTax > 5 ? 'text-red' : 'text-text-primary'}>{(sellTax * 100).toFixed(1)}%</span></span>
        </div>
      )}

      {chain === 'solana' && rugcheck && typeof rugcheck.score === 'number' && (
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-text-secondary">RugCheck</span>
          <span className={`text-xs font-mono ${rugcheck.score < 500 ? 'text-green' : rugcheck.score < 2000 ? 'text-yellow' : 'text-red'}`}>
            Score: {rugcheck.score as number}
          </span>
        </div>
      )}

      {honeypot?.honeypot_reason && (
        <div className="text-[10px] text-red">{honeypot.honeypot_reason as string}</div>
      )}
    </div>
  )
}
