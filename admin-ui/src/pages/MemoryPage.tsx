import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useOutletContext } from 'react-router'

import { ApiError, deactivateMemory, getMemories, getMemoryHistory, saveMemory } from '../api'
import type { MemoryKey, Project, ProjectMemory } from '../types'

const definitions: { key: MemoryKey; title: string; hint: string; placeholder?: string; options?: string[] }[] = [
  { key: 'default_baseline', title: '默认基线', hint: '没有特别指定时优先采用此实验；比较前仍会检查当前文件是否包含它。', placeholder: '例如 R001' },
  { key: 'preferred_metric', title: '关注指标', hint: '用于提示你关注的指标，不会替换 CSV 中的实际指标或改变比较规则。', placeholder: '例如 RMSE' },
  { key: 'response_length', title: '回复长度', hint: '让 Agent 按适合你的详细程度组织回答。', options: ['简短', '适中', '详细'] },
  { key: 'report_language', title: '报告语言', hint: '新会话也会使用此语言；当前消息中明确指定的语言优先。', options: ['中文', '英文'] },
]

function MemoryCard({ definition, memory, disabled, onSave, onDeactivate, onHistory }: {
  definition: typeof definitions[number]; memory?: ProjectMemory; disabled: boolean
  onSave: (key: MemoryKey, value: string, revision: number) => void
  onDeactivate: (key: MemoryKey, revision: number) => void
  onHistory: (key: MemoryKey) => void
}) {
  const [value, setValue] = useState(memory?.is_active ? memory.value : '')
  const fieldId = `memory-${definition.key}`
  function submit(event: FormEvent) { event.preventDefault(); if (value.trim()) onSave(definition.key, value.trim(), memory?.revision ?? 0) }
  return <article className="memory-card">
    <div className="section-heading"><h2>{definition.title}</h2><span className={'badge ' + (memory?.is_active ? 'green' : 'neutral')}>{memory?.is_active ? '生效中' : memory ? '已停用' : '未设置'}</span></div>
    <p className="memory-hint">{definition.hint}</p>
    {memory?.is_active && <p className="memory-current">当前偏好：<strong>{memory.value}</strong></p>}
    <form onSubmit={submit}>
      <label htmlFor={fieldId}>{memory?.is_active ? '修改为' : '设置偏好'}</label>
      {definition.options ? <select id={fieldId} value={value} onChange={(event) => setValue(event.target.value)} disabled={disabled} required><option value="">请选择</option>{definition.options.map((option) => <option key={option}>{option}</option>)}</select>
        : <input id={fieldId} value={value} onChange={(event) => setValue(event.target.value)} maxLength={100} placeholder={definition.placeholder} disabled={disabled} required />}
      <div className="page-actions"><button type="submit" className="primary-button" disabled={disabled || !value.trim() || (memory?.is_active && value.trim() === memory.value)}>保存偏好</button>
        {memory?.is_active && <button type="button" className="refresh-button" disabled={disabled} onClick={() => onDeactivate(definition.key, memory.revision)}>停用偏好</button>}</div>
    </form>
    {memory && <div className="memory-source"><p>版本 {memory.revision} · {memory.source === 'agent' ? '由聊天工具保存' : '在偏好页设置'} · {new Date(memory.created_at).toLocaleString()}</p>
      {memory.source_quote && <blockquote>{memory.source_quote}</blockquote>}
      <button className="text-button" type="button" disabled={disabled} onClick={() => onHistory(definition.key)}>查看修订记录 →</button></div>}
  </article>
}

function MemoryHistory({ projectId, memoryKey }: { projectId: string; memoryKey: MemoryKey }) {
  const [rows, setRows] = useState<ProjectMemory[] | null>(null)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void getMemoryHistory(projectId, memoryKey, controller.signal).then((items) => { if (!controller.signal.aborted) { setRows(items); setError('') } }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取失败') })
    return () => controller.abort()
  }, [projectId, memoryKey, reload])
  return <section className="memory-history" aria-label="偏好修订记录"><h2>{definitions.find((item) => item.key === memoryKey)?.title} · 最近 50 个版本</h2>
    {error && <div role="alert" className="error-panel">{error}<button type="button" className="text-button" onClick={() => setReload((n) => n + 1)}>重新读取 →</button></div>}
    {!rows && !error && <p role="status">正在读取修订记录…</p>}
    <ol>{rows?.map((row, index) => <li key={row.id}><strong>版本 {row.revision} · {row.value}</strong><span className={'badge ' + (row.is_active ? 'green' : 'neutral')}>{row.is_active ? '生效中' : index === 0 ? '已停用' : '历史版本'}</span><p>{new Date(row.created_at).toLocaleString()} · {row.source === 'agent' ? '来自用户聊天' : '来自偏好页'}{row.supersedes_id ? ` · 接续版本 ${row.revision - 1}` : ' · 首次设置'}</p>{row.source_quote && <blockquote>{row.source_quote}</blockquote>}</li>)}</ol>
  </section>
}

function MemoryWorkspace({ project }: { project: Project }) {
  const [version, setVersion] = useState(0)
  const [snapshot, setSnapshot] = useState<{ version: number; items: ProjectMemory[]; error: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [historyKey, setHistoryKey] = useState<MemoryKey | null>(null)
  const mutation = useRef<AbortController | null>(null)
  useEffect(() => () => mutation.current?.abort(), [])
  useEffect(() => {
    const controller = new AbortController()
    void getMemories(project.id, controller.signal).then((items) => { if (!controller.signal.aborted) setSnapshot({ version, items, error: '' }) }).catch((reason: unknown) => { if (!controller.signal.aborted) setSnapshot({ version, items: [], error: reason instanceof Error ? reason.message : '无法读取偏好' }) })
    return () => controller.abort()
  }, [project.id, version])
  const loading = snapshot?.version !== version
  async function update(key: MemoryKey, value: string | null, revision: number) {
    if (mutation.current) return
    const controller = new AbortController()
    mutation.current = controller
    setBusy(true); setError(''); setNotice('')
    try {
      if (value === null) await deactivateMemory(project.id, key, revision, controller.signal)
      else await saveMemory(project.id, key, value, revision, controller.signal)
      if (!controller.signal.aborted) setNotice(value === null ? '偏好已停用，后续模型调用不再召回该值。原始聊天与修订记录仍保留。' : '偏好已保存，当前项目的新会话和后续模型调用都能读取。')
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof ApiError && reason.code === 'REVISION_CONFLICT' ? '这项偏好已有更新，已重新读取最新版本。请确认后再修改。' : reason instanceof Error ? reason.message : '保存失败，请刷新确认当前状态')
    } finally {
      mutation.current = null
      if (!controller.signal.aborted) { setBusy(false); setVersion((n) => n + 1) }
    }
  }
  return <div className="memory-page"><div className="page-heading"><div><p className="eyebrow">PROJECT PREFERENCES</p><h1>项目偏好</h1><p className="page-description">让心语记住你的工作习惯，在这个项目的不同会话中继续使用。</p></div><button className="refresh-button" type="button" disabled={busy || loading} onClick={() => { setNotice(''); setVersion((n) => n + 1) }}>刷新偏好</button></div>
    <div className="memory-explainer"><p>可以直接对 Agent 说：“记住，这个项目默认以 R001 为基线，回复请简短。”</p><p>偏好仅属于 {project.id}。停用会停止召回，保留原始聊天与修订记录。<Link to={`/projects/${encodeURIComponent(project.id)}/chat`}>前往 Agent 对话 →</Link></p></div>
    {notice && <p className="memory-notice" role="status">{notice}</p>}
    {error && <div className="error-panel" role="alert">{error}</div>}
    {loading && <p role="status">正在读取项目偏好…</p>}
    {!loading && snapshot?.error && <div className="error-panel" role="alert">{snapshot.error}<p>点击“刷新偏好”重试。</p></div>}
    {!loading && !snapshot?.error && <div className="memory-grid">{definitions.map((definition) => {
      const memory = snapshot?.items.find((row) => row.key === definition.key)
      return <MemoryCard key={`${definition.key}:${memory?.revision ?? 0}`} definition={definition} memory={memory} disabled={busy || loading} onSave={(key, value, revision) => { void update(key, value, revision) }} onDeactivate={(key, revision) => { void update(key, null, revision) }} onHistory={setHistoryKey} />
    })}</div>}
    {historyKey && <MemoryHistory key={`${historyKey}:${version}`} projectId={project.id} memoryKey={historyKey} />}
  </div>
}

export default function MemoryPage() {
  const project = useOutletContext<Project>()
  return <MemoryWorkspace key={project.id} project={project} />
}
