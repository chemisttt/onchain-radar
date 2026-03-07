import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export interface PriceRvPoint {
  date: string
  price: number
  rv_30d: number
}

export interface IvRvPoint {
  date: string
  price: number
  iv_30d: number | null
  rv_30d: number | null
}

export interface SkewZPoint {
  date: string
  price: number
  skew_25d: number | null
  skew_zscore: number | null
}

export interface VrpPoint {
  date: string
  vrp: number | null
  vrp_zscore: number | null
}

export interface VolConeEntry {
  p10: number
  p25: number
  p50: number
  p75: number
  p90: number
  current: number
}

export interface MomentumData {
  symbol: string
  has_options_data: boolean
  price_rv: PriceRvPoint[]
  iv_rv: (IvRvPoint & { vrp?: number | null })[]
  skew_zscore: SkewZPoint[]
  vrp_series: VrpPoint[]
  vol_cone: Record<string, VolConeEntry>
}

export function useMomentum(symbol: string | null, days = 365) {
  return useQuery<MomentumData>({
    queryKey: ['derivatives-momentum', symbol, days],
    queryFn: async () =>
      (await client.get(`/derivatives/momentum/${symbol}`, { params: { days } })).data,
    enabled: !!symbol,
    staleTime: 300_000,
  })
}
