import { useState, useRef, useCallback } from 'react'

interface ClaudeEvent {
  type: 'text' | 'error' | 'done' | 'cancelled'
  content?: string
  session_id?: string
}

export function useClaudeStream() {
  const [text, setText] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const analyze = useCallback((chain: string, address: string, prompt?: string) => {
    abortRef.current?.abort()
    setText('')
    setIsStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller

    fetch('/api/claude/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chain, address, prompt }),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok || !res.body) throw new Error(`Request failed: ${res.status}`)

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const blocks = buffer.split('\n\n')
          buffer = blocks.pop() || ''

          for (const block of blocks) {
            const trimmed = block.trim()
            if (!trimmed.startsWith('data: ')) continue
            const jsonStr = trimmed.slice(6)
            try {
              const event: ClaudeEvent = JSON.parse(jsonStr)
              if (event.type === 'text' && event.content) {
                setText((prev) => prev + event.content)
              } else if (event.type === 'error') {
                setText((prev) => prev + `\n\nError: ${event.content}`)
              }
            } catch { /* ignore */ }
          }
        }
      })
      .catch((err) => {
        if (err.name === 'AbortError') return
        setText((prev) => prev + `\n\nConnection error: ${err.message}`)
      })
      .finally(() => setIsStreaming(false))
  }, [])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setIsStreaming(false)
  }, [])

  const reset = useCallback(() => {
    stop()
    setText('')
  }, [stop])

  return { text, isStreaming, analyze, stop, reset }
}
