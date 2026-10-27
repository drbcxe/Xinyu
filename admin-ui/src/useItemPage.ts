import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './relationshipApi'
import type { Item } from './relationshipApi'

type Page = { items: Item[]; next_cursor: string | null }
export default function useItemPage(kind: Item['kind'] | null, version: string | undefined, query = '', status = 'all') {
  const [page, setPage] = useState<Page>({ items: [], next_cursor: null })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const key = `${kind}:${version}:${query}:${status}`
  const [loadedKey, setLoadedKey] = useState('')
  const path = useCallback((cursor?: string | null) => {
    const params = new URLSearchParams({ kind: kind ?? 'memory', q: query, status })
    if (cursor) params.set('cursor', cursor)
    return `/items?${params}`
  }, [kind, query, status])
  useEffect(() => {
    const stamp = ++generation.current
    if (!kind || !version) return
    const timer = setTimeout(() => {
      setLoading(true); setError(''); setPage({ items: [], next_cursor: null })
      void api<Page>(path()).then(next => { if (generation.current === stamp) { setPage(next); setLoadedKey(key) } }).catch(err => { if (generation.current === stamp) setError(err.message) }).finally(() => { if (generation.current === stamp) setLoading(false) })
    }, query ? 250 : 0)
    return () => { generation.current = stamp + 1; clearTimeout(timer) }
  }, [kind, version, query, status, path, key])
  async function loadMore() {
    if (!page.next_cursor || loading) return
    const stamp = generation.current
    setLoading(true); setError('')
    try {
      const next = await api<Page>(path(page.next_cursor))
      if (generation.current === stamp) setPage(previous => ({ items: [...previous.items, ...next.items].filter((item,index,array) => array.findIndex(row => row.id === item.id) === index), next_cursor:next.next_cursor }))
    } catch (err) { if (generation.current === stamp) setError((err as Error).message) }
    finally { if (generation.current === stamp) setLoading(false) }
  }
  return { items: loadedKey === key ? page.items : [], hasMore: loadedKey === key && !!page.next_cursor, loading, error, loadMore }
}
