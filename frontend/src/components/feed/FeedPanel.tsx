import { useMemo } from 'react'
import Panel from '../layout/Panel'
import FeedFilters from './FeedFilters'
import FeedItem from './FeedItem'
import { useFeed } from '../../hooks/useFeed'
import { useFeedStore } from '../../store/feed'

const STATUS_DOT: Record<string, string> = {
  connected: 'bg-green',
  disconnected: 'bg-red',
  reconnecting: 'bg-yellow animate-pulse',
}

const MAX_RENDERED = 80

export default function FeedPanel() {
  useFeed()
  const { events, chainFilter, typeFilter, wsStatus } = useFeedStore()

  const filtered = useMemo(
    () =>
      events
        .filter((e) => {
          if (chainFilter && e.chain !== chainFilter) return false
          if (typeFilter && e.event_type !== typeFilter) return false
          return true
        })
        .slice(0, MAX_RENDERED),
    [events, chainFilter, typeFilter],
  )

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          Live Feed
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${STATUS_DOT[wsStatus] || 'bg-red'}`} title={wsStatus} />
          {wsStatus === 'reconnecting' && <span className="text-[9px] text-yellow font-normal">reconnecting...</span>}
        </span>
      }
    >
      <FeedFilters />
      <div className="flex flex-col -mx-3">
        {filtered.length === 0 ? (
          <div className="text-text-secondary text-xs px-3 py-4">Waiting for events...</div>
        ) : (
          filtered.map((event) => <FeedItem key={event.id} event={event} />)
        )}
      </div>
    </Panel>
  )
}
