import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export interface DerivativesLatest {
  price: number
  open_interest_usd: number
  funding_rate: number
  liquidations_long: number
  liquidations_short: number
  liquidations_delta: number
  volume_usd: number
  oi_zscore: number
  funding_zscore: number
  liq_zscore: number
  volume_zscore: number
  oi_percentile: number
  funding_percentile: number
  liq_percentile: number
  volume_percentile: number
  oi_change_24h_pct: number
  price_change_24h_pct: number
}

export interface HistoryPoint {
  date: string
  price: number
  oi: number
  funding: number
  liq_delta: number
  volume: number
  oi_zscore: number
  funding_zscore: number
  liq_zscore: number
}

export interface DerivativesDetail {
  symbol: string
  latest: DerivativesLatest
  history: HistoryPoint[]
}

export function useDerivativesDetail(symbol: string | null, days = 365) {
  return useQuery<DerivativesDetail>({
    queryKey: ['derivatives-detail', symbol, days],
    queryFn: async () => (await client.get(`/derivatives/${symbol}`, { params: { days } })).data,
    enabled: !!symbol,
    staleTime: 5 * 60 * 1000,
  })
}
