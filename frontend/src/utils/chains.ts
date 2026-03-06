export const CHAINS: Record<string, { label: string; color: string; chainId: number }> = {
  ethereum: { label: 'ETH', color: '#627eea', chainId: 1 },
  bsc: { label: 'BSC', color: '#f0b90b', chainId: 56 },
  polygon: { label: 'MATIC', color: '#8247e5', chainId: 137 },
  arbitrum: { label: 'ARB', color: '#28a0f0', chainId: 42161 },
  base: { label: 'BASE', color: '#0052ff', chainId: 8453 },
  solana: { label: 'SOL', color: '#9945ff', chainId: 0 },
  avalanche: { label: 'AVAX', color: '#e84142', chainId: 43114 },
  optimism: { label: 'OP', color: '#ff0420', chainId: 10 },
  fantom: { label: 'FTM', color: '#1969ff', chainId: 250 },
  cronos: { label: 'CRO', color: '#002d74', chainId: 25 },
  sui: { label: 'SUI', color: '#6fbcf0', chainId: 0 },
  ton: { label: 'TON', color: '#0098ea', chainId: 0 },
  perp: { label: 'PERP', color: '#eab308', chainId: 0 },
}

export function getChainInfo(chain: string) {
  return CHAINS[chain] ?? { label: chain.slice(0, 5).toUpperCase(), color: '#808080', chainId: 0 }
}

export function getExplorerUrl(chain: string, address: string, type: 'address' | 'token' = 'address'): string {
  const explorers: Record<string, string> = {
    ethereum: 'https://etherscan.io',
    bsc: 'https://bscscan.com',
    polygon: 'https://polygonscan.com',
    arbitrum: 'https://arbiscan.io',
    base: 'https://basescan.org',
    solana: 'https://solscan.io',
    avalanche: 'https://snowtrace.io',
    optimism: 'https://optimistic.etherscan.io',
    fantom: 'https://ftmscan.com',
    cronos: 'https://cronoscan.com',
  }
  const base = explorers[chain] ?? 'https://etherscan.io'
  if (chain === 'solana') return `${base}/${type === 'token' ? 'token' : 'account'}/${address}`
  return `${base}/${type}/${address}`
}
