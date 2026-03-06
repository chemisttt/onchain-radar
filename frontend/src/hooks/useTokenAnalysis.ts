import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export function useTokenAnalysis(chain: string | null, address: string | null, pairAddress?: string | null) {
  const hasAddress = !!address
  const hasPair = !!pairAddress
  const enabled = !!chain && (hasAddress || hasPair)

  return useQuery({
    queryKey: ['token', chain, address, pairAddress],
    queryFn: async () => {
      const tokenAddr = address || '_'
      const params = pairAddress ? { pair_address: pairAddress } : {}
      const { data } = await client.get(`/tokens/${chain}/${tokenAddr}`, { params })
      return data
    },
    enabled,
    staleTime: 5 * 60 * 1000,
  })
}
