import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

interface ExchangeRate {
  rate: number
  apr: number
  next_funding_time: number | null
}

export interface FundingRow {
  symbol: string
  rates: Record<string, ExchangeRate>
  next_funding_ms: number | null
}

export function useFunding(params?: { sort?: string; min_rate?: number; symbol?: string }) {
  return useQuery<FundingRow[]>({
    queryKey: ['funding', params],
    queryFn: async () => (await client.get('/funding/rates', { params })).data,
    refetchInterval: 60000,
  })
}
