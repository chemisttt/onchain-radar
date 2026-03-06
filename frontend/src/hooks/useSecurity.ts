import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export function useSecurity(chain: string | null, address: string | null) {
  return useQuery({
    queryKey: ['security', chain, address],
    queryFn: async () => {
      const { data } = await client.get(`/security/${chain}/${address}`)
      return data
    },
    enabled: !!chain && !!address,
    staleTime: 15 * 60 * 1000,
  })
}
