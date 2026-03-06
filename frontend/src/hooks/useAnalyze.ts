import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

interface Flag {
  ok: boolean
  msg: string
}

interface Category {
  score: number
  max: number
  flags: Flag[]
}

interface RedFlag {
  severity: 'critical' | 'high' | 'medium' | 'low'
  msg: string
}

export interface AnalysisResult {
  chain: string
  address: string
  score: number
  verdict: string
  categories: {
    contract: Category
    liquidity: Category
    holders: Category
    trading: Category
  }
  red_flags: RedFlag[]
  token_data: Record<string, unknown>
  raw: {
    goplus: Record<string, unknown>
    honeypot: Record<string, unknown>
    rugcheck: Record<string, unknown>
  }
  cached: boolean
}

export function useAnalyze(chain: string | null, address: string | null) {
  return useQuery<AnalysisResult>({
    queryKey: ['analyze', chain, address],
    queryFn: async () => (await client.get(`/analyze/${chain}/${address}`)).data,
    enabled: !!chain && !!address && address.length > 5,
    staleTime: 10 * 60 * 1000,
    retry: 1,
  })
}
