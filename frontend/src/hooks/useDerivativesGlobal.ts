import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export interface GlobalOIPoint {
  date: string
  btc: number
  eth: number
  others: number
  total: number
}

export interface GlobalZPoint {
  date: string
  zscore: number
}

export interface GlobalLiqPoint {
  date: string
  value: number
}

export interface RiskAppetitePoint {
  date: string
  value: number
}

export interface PerformancePoint {
  date: string
  pct: number
}

export interface HeatmapEntry {
  symbol: string
  data: { date: string; rate: number }[]
}

export interface AltOIDomPoint {
  date: string
  value: number
}

export interface DerivativesGlobal {
  global_oi: GlobalOIPoint[]
  global_oi_zscore: GlobalZPoint[]
  global_liquidations: GlobalLiqPoint[]
  risk_appetite: RiskAppetitePoint[]
  alt_oi_dominance: AltOIDomPoint[]
  performance: Record<string, PerformancePoint[]>
  funding_heatmap: HeatmapEntry[]
  heatmap_dates: string[]
}

export function useDerivativesGlobal(days = 365) {
  return useQuery<DerivativesGlobal>({
    queryKey: ['derivatives-global', days],
    queryFn: async () => (await client.get('/derivatives/global', { params: { days } })).data,
    staleTime: 5 * 60 * 1000,
  })
}
