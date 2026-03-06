import { create } from 'zustand'

export interface FeedEvent {
  id: number
  event_type: string
  chain: string
  token_address: string | null
  pair_address: string | null
  token_symbol: string | null
  details: Record<string, unknown>
  severity: string
  created_at: string
}

interface FeedStore {
  events: FeedEvent[]
  selectedEvent: FeedEvent | null
  chainFilter: string | null
  typeFilter: string | null
  wsStatus: 'connected' | 'disconnected' | 'reconnecting'
  addEvent: (event: FeedEvent) => void
  addEvents: (events: FeedEvent[]) => void
  setEvents: (events: FeedEvent[]) => void
  selectEvent: (event: FeedEvent | null) => void
  setChainFilter: (chain: string | null) => void
  setTypeFilter: (type: string | null) => void
  setWsStatus: (status: 'connected' | 'disconnected' | 'reconnecting') => void
}

export const useFeedStore = create<FeedStore>((set) => ({
  events: [],
  selectedEvent: null,
  chainFilter: null,
  typeFilter: null,
  wsStatus: 'disconnected',
  addEvent: (event) =>
    set((s) => {
      if (event.id && s.events.some((e) => e.id === event.id)) return s
      return { events: [event, ...s.events].slice(0, 300) }
    }),
  addEvents: (events) =>
    set((s) => {
      const ids = new Set(s.events.map((e) => e.id))
      const fresh = events.filter((e) => !e.id || !ids.has(e.id))
      if (!fresh.length) return s
      return { events: [...[...fresh].reverse(), ...s.events].slice(0, 300) }
    }),
  setEvents: (events) => set({ events: events.slice(0, 300) }),
  selectEvent: (event) => set({ selectedEvent: event }),
  setChainFilter: (chain) => set({ chainFilter: chain }),
  setTypeFilter: (type) => set({ typeFilter: type }),
  setWsStatus: (status) => set({ wsStatus: status }),
}))
