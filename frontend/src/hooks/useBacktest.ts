import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export interface BacktestCandle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface BacktestAlert {
  time: number
  type: string
  tier: string
  confluence: number
  fired_at: string
  entry_price: number
  direction: string | null
  price_1d: number | null
  price_3d: number | null
  price_7d: number | null
  return_1d: number | null
  return_3d: number | null
  return_7d: number | null
  mfe_return?: number | null
  mfe_price?: number | null
  simulated?: boolean
  zscores?: { oi: number; funding: number; liq: number; volume: number }
}

export interface BacktestStats {
  total_signals: number
  real_signals: number
  simulated_signals: number
  with_returns: number
  wins: number
  win_rate: number
  avg_return: number
  by_type?: Record<string, { count: number; win_rate: number; avg_return: number; pf: number }>
}

export interface PriceStructure {
  trend: string
  key_levels: { price: number; type: string; touches: number }[]
  ema_21: number
  ema_50: number
  ema_200: number | null
  atr_14: number
  current_price: number
}

export interface BacktestData {
  candles: BacktestCandle[]
  alerts: BacktestAlert[]
  structure: PriceStructure | null
  stats: BacktestStats
}

export function useBacktest(symbol: string | null, range: string = '1M') {
  return useQuery<BacktestData>({
    queryKey: ['backtest', symbol, range],
    queryFn: async () =>
      (await client.get(`/derivatives/backtest/${symbol}`, { params: { range } })).data,
    enabled: !!symbol,
    refetchInterval: 300_000, // 5 min
  })
}
