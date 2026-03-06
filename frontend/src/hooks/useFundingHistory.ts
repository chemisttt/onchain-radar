import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export interface HistoryPoint {
  time: string
  rates: Record<string, number>
}

export interface FundingHistoryData {
  symbol: string
  hours: number
  data: HistoryPoint[]
}

export function useFundingHistory(symbol: string | null, hours = 168) {
  return useQuery<FundingHistoryData>({
    queryKey: ['funding-history', symbol, hours],
    queryFn: async () => (await client.get('/funding/history', { params: { symbol, hours } })).data,
    enabled: !!symbol,
    staleTime: 5 * 60 * 1000,
  })
}
