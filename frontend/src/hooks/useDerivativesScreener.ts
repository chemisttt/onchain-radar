import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export interface ScreenerRow {
  symbol: string
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
  percentile_avg: number
  ob_depth_usd: number
  ob_skew_zscore: number
}

export function useDerivativesScreener(sort = 'oi_zscore', limit = 50) {
  return useQuery<ScreenerRow[]>({
    queryKey: ['derivatives-screener', sort, limit],
    queryFn: async () => (await client.get('/derivatives/screener', { params: { sort, limit } })).data,
    refetchInterval: 60000,
  })
}
