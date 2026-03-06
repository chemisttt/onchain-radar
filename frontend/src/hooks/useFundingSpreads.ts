import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export interface SpreadRow {
  symbol: string
  long_exchange: string
  long_rate: number
  short_exchange: string
  short_rate: number
  spread: number
  spread_pct: number
  est_daily_usd: number
  fees_pct: number
  fees_daily: number
  net_daily: number
  exchanges_count: number
  open_interest: number
  volume_24h: number
  all_rates: Record<string, number>
}

interface SpreadParams {
  min_spread?: number
  position_size?: number
  limit?: number
  only_positive?: boolean
}

export function useFundingSpreads(params?: SpreadParams) {
  return useQuery<SpreadRow[]>({
    queryKey: ['funding-spreads', params],
    queryFn: async () => (await client.get('/funding/spreads', { params })).data,
    refetchInterval: 60000,
  })
}
