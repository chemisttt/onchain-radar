import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import client from '../api/client'

export interface Trade {
  id: number
  alert_id: number | null
  symbol: string
  direction: string
  signal_type: string
  entry_price: number
  entry_size_usd: number
  leverage: number
  sl_price: number | null
  sl_order_id: string | null
  exit_price: number | null
  exit_reason: string | null
  pnl_pct: number | null
  pnl_usd: number | null
  status: 'open' | 'closed'
  opened_at: string
  closed_at: string | null
  meta: string
}

export interface TradingStats {
  open_count: number
  closed_count: number
  win_count: number
  win_rate: number
  total_pnl_usd: number
  avg_pnl_pct: number
}

export function useTradingPositions() {
  return useQuery<Trade[]>({
    queryKey: ['trading-positions'],
    queryFn: async () => (await client.get('/trading/positions')).data,
    refetchInterval: 15000,
  })
}

export function useTradingHistory(limit = 50) {
  return useQuery<Trade[]>({
    queryKey: ['trading-history', limit],
    queryFn: async () => (await client.get('/trading/history', { params: { limit } })).data,
    refetchInterval: 60000,
  })
}

export function useTradingStats() {
  return useQuery<TradingStats>({
    queryKey: ['trading-stats'],
    queryFn: async () => (await client.get('/trading/stats')).data,
    refetchInterval: 30000,
  })
}

export function useClosePosition() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (tradeId: number) => (await client.post(`/trading/close/${tradeId}`)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['trading-positions'] })
      qc.invalidateQueries({ queryKey: ['trading-history'] })
      qc.invalidateQueries({ queryKey: ['trading-stats'] })
    },
  })
}
