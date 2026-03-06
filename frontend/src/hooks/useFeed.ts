import { useEffect, useRef, useCallback } from 'react'
import { useFeedStore, type FeedEvent } from '../store/feed'
import client from '../api/client'

export function useFeed() {
  const { addEvent, addEvents, setEvents, setWsStatus } = useFeedStore()
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<ReturnType<typeof setTimeout>>()
  const retryCount = useRef(0)

  const connect = useCallback(() => {
    setWsStatus('reconnecting')
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${location.host}/ws/feed`)
    wsRef.current = ws

    ws.onopen = () => {
      setWsStatus('connected')
      retryCount.current = 0
    }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'feed_event' && msg.data) {
          addEvent(msg.data as FeedEvent)
        } else if (msg.type === 'feed_batch' && Array.isArray(msg.data)) {
          // Batch update — add all events at once to avoid rapid re-renders
          addEvents(msg.data as FeedEvent[])
        }
      } catch { /* ignore parse errors */ }
    }

    ws.onclose = () => {
      setWsStatus('disconnected')
      // Exponential backoff: 1s, 2s, 4s, 8s, max 15s
      const delay = Math.min(1000 * 2 ** retryCount.current, 15000)
      retryCount.current++
      reconnectRef.current = setTimeout(connect, delay)
    }

    ws.onerror = () => ws.close()
  }, [addEvent, setWsStatus])

  useEffect(() => {
    // Load initial feed history
    client.get('/feed?limit=100').then((res) => {
      setEvents(res.data)
    }).catch(() => {})

    connect()

    return () => {
      clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [connect, setEvents])
}
