import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export interface LiqLevel {
  price: number
  long_vol: number
  short_vol: number
  leverage: number
}

export interface LiqEvent {
  price: number
  side: string
  usd_value: number
  exchange: string
  ts: number
}

export interface LiquidationMapData {
  symbol: string
  current_price: number
  levels: LiqLevel[]
  recent_events: LiqEvent[]
}

export function useLiquidationMap(symbol: string | null) {
  return useQuery<LiquidationMapData>({
    queryKey: ['derivatives-liq-map', symbol],
    queryFn: async () =>
      (await client.get(`/derivatives/liquidation-map/${symbol}`)).data,
    enabled: !!symbol,
    refetchInterval: 30000,
  })
}
