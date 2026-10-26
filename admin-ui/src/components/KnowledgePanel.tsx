import { useCallback, useEffect, useState } from 'react'
import { api } from '../relationshipApi'
import type { RagSource, Retrieval } from '../relationshipApi'
import Icon from './Icon'
import './KnowledgePanel.css'

type Document = { id: string; title: string; kind: string; status: string; revision: number; chunks: number; error: string }
type IndexStatus = { engine: string; embedding_model: string; embedding_dimensions: number; chunk_size: number; chunk_overlap: number; counts: Record<string, number>; chunks: number; documents: Document[] }
const statusNames: Record<string, string> = { pending: '等待索引', indexing: '正在处理', ready: '可以检索', failed: '需要重试', deleting: '正在移除', delete_failed: '等待移除重试' }

export function SourceCards({ sources }: { sources: RagSource[] }) {
  return <div className="knowledge-sources">{sources.map((source, index) => <article className="knowledge-source" key={source.citation_id}>
    <header><span>{String(index + 1).padStart(2, '0')}</span><strong>{source.title}</strong><small>{source.kind === 'memory' ? '已确认偏好' : '导入聊天'}</small></header>
    <blockquote>{source.quote}</blockquote>
    <footer><span>修订 {source.revision} · 原文第 {source.offset + 1}–{source.end_offset} 字符</span><span>{source.retrievers.map(name => name === 'vector' ? '语义匹配' : '关键词命中').join(' + ')}</span></footer>
  </article>)}</div>
}

export default function KnowledgePanel() {
  const [status, setStatus] = useState<IndexStatus | null>(null)
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<Retrieval | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [searching, setSearching] = useState(false)
  const refresh = useCallback(async () => { setStatus(await api<IndexStatus>('/knowledge')) }, [])

  useEffect(() => {
    let active = true
    const load = () => { void api<IndexStatus>('/knowledge').then(value => { if (active) setStatus(value) }).catch(err => { if (active) setError(err.message) }) }
    load()
    const timer = setInterval(load, 5000)
    return () => { active = false; clearInterval(timer) }
  }, [])

  async function action(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await work(); await refresh() } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  async function search() {
    if (!query.trim() || searching) return
    setSearching(true); setError(''); setResult(null)
    try { setResult(await api<Retrieval>('/knowledge/search', 'POST', { query: query.trim() })) }
    catch (err) { setError((err as Error).message) } finally { setSearching(false) }
  }

  return <section className="love-library knowledge-panel">
    <div className="love-section-head"><div><div className="love-eyebrow">A LITTLE CONTEXT GOES A LONG WAY</div><h1>先找回原文，再给建议。</h1><p>已确认的偏好与主动导入的聊天，组成只属于你的参考资料。</p></div><label className={`knowledge-import ${busy ? 'disabled' : ''}`}><Icon name="upload" />导入聊天<input hidden type="file" accept=".txt,.json" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (file) void action(async () => { const body = new FormData(); body.append('file', file); await api('/imports', 'POST', body); setNotice('已导入，后台正在建立语义索引。') }); event.target.value = '' }} /></label></div>
    {error && <div className="love-alert" role="alert">{error}</div>}{notice && <div className="love-notice" role="status">{notice}</div>}
    <div className="knowledge-summary"><div><span>可以检索的资料</span><strong>{status ? status.counts.ready ?? 0 : '—'}<small>份</small></strong></div><div><span>已建立的原文片段</span><strong>{status?.chunks ?? '—'}<small>段</small></strong></div><div><span>等待 / 正在索引</span><strong>{status ? (status.counts.pending ?? 0) + (status.counts.indexing ?? 0) : '—'}<small>份</small></strong></div><div><span>需要处理</span><strong>{status ? status.counts.failed ?? 0 : '—'}<small>份</small></strong></div></div>
    <div className="knowledge-search-box"><div><Icon name="search" /><h2>换个说法，也能找回那句话。</h2></div><p>例如原文说“不太能吃辣”，可以试着问“约会吃饭要避开什么？”</p><form onSubmit={event => { event.preventDefault(); void search() }}><input aria-label="检索参考资料" maxLength={2000} value={query} onChange={event => setQuery(event.target.value)} placeholder="想找回哪一段聊天，或者哪一个偏好？" /><button className="love-primary" disabled={searching || !query.trim()}>{searching ? '正在检索…' : '检索资料'}<Icon name="arrow" /></button></form><small>只在你的资料中检索；这里展示原文，不生成回复。</small></div>
    {result && <div className="knowledge-results"><div className="knowledge-list-heading"><h2>检索到 {result.sources.length} 段参考</h2><span>{result.mode === 'hybrid' ? '语义 + 关键词' : result.mode === 'vector' ? '语义检索' : result.mode === 'empty' ? '暂无资料' : '关键词检索'} · {result.milliseconds} ms</span></div>{result.warnings.map(warning => <p className="knowledge-warning" key={warning}>{warning}</p>)}{result.sources.length ? <SourceCards sources={result.sources} /> : <div className="love-empty love-empty-compact"><p>暂未找到相关原文，可以换个问法，或先添加偏好、导入聊天。</p></div>}</div>}
    <div className="knowledge-list-heading"><h2>我的参考资料</h2><button disabled={busy || !status?.documents.length} onClick={() => void action(async () => { const response = await api<{ queued: number }>('/knowledge/reindex', 'POST'); setNotice(`已安排 ${response.queued} 份资料重新索引。`) })}>重新建立索引 ↻</button></div>
    {!status ? <p className="knowledge-caption">正在读取索引状态…</p> : !status.documents.length ? <div className="love-empty"><span className="love-empty-icon"><Icon name="memory" /></span><h3>有些小事，值得一直记得。</h3><p>在“关于 TA”添加已确认偏好，或导入 UTF-8 TXT / JSON 聊天文件，后台会自动建立索引。</p></div> : <div className="knowledge-document-list">{status.documents.map(document => <article key={document.id}><span className="knowledge-document-icon"><Icon name={document.kind === 'memory' ? 'heart' : 'chat'} /></span><div><h3>{document.title}</h3><p>{document.kind === 'memory' ? '已确认偏好' : '聊天原文'} · 修订 {document.revision}{document.chunks > 0 ? ` · ${document.chunks} 段` : ''}</p>{document.error && <small className="knowledge-warning">{document.error}</small>}</div><span className={`knowledge-index-state ${document.status}`}>{statusNames[document.status] ?? document.status}</span>{document.status === 'failed' && <button disabled={busy} onClick={() => void action(async () => { await api(`/knowledge/${document.id}/retry`, 'POST'); setNotice('已重新安排索引。') })}>重试</button>}</article>)}</div>}
    {status && <div className="knowledge-engine-note"><Icon name="shield" /><span>资料按个人空间隔离。修改或删除资料后会同步更新索引。</span><details><summary>检索配置</summary><p>{status.engine} · {status.embedding_model} · {status.embedding_dimensions} 维 · 分块 {status.chunk_size} 字符 / 重叠 {status.chunk_overlap} 字符。回答时可使用语义重排。</p></details></div>}
  </section>
}
