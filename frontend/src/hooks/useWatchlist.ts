import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import client from '../api/client'

interface WatchlistItem {
  id: number
  chain: string
  address: string
  symbol: string | null
  name: string | null
  notes: string
  added_at: string
}

export function useWatchlist() {
  const qc = useQueryClient()

  const list = useQuery<WatchlistItem[]>({
    queryKey: ['watchlist'],
    queryFn: async () => (await client.get('/watchlist')).data,
  })

  const prices = useQuery<Record<string, { price_usd: string; price_change_24h: number; volume_24h: number }>>({
    queryKey: ['watchlist-prices'],
    queryFn: async () => (await client.get('/watchlist/prices')).data,
    refetchInterval: 30000,
    enabled: (list.data?.length ?? 0) > 0,
  })

  const add = useMutation({
    mutationFn: (item: { chain: string; address: string; symbol?: string; name?: string }) =>
      client.post('/watchlist', item),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const remove = useMutation({
    mutationFn: (id: number) => client.delete(`/watchlist/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  return { list, prices, add, remove }
}
