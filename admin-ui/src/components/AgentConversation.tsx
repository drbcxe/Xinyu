import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import Markdown from 'react-markdown'

import { getChatMessages, postChatRun } from '../api'
import type { ChatMessages, ChatRun, ChatRunInput } from '../types'
import ComparisonResultCard from './ComparisonResultCard'
import Icon from './Icon'

const toolNames: Record<string, string> = { get_project_context: '读取项目背景', list_runs: '查询实验记录', compare_runs: '比较实验并保存结果', get_project_preferences: '读取项目偏好与版本', revise_memory: '保存项目偏好' }

function ToolTrace({ run }: { run: ChatRun }) {
  const started = run.events.filter((event) => event.type === 'tool.started')
  return (
    <details className="agent-tool-trace" open={run.status === 'running'}>
      <summary>{run.status === 'running' ? 'Agent 正在执行' : `本轮使用 ${started.length} 次工具`} · {run.model_name}</summary>
      {started.length === 0 && <p>{run.status === 'running' ? '正在等待模型答复或工具选择…' : '本轮未执行工具调用。'}</p>}
      <ol>{started.map((event) => {
        const finished = run.events.find((item) => item.type === 'tool.finished' && item.payload.tool_call_id === event.payload.tool_call_id)
        const name = String(event.payload.tool ?? '')
        return <li key={event.sequence}><strong>{toolNames[name] ?? name}</strong><span className={'badge ' + (finished ? finished.payload.ok ? 'green' : 'amber' : run.status === 'running' ? 'neutral' : 'red')}>
          {finished ? finished.payload.ok ? '完成' : '业务拒绝' : run.status === 'running' ? '执行中' : '已中断'}</span><p>{finished ? String(finished.payload.summary ?? '') : '请求已登记'}</p></li>
      })}</ol>
      {run.duration_ms !== null && <p>本轮耗时 {(run.duration_ms / 1000).toFixed(1)} 秒</p>}
    </details>
  )
}

function RunAttachments({ run }: { run: ChatRun }) {
  return <div className="agent-attachments"><ToolTrace run={run} />
    {run.error && <div className="error-panel" role="alert"><strong>本轮未完成</strong><p>{run.error.message}</p><small>{run.error.code}</small></div>}
    {run.evidence.map((result) => <ComparisonResultCard key={result.id} result={result} />)}
  </div>
}

interface Props {
  projectId: string
  threadId: string
  artifactId: string | null
  onBusyChange: (busy: boolean) => void
  onMessageSent: (message: string) => void
}

export default function AgentConversation({ projectId, threadId, artifactId, onBusyChange, onMessageSent }: Props) {
  const [snapshot, setSnapshot] = useState<ChatMessages | null>(null)
  const [readError, setReadError] = useState('')
  const [sendError, setSendError] = useState('')
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [revision, setRevision] = useState(0)
  const [optimistic, setOptimistic] = useState<ChatRunInput | null>(null)
  const [recoverable, setRecoverable] = useState<ChatRunInput | null>(null)
  const request = useRef<AbortController | null>(null)
  const form = useRef<HTMLFormElement>(null)
  const transcript = useRef<HTMLDivElement>(null)
  const stayAtBottom = useRef(true)
  const busy = sending || Boolean(snapshot?.active_run_id)

  useEffect(() => { onBusyChange(busy); return () => onBusyChange(false) }, [busy, onBusyChange])
  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let active = false
    async function load() {
      try {
        const messages = await getChatMessages(projectId, threadId, controller.signal)
        if (controller.signal.aborted) return
        setSnapshot(messages)
        setReadError('')
        active = Boolean(messages.active_run_id)
      } catch (error) {
        if (controller.signal.aborted) return
        setReadError(error instanceof Error ? error.message : '无法读取聊天记录')
      }
      if (!controller.signal.aborted && (active || request.current)) timer = setTimeout(() => { void load() }, 1500)
    }
    void load()
    return () => { controller.abort(); if (timer) clearTimeout(timer) }
  }, [projectId, threadId, revision])

  const eventCount = snapshot?.runs.reduce((count, run) => count + run.events.length, 0) ?? 0
  useEffect(() => {
    if (stayAtBottom.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight
  }, [snapshot?.items.length, eventCount, optimistic])

  async function send(input: ChatRunInput) {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setSending(true)
    setSendError('')
    setRecoverable(null)
    setOptimistic(input)
    setRevision((count) => count + 1)
    stayAtBottom.current = true
    try {
      await postChatRun(projectId, threadId, input, controller.signal)
      if (!controller.signal.aborted) onMessageSent(input.message)
    } catch (error) {
      if (!controller.signal.aborted) {
        setSendError(error instanceof Error ? error.message : '发送连接中断')
        setRecoverable(input)
      }
    } finally {
      request.current = null
      if (!controller.signal.aborted) { setSending(false); setRevision((count) => count + 1) }
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !snapshot || !draft.trim()) return
    const input = { message: draft.trim(), request_id: crypto.randomUUID(), artifact_id: artifactId }
    setDraft('')
    void send(input)
  }

  const hasPersistedOptimistic = optimistic && snapshot?.runs.some((run) => run.request_id === optimistic.request_id)
  const byRun = new Map(snapshot?.runs.map((run) => [run.id, run]))
  return (
    <section className="agent-conversation" aria-label="Agent 对话内容">
      <header className="conversation-header"><span className="conversation-logo"><Icon name="spark" /></span><div><strong>心语 Agent</strong><small>在数据与灵感之间，与你协作。</small></div><span className="conversation-mode">{busy ? '正在思考' : '研究助手'}</span></header>
      <div className="agent-transcript" ref={transcript} role="log" aria-live="polite" aria-relevant="additions text" onScroll={() => { const element = transcript.current; if (element) stayAtBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 120 }}>
        {!snapshot && !readError && <p className="artifact-empty" role="status">正在恢复聊天记录…</p>}
        {readError && <div className="error-panel" role="alert"><p>{readError}</p><button className="text-button" type="button" onClick={() => setRevision((count) => count + 1)}>重新读取 →</button></div>}
        {snapshot?.items.length === 0 && !optimistic && <div className="agent-welcome"><span className="welcome-spark"><Icon name="spark" /></span><p className="eyebrow">A LITTLE CURIOSITY GOES A LONG WAY</p><h2>今天，想探索什么？</h2><p>查询实验、比较结果，或者让我记住你的研究习惯。</p><div className="agent-suggestions">{['介绍一下当前项目', '列出当前文件中的实验', '以 R001 为基线，比较 R002 的改善幅度', '记住，这个项目默认以 R001 为基线'].map((text) => <button type="button" key={text} onClick={() => setDraft(text)}><span>{text}</span><Icon name="arrow" /></button>)}</div></div>}
        {snapshot?.items.map((message) => {
          const run = byRun.get(message.run_id)
          return <div key={message.id} className={'chat-turn ' + message.role}>
            <p className="chat-role">{message.role === 'assistant' && <Icon name="spark" />}{message.role === 'user' ? '你' : '心语 Agent'}</p>
            <div className="chat-bubble">{message.role === 'assistant' ? <Markdown skipHtml disallowedElements={['img']}>{message.content}</Markdown> : message.content}</div>
            {run && ((message.role === 'assistant') || (message.role === 'user' && run.status !== 'succeeded')) && <RunAttachments run={run} />}
          </div>
        })}
        {optimistic && !hasPersistedOptimistic && <div className="chat-turn user"><p className="chat-role">你 · 等待后端确认</p><div className="chat-bubble">{optimistic.message}</div></div>}
        {busy && <p className="agent-live-status" role="status">Agent 正在处理，本页会自动更新实际执行步骤…</p>}
      </div>
      {sendError && <div className="error-panel agent-send-error" role="alert"><p>{sendError}</p><p>请求可能已经被接收。恢复本次请求会沿用原编号，不会重复执行已经登记的请求。</p>{recoverable && <button className="text-button" type="button" disabled={sending} onClick={() => { void send(recoverable) }}>恢复本次请求 →</button>}</div>}
      <form className="agent-composer" ref={form} onSubmit={submit}>
        <label className="visually-hidden" htmlFor="agent-message">发送研究问题</label>
        <textarea id="agent-message" value={draft} disabled={busy} maxLength={8000} rows={3} placeholder="例如：以 R001 为基线，比较 R002，并说明来源。" onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); form.current?.requestSubmit() } }} />
        <div className="composer-actions"><p>{artifactId ? '将在当前选中的文件范围内使用工具' : '尚未选文件，可先询问项目背景'}<span>Enter 发送 · Shift + Enter 换行</span></p><button className="primary-button" type="submit" disabled={busy || !snapshot || !draft.trim()}>{busy ? '执行中…' : '发送给 Agent'}</button></div>
      </form>
    </section>
  )
}
