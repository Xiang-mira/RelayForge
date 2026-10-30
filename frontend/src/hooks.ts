import { useEffect, useRef, useState } from 'react'
import type { RuntimeEvent } from './api'
export function useHashState(
  key: string,
  initial = '',
): [string, (value: string, push?: boolean) => void] {
  const read = () =>
    new URLSearchParams(window.location.hash.split('?')[1] ?? '').get(key) ?? initial
  const [value, setValue] = useState(read)
  useEffect(() => {
    const sync = () => setValue(read())
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [key, initial])
  return [
    value,
    (next, push = false) => {
      const [path, query] = window.location.hash.split('?')
      const params = new URLSearchParams(query)
      if (next === initial || next === '') params.delete(key)
      else params.set(key, next)
      const hash = path + (params.size ? '?' + params.toString() : '')
      if (push) window.location.hash = hash
      else {
        window.history.replaceState(null, '', hash)
        window.dispatchEvent(new HashChangeEvent('hashchange'))
      }
      setValue(next)
    },
  ]
}
export function useLoad<T>(key: string, loader: () => Promise<T>, tick = 0) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const previousKey = useRef('')
  useEffect(() => {
    let active = true
    if (previousKey.current !== key) {
      setData(null)
      setLoading(true)
      previousKey.current = key
    }
    setError('')
    loader()
      .then((value) => {
        if (active) setData(value)
      })
      .catch((e) => {
        if (active) {
          setData(null)
          setError(String(e.message))
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [key, tick])
  return { data, error, loading }
}
export function useEvents(runId: string) {
  const [events, setEvents] = useState<RuntimeEvent[]>([])
  const [connection, setConnection] = useState('连接中')
  useEffect(() => {
    setEvents([])
    setConnection('连接中')
    const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`)
    source.onopen = () => setConnection('已连接')
    source.onerror = () => setConnection('连接中断，正在重连')
    source.addEventListener('transport_error', () => setConnection('数据库暂不可用，等待重连'))
    source.addEventListener('runtime', (event) => {
      try {
        const row: RuntimeEvent = JSON.parse((event as MessageEvent).data)
        setEvents((previous) =>
          previous.some((e) => e.id === row.id) ? previous : [...previous, row],
        )
      } catch {
        setConnection('事件格式错误，请重新加载')
      }
    })
    return () => source.close()
  }, [runId])
  return { events, connection }
}
