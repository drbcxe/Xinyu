import { useEffect, useRef, useState } from 'react'
import { Link, Navigate, useOutletContext, useSearchParams } from 'react-router'

import { createThread, getArtifacts, getThreads } from '../api'
import AgentConversation from '../components/AgentConversation'
import type { Artifact, ChatThread, Project } from '../types'

interface Snapshot { key: string; threads: ChatThread[]; files: Artifact[]; error: string }

export default function ChatPage() {
  const project = useOutletContext<Project>()
  const [params, setParams] = useSearchParams()
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [revision, setRevision] = useState(0)
  const [creating, setCreating] = useState(false)
  const [creationError, setCreationError] = useState('')
  const [busy, setBusy] = useState(false)
  const creation = useRef<AbortController | null>(null)
  const key = `${project.id}:${revision}`
  const current = snapshot?.key === key ? snapshot : null
  const base = `/projects/${encodeURIComponent(project.id)}`
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const [threads, files] = await Promise.all([getThreads(project.id, controller.signal), getArtifacts(project.id, controller.signal)])
        if (!controller.signal.aborted) setSnapshot({ key, threads, files, error: '' })
      } catch (error) {
        if (!controller.signal.aborted) setSnapshot({ key, threads: [], files: [], error: error instanceof Error ? error.message : '无法打开对话页' })
      }
    }
    void load()
    return () => controller.abort()
  }, [project.id, key])
  useEffect(() => () => creation.current?.abort(), [])
  const threadId = params.get('thread')
  const selected = current?.threads.find((thread) => thread.id === threadId)
  const artifactParam = params.get('artifact')
  const artifactId = artifactParam !== null ? artifactParam === 'none' ? null : artifactParam : selected?.artifact_id ?? (current?.files.length === 1 ? current.files[0].id : null)
  const validArtifact = artifactId === null || Boolean(current?.files.some((file) => file.id === artifactId))

  async function newThread() {
    if (creation.current || !current || !validArtifact) return
    const controller = new AbortController()
    creation.current = controller
    setCreating(true)
    setCreationError('')
    try {
      const thread = await createThread(project.id, artifactId, controller.signal)
      if (controller.signal.aborted) return
      setSnapshot((previous) => previous?.key === key ? { ...previous, threads: [thread, ...previous.threads] } : previous)
      setParams({ thread: thread.id, artifact: artifactId ?? 'none' })
    } catch (error) {
      if (!controller.signal.aborted) setCreationError(error instanceof Error ? error.message : '无法创建会话')
    } finally {
      creation.current = null
      if (!controller.signal.aborted) setCreating(false)
    }
  }

  function updateTitle(message: string) {
    setSnapshot((previous) => previous ? { ...previous, threads: previous.threads.map((thread) => thread.id === threadId && thread.title === '新会话' ? { ...thread, title: message.replaceAll('\n', ' ').slice(0, 40) } : thread) } : previous)
  }

  if (current && !current.error && !threadId && current.threads.length > 0) {
    const first = current.threads[0]
    return <Navigate to={`${base}/chat?thread=${first.id}&artifact=${encodeURIComponent(artifactParam ?? first.artifact_id ?? 'none')}`} replace />
  }
  return (
    <>
      <div className="page-heading"><div><p className="eyebrow">RESEARCH WITH YOUR AGENT</p><h1>与心语对话</h1><p className="page-description">用自然语言提出问题，查看模型实际调用了哪些项目工具。</p></div><button className="primary-button new-chat-button" type="button" disabled={!current || Boolean(current.error) || creating || !validArtifact} onClick={() => { void newThread() }}>{creating ? '创建中…' : '＋ 新建对话'}</button></div>
      {!current ? <p className="route-message" role="status">正在读取会话与文件…</p>
        : current.error ? <div className="error-panel route-message" role="alert"><p>{current.error}</p><button className="text-button" type="button" onClick={() => setRevision((count) => count + 1)}>重新读取 →</button></div>
          : <>
            <div className="agent-file-picker"><label htmlFor="agent-artifact">当前文件</label><select id="agent-artifact" value={validArtifact ? artifactId ?? 'none' : ''} disabled={busy || creating} onChange={(event) => { const next = new URLSearchParams(params); next.set('artifact', event.target.value); setParams(next) }}>
              {!validArtifact && <option value="" disabled>请选择有效文件</option>}<option value="none">暂不选择文件 · 仅聊项目背景</option>{current.files.map((file) => <option key={file.id} value={file.id}>{file.original_name} · {file.row_count} 条实验 · {file.sha256.slice(0, 8)}</option>)}
            </select><Link className="text-button" to={`${base}/files`}>管理文件 →</Link><Link className="text-button" to={`${base}/memories`}>项目偏好 →</Link></div>
            {!validArtifact && <div className="error-panel" role="alert">地址中的文件不属于当前项目，请重新选择。</div>}
            {creationError && <div className="error-panel" role="alert">{creationError}</div>}
            <div className="agent-layout">
              <aside className="agent-threads" aria-label="项目会话"><div className="panel-title-row"><h2>对话记录</h2><button className="text-button" type="button" disabled={busy || creating} onClick={() => setRevision((count) => count + 1)}>刷新</button></div><ul>{current.threads.map((thread) => <li key={thread.id}><Link aria-current={selected?.id === thread.id ? 'page' : undefined} to={`${base}/chat?thread=${thread.id}&artifact=${thread.artifact_id ?? 'none'}`}><strong>{thread.title}</strong><small>{new Date(thread.updated_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</small></Link></li>)}</ul>{current.threads.length === 0 && <p className="field-hint">还没有会话，先新建一段对话。</p>}</aside>
              {selected && validArtifact ? <AgentConversation key={`${project.id}:${selected.id}`} projectId={project.id} threadId={selected.id} artifactId={artifactId} onBusyChange={setBusy} onMessageSent={updateTitle} />
                : <section className="agent-start"><span className="brand-mark" aria-hidden="true">X</span><h2>{threadId ? '找不到这段对话' : '让工具成为你的研究助手'}</h2><p>{threadId ? '请从左侧选择当前项目的会话，或新建对话。' : '新建对话后，可以让 Agent 查询实验、比较结果，并继续追问。'}</p><button className="primary-button" type="button" disabled={creating || !validArtifact} onClick={() => { void newThread() }}>开始新对话</button></section>}
            </div>
          </>}
    </>
  )
}
