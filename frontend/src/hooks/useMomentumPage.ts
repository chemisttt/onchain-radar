import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export interface MomentumHistoryPoint {
  date: string
  price: number
  momentum: number | null
  di: number | null
  vr: number | null
}

export interface ScatterPoint {
  x: number
  y: number
}

export interface ScatterPeriod {
  points: ScatterPoint[]
  r2: number
  n: number
  avg_at_current: number
  current: number
}

export interface PriceDistHorizon {
  implied: {
    vol_pct: number
    low_1s: number
    high_1s: number
    low_2s: number
    high_2s: number
  }
  adjusted: {
    vol_pct: number
    low_1s: number
    high_1s: number
    low_2s: number
    high_2s: number
    center: number
  }
}

export interface MomentumStats {
  score: number
  zscore: number
  avg: number
  change_30d: number
}

export interface SkewStats {
  score: number
  skew: number
  zscore: number
  avg: number
  change_30d: number
}

export interface MomentumPageData {
  symbol: string
  regime: string
  metrics: {
    momentum_value: number | null
    cs_decile: number | null
    ts_decile: number | null
    rel_decile: number | null
    di: number | null
    vol_regime: number | null
    relative_volume: number | null
    proximity_52w_high: number | null
  }
  history: MomentumHistoryPoint[]
  di_scatter: Record<string, ScatterPeriod>
  vr_scatter: Record<string, ScatterPeriod>
  price_distribution: Record<string, PriceDistHorizon>
  momentum_stats: MomentumStats | Record<string, never>
  skew_stats: SkewStats | Record<string, never>
}

export function useMomentumPage(symbol: string | null, days = 365) {
  return useQuery<MomentumPageData>({
    queryKey: ['momentum-page', symbol, days],
    queryFn: async () =>
      (await client.get(`/derivatives/momentum-page/${symbol}`, { params: { days } })).data,
    enabled: !!symbol,
    staleTime: 300_000,
  })
}
