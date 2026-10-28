import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { NavLink, useLocation } from 'react-router'
import ReactMarkdown from 'react-markdown'
import Icon from '../components/Icon'
import AuthDialog from '../components/AuthDialog'
import SettingsPanel from '../components/SettingsPanel'
import { getSettingsSection } from '../settingsSections'
import PrivacyDialog from '../components/PrivacyDialog'
import FeedbackControls from '../components/FeedbackControls'
import KnowledgePanel, { SourceCards } from '../components/KnowledgePanel'
import { api, ApiError, localDate, toolNames } from '../relationshipApi'
import type { Candidate, Item, Job, Plan, State, Turn } from '../relationshipApi'
import useItemPage from '../useItemPage'
import './RelationshipPage.css'

type Draft = { kind: 'memory' | 'reminder'; title: string; quote: string; due_at: string; source_turn_id?: string; id?: string }
const labels = { chat: '聊一聊', memories: '关于 TA', reminders: '重要事项', dates: '约会规划', knowledge: '资料检索', settings: '偏好与设置' }
const graphLabels: Record<string, string> = { load_context: '读取个人背景', retrieve_context: '检索参考资料', call_model: '模型理解与生成', execute_tools: '执行工具', validate_answer: '校验结果与来源', finalize: '整理并保存结果' }

function privacyScope(value: State) { return `${value.account.username ?? 'guest'}:${value.privacy.version}:${value.privacy.provider_fingerprint}` }

export default function RelationshipPage() {
  const { pathname } = useLocation()
  const rootPage = pathname.split('/').filter(Boolean)[1]
  const tab = rootPage === 'memories' ? 'memories' : rootPage === 'reminders' ? 'reminders' : rootPage === 'dates' ? 'dates' : rootPage === 'knowledge' ? 'knowledge' : rootPage === 'settings' ? 'settings' : 'chat'
  const settingsSection = getSettingsSection(pathname)
  const [state, setState] = useState<State | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [profile, setProfile] = useState({ partner_name: '', style: '' })
  const [privacyManualFor, setPrivacyManualFor] = useState<string | null>(null)
  const [privacyDismissedFor, setPrivacyDismissedFor] = useState<string | null>(null)
  const [older, setOlder] = useState<Turn[]>([])
  const [hasOlder, setHasOlder] = useState(true)
  const olderLoaded = useRef(false)
  const identity = useRef<string | null | undefined>(undefined)
  const [authMode, setAuthMode] = useState<'login' | 'register' | null>(null)
  const [images, setImages] = useState<{ name: string; url: string }[]>([])
  const [transcript, setTranscript] = useState<string | null>(null)
  const [rawDocument, setRawDocument] = useState<{ title: string; text: string } | null>(null)
  const [planForm, setPlanForm] = useState({ city: '', budget: '', when: '', preferences: '' })
  const [memoryQuery, setMemoryQuery] = useState('')
  const [itemFilter, setItemFilter] = useState('all')
  const endRef = useRef<HTMLDivElement>(null)
  const requestKey = useRef<string | null>(null)
  const notified = useRef(new Set<string>())
  const modalOpen = Boolean(draft || authMode || transcript !== null || rawDocument)
  const stateVersion = useRef<string | null>(null)
  const refreshing = useRef(false)
  const collectionKind = tab === 'memories' ? 'memory' : tab === 'reminders' ? 'reminder' : tab === 'dates' ? 'date_plan' : tab === 'settings' && settingsSection.key === 'data' ? 'chat_log' : null
  const collection = useItemPage(collectionKind, state?.version, tab === 'memories' ? memoryQuery : '', tab === 'reminders' ? itemFilter : 'all')
  const refresh = useCallback(async () => {
    const next = await api<State>('/state')
    if (identity.current !== undefined && identity.current !== next.account.username) {
      setOlder([]); olderLoaded.current = false; setRawDocument(null); setTranscript(null); setDraft(null); setText(''); setImages([])
    }
    identity.current = next.account.username
    if (!next.turns.length) { setOlder([]); olderLoaded.current = false }
    if (!olderLoaded.current) setHasOlder(next.history_has_more)
    stateVersion.current = next.version
    setState(next)
    return next
  }, [])
  const reloadSpace = useCallback(async () => {
    const next = await refresh()
    setPrivacyManualFor(null); setPrivacyDismissedFor(null)
    olderLoaded.current = false
    setProfile(next.profile); setOlder([]); setHasOlder(next.history_has_more)
    setText(''); setImages([]); setDraft(null); requestKey.current = null
  }, [refresh])

  useEffect(() => {
    let active = true
    api<State>('/state').then(next => { if (active) { identity.current = next.account.username; stateVersion.current = next.version; setState(next); setProfile(next.profile); setHasOlder(next.history_has_more) } }).catch(err => { if (active) setError(err.message) })
    const poll = async () => {
      if (!active || refreshing.current || document.hidden) return
      refreshing.current = true
      try {
        const summary = await api<Pick<State, 'version' | 'jobs' | 'worker_online' | 'server_time'>>('/state/summary')
        if (!active) return
        if (summary.version !== stateVersion.current) await refresh()
        else setState(previous => previous ? { ...previous, jobs: summary.jobs, worker_online: summary.worker_online, server_time: summary.server_time } : previous)
      } catch { /* The next bounded poll retries; existing content stays visible. */ }
      finally { refreshing.current = false }
    }
    const timer = setInterval(() => { void poll() }, 3000)
    const visible = () => { if (!document.hidden) void poll() }
    document.addEventListener('visibilitychange', visible)
    return () => { active = false; clearInterval(timer); document.removeEventListener('visibilitychange', visible) }
  }, [refresh])
  useEffect(() => { document.title = `${tab === 'settings' ? settingsSection.label + ' · 设置' : labels[tab]} · 心语 Xinyu` }, [tab, settingsSection.label])
  useEffect(() => {
    if (!modalOpen) return
    const previous = document.activeElement as HTMLElement | null
    const dialog = document.querySelector<HTMLElement>('.love-modal')
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), a[href]') ?? [])
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !saving) { setDraft(null); setAuthMode(null); setTranscript(null); setRawDocument(null) }
      if (event.key !== 'Tab') return
      const elements = focusable(); const first = elements[0]; const last = elements.at(-1)
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    if (!dialog?.contains(document.activeElement)) focusable()[0]?.focus()
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); previous?.focus() }
  }, [modalOpen, saving])
  useEffect(() => {
    if (tab === 'chat' && state?.turns.length) endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [state?.turns.length, tab])
  useEffect(() => {
    if (!state) return
    for (const message of state.notices) {
      if (notified.current.has(message.id)) continue
      notified.current.add(message.id)
      if (message.status === 'unread' && 'Notification' in window && Notification.permission === 'granted' && localStorage.getItem('xinyu-notifications') === 'enabled') {
        new Notification('心语 · 重要事项', { body: message.title, tag: message.id })
      }
    }
  }, [state])

  const pending = state?.jobs.filter(job => job.status === 'queued' || job.status === 'running') ?? []
  const busy = submitting || pending.length > 0
  const aiEnabled = Boolean(state?.privacy?.accepted)
  const privacyKey = state?.privacy ? privacyScope(state) : ''
  const privacyAutoOpen = Boolean(state?.privacy && !aiEnabled && (tab === 'chat' || tab === 'dates') && privacyDismissedFor !== privacyKey)
  const privacyOpen = Boolean(state?.privacy && !modalOpen && (privacyManualFor === privacyKey || privacyAutoOpen))
  function closePrivacy() { setPrivacyManualFor(null); setPrivacyDismissedFor(privacyKey) }
  async function handleFailure(err: unknown) {
    if (err instanceof ApiError && err.code === 'MODEL_CONSENT_REQUIRED') {
      try { const next = await refresh(); setPrivacyManualFor(privacyScope(next)) }
      catch { setError('暂时无法读取数据使用说明，请刷新后重试。') }
      return
    }
    setError((err as Error).message)
  }
  const unread = state?.notices.filter(message => message.status === 'unread') ?? []
  const allTurns = [...older, ...(state?.turns ?? [])].filter((turn, index, array) => array.findIndex(t => t.id === turn.id) === index)

  async function task(action: () => Promise<void>) {
    setSaving(true); setError('')
    try { await action() } catch (err) { await handleFailure(err) } finally { setSaving(false) }
  }

  async function send(content: string, mode: 'chat' | 'date' = 'chat') {
    if (!content.trim() || busy) return
    if (!aiEnabled) { setPrivacyManualFor(privacyKey); return }
    setSubmitting(true); setError(''); setNotice('')
    requestKey.current ??= crypto.randomUUID()
    try {
      await api<Job>('/runs', 'POST', { request_id: requestKey.current, content: content.trim(), mode })
      requestKey.current = null
      setText(''); await refresh()
      setNotice('任务已交给后台，关闭页面后仍可继续；结果会保存在对话中。')
    } catch (err) { await handleFailure(err) } finally { setSubmitting(false) }
  }

  async function saveDraft(event: FormEvent) {
    event.preventDefault()
    if (!draft) return
    await task(async () => {
      await api(draft.id ? `/items/${draft.id}` : '/items', draft.id ? 'PUT' : 'POST', {
        ...draft, title: draft.title.trim(), due_at: draft.kind === 'reminder' ? new Date(draft.due_at).toISOString() : null, status: 'active',
      })
      setDraft(null); setNotice(draft.kind === 'memory' ? '偏好已保存，后台会更新检索索引。' : '已保存。后台会在到期时生成提醒。'); await refresh()
    })
  }

  async function changeItem(item: Item, action: 'completed' | 'cancelled' | 'delete') {
    await task(async () => {
      await api(`/items/${item.id}`, action === 'delete' ? 'DELETE' : 'PUT', action === 'delete' ? undefined : { title: item.title, due_at: item.due_at, status: action })
      await refresh()
    })
  }

  async function readImages(files: FileList | null) {
    if (!files) return
    await task(async () => {
      if (images.length + files.length > 2) throw new Error('每次最多两张截图')
      const added = await Promise.all(Array.from(files).map(async file => {
        if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 4 * 1024 * 1024) throw new Error('请选择 4 MB 以内的 PNG 或 JPEG 图片')
        const url = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(file) })
        return { name: file.name, url }
      }))
      setImages(current => [...current, ...added])
    })
  }

  async function transcribeImages() {
    if (!aiEnabled) { setPrivacyManualFor(privacyKey); return }
    await task(async () => {
      const result = await api<{ transcript: string }>('/transcribe', 'POST', { images: images.map(image => image.url) })
      setTranscript(result.transcript)
    })
  }

  function openCandidate(candidate: Candidate, turn: Turn) {
    setError(''); setDraft({ ...candidate, source_turn_id: turn.id, due_at: '' })
  }

  async function savePlan(plan: Plan, turn: Turn, index: number) {
    await task(async () => {
      await api('/items', 'POST', { kind: 'date_plan', title: plan.title, source_turn_id: turn.id, plan_index: index })
      setNotice('方案已保存，可以在约会规划页查看。'); await refresh()
    })
  }

  function renderTurn(turn: Turn) {
    return <article key={turn.id} className="love-turn">
      <div className="love-user"><span>你 · {new Date(turn.created_at).toLocaleString('zh-CN')}</span><p>{turn.content}</p></div>
      <div className="love-answer">
        <div className="love-answer-label">✦ 心语</div>
        {!!turn.result.graph_trace?.length && <details className="love-graph-trace"><summary>查看执行过程 · {turn.result.graph_trace.length} 个步骤{turn.result.execution?.resumed ? ' · 已恢复执行' : ''}</summary><ol>{turn.result.graph_trace.map((step, index) => <li key={index}><span>{String(index + 1).padStart(2, '0')}</span><strong>{graphLabels[step.node] ?? step.node}{step.valid === false ? ' · 重新生成' : ''}{step.sources !== undefined ? ` · ${step.sources} 段参考` : ''}</strong><small>{step.milliseconds} ms</small></li>)}</ol><p>{turn.result.execution?.engine} · {turn.result.execution?.checkpointed ? '执行检查点已保存' : ''} · {turn.result.execution?.model_steps} 次模型调用 · {turn.result.execution?.tool_calls} 次工具调用{turn.result.execution?.usage.total_tokens ? ` · 主模型 ${turn.result.execution.usage.total_tokens} Token（不含重排）` : ''}</p>{[...new Set(turn.result.retrieval_trace?.flatMap(trace => trace.warnings) ?? [])].map(warning => <p className="knowledge-warning" key={warning}>{warning}</p>)}</details>}
        {!!turn.result.tool_trace?.length && <details className="love-tool-trace"><summary>查看实际工具执行 · {turn.result.tool_trace.length} 步</summary>{turn.result.tool_trace.map((trace, index) => <div key={index}><strong>{toolNames[trace.tool] ?? trace.tool}</strong><small>{trace.status === 'succeeded' ? '已完成' : '已拒绝'} · {trace.milliseconds} ms</small><pre>{JSON.stringify(trace.result, null, 2)}</pre></div>)}</details>}
        <ReactMarkdown>{turn.result.analysis}</ReactMarkdown>
        {!!turn.result.sources?.length && <div className="love-source-section"><h3>本次回答参考的原文</h3><SourceCards sources={turn.result.sources} /></div>}
        {turn.result.uncertainty && <p className="love-uncertainty">需要留意：{turn.result.uncertainty}</p>}
        {turn.result.replies.map((reply, index) => <div className="love-reply" key={index}><small>表达建议 {index + 1}</small><p>{reply}</p><button onClick={() => { void navigator.clipboard.writeText(reply).then(() => setNotice('已复制，可以修改后再发给对方。')).catch(() => setError('复制失败，请手动选择文字复制')) }}>复制回复</button></div>)}
        {!!turn.result.candidates.length && <div className="love-candidates"><small>确认后才会保存</small>{turn.result.candidates.map((candidate, index) => <button disabled={saving} key={index} onClick={() => openCandidate(candidate, turn)}><span>{candidate.kind === 'memory' ? '◇ 偏好' : '◷ 事项'} · {candidate.title}</span><b>确认 →</b></button>)}</div>}
        {turn.result.date_plans?.map((plan, index) => <div className="love-reply love-plan" key={index}><h3>{plan.title}</h3><small>参考预算：{plan.budget}</small><ol>{plan.schedule.map((entry, i) => <li key={i}>{entry}</li>)}</ol><p>{plan.reason}</p>{!!plan.needs_confirmation.length && <p className="love-uncertainty">待核实：{plan.needs_confirmation.join('；')}</p>}<button disabled={saving} onClick={() => void savePlan(plan, turn, index)}>保存这个方案</button></div>)}
        <FeedbackControls turnId={turn.id} />
      </div>
    </article>
  }

  async function loadOlder() {
    await task(async () => {
      const first = allTurns[0]
      if (!first) return
      const rows = await api<Turn[]>(`/history?before=${encodeURIComponent(first.created_at)}&before_id=${encodeURIComponent(first.id)}`)
      olderLoaded.current = true
      setOlder(current => [...rows, ...current]); setHasOlder(rows.length === 50)
    })
  }

  const items = collection.items
  const dateTurns = allTurns.filter(turn => !!turn.result.date_plans?.length)
  const plans = collection.items
  const imports = collection.items

  return <div className={`love-app love-view-${tab}`}>
    <aside className="love-sidebar">
      <NavLink to="/assistant" className="love-brand"><img src="/xinyu.svg" alt="" /><div>心语<small>XINYU</small></div></NavLink>
      <div className="love-nav-label">个人工作空间</div>
      <nav aria-label="恋爱助手导航">
        <NavLink end to="/assistant"><Icon name="chat" /><span>聊一聊</span></NavLink>
        <NavLink to="/assistant/memories"><Icon name="heart" /><span>关于 TA</span></NavLink>
        <NavLink to="/assistant/reminders"><Icon name="bell" /><span>重要事项</span>{unread.length > 0 && <b>{unread.length}</b>}</NavLink>
        <NavLink to="/assistant/dates"><Icon name="calendar" /><span>约会规划</span></NavLink>
        <NavLink to="/assistant/knowledge"><Icon name="search" /><span>资料检索</span></NavLink>
        <NavLink to="/assistant/settings"><Icon name="settings" /><span>偏好与设置</span></NavLink>
      </nav>
      <div className="love-sidebar-bottom"><div className="love-side-note"><Icon name="spark" /><p>让在意，有回应。</p><small>懂得表达，也记得行动。</small><NavLink to="/assistant/memories">看看已记住的事 <Icon name="arrow" /></NavLink></div><div className="love-side-identity"><span className="love-avatar">{state?.account.username?.slice(0, 1).toUpperCase() ?? 'U'}</span><div><strong>{state?.account.username ?? '我的个人空间'}</strong><small>{state?.account.username ? '已登录账号' : '访客模式 · 可注册保存'}</small></div></div></div>
    </aside>
    <main className="love-main">
      <header className="love-top"><span>个人空间 <i>/</i> <strong>{tab === 'settings' ? '设置 · ' + settingsSection.label : labels[tab]}</strong></span><div className="love-top-actions">{state?.privacy && <button className="privacy-entry" aria-label="打开数据使用说明与授权设置" onClick={() => setPrivacyManualFor(privacyKey)}><Icon name="shield" />数据使用</button>}<span className={`love-service-state ${state?.worker_online ? 'online' : ''}`}><span />{state?.worker_online ? '助手在线' : '后台离线'}</span><button className="love-account-button" onClick={() => setAuthMode('login')}><Icon name="user" />{state?.account.username ? '账号管理' : '登录 / 注册'}</button></div></header>
      <div className="love-content">
        {error && <div className="love-alert" role="alert">{error}<button onClick={() => setError('')}>关闭</button></div>}
        {notice && <div className="love-notice" role="status">{notice}<button onClick={() => setNotice('')}>关闭</button></div>}
        {collectionKind && collection.error && <div className="love-alert" role="alert">{collection.error}</div>}
        {collectionKind && collection.loading && <p className="knowledge-caption" role="status">正在读取资料…</p>}
        {state && !state.worker_online && <div className="love-alert">后台任务未启动，分析和到期提醒暂时不会执行。请启动 scripts/worker.py。</div>}
        {!!unread.length && tab !== 'reminders' && <NavLink className="love-due" to="/assistant/reminders">◷ 有 {unread.length} 条未读提醒：{unread[0].title}</NavLink>}
        {!state && <div className="love-empty"><p>正在连接你的空间…</p><button onClick={() => { void reloadSpace().catch(err => setError(err.message)) }}>重新连接</button></div>}
        {state && tab === 'knowledge' && <KnowledgePanel key={state.account.username ?? 'guest'} />}

        {state && tab === 'chat' && <>
          {!allTurns.length && <section className="love-welcome"><div className="love-hero-main"><div className="love-hero-copy"><div className="love-hero-pill"><Icon name="spark" />为真实的关系，多想一步</div><h1>好好沟通，<br /><span>从这里开始。</span></h1><p>一起读懂聊天里的在意，<br />找到自然的表达，记住值得用心的小事。</p></div><div className="love-hero-art" aria-hidden="true"><div className="love-art-orbit" /><div className="love-art-orbit second" /><div className="love-art-heart"><Icon name="heart" /></div><span className="love-art-chip first"><Icon name="chat" />理解语境</span><span className="love-art-chip second"><Icon name="memory" />记住偏好</span><i className="love-art-dot one" /><i className="love-art-dot two" /></div></div><div className="love-starters">{[{ title: '这句话，怎么回？', detail: '理清语境，找到自然表达', icon: 'chat' as const, value: '帮我看看这段聊天，怎么回复更自然？' }, { title: '把误会，好好说开', detail: '理解分歧，表达真实想法', icon: 'heart' as const, value: '我想解释一次误会，但不知道怎么开口。' }, { title: '重要的事，别忘了', detail: '记下约定，把关心放进行动', icon: 'bell' as const, value: '她下周有重要面试，我想记下来。' }].map(item => <button key={item.title} onClick={() => { setText(item.value); requestKey.current = null }}><span className="love-starter-icon"><Icon name={item.icon} /></span><strong>{item.title}</strong><small>{item.detail}</small><Icon name="arrow" /></button>)}</div></section>}
          {!!allTurns.length && hasOlder && <button className="love-load-more" disabled={saving} onClick={() => void loadOlder()}>加载更早的咨询</button>}
          <div className="love-conversation" aria-live="polite">{allTurns.map(renderTurn)}</div>
          {state.jobs.filter(job => job.status !== 'succeeded').slice(0, 4).map(job => <div className={`love-job ${job.status}`} key={job.id}><span>{job.status === 'queued' ? job.capacity_wait_started_at ? '◷ 助手繁忙，任务已排队等待' : job.error ? `◷ 正在等待重试：${job.error}` : '◷ 等待后台处理' : job.status === 'running' ? `✦ ${graphLabels[job.progress?.node ?? ''] ?? '准备执行'}${job.progress?.model_step ? ` · 第 ${job.progress.model_step} 轮模型调用` : ''}…` : job.error}</span>{(job.status === 'queued' || job.status === 'running') && <button disabled={saving} onClick={() => void task(async () => { await api(`/runs/${job.id}/cancel`, 'POST'); await refresh(); setNotice('已取消。已经发出的模型请求可能仍产生用量。') })}>取消任务</button>}{job.status === 'failed' && <button disabled={saving} onClick={() => { if (!aiEnabled) { setPrivacyManualFor(privacyKey); return }; void task(async () => { await api(`/runs/${job.id}/retry`, 'POST'); await refresh() }) }}>重试</button>}</div>)}
          <div ref={endRef} />
          <form className={`love-composer${allTurns.length ? ' has-history' : ''}`} onSubmit={event => { event.preventDefault(); void send(text) }}>
            <div className="love-composer-heading"><Icon name="spark" /><span>说说发生了什么</span><small>你的私人沟通助手</small></div><label htmlFor="love-input" className="love-sr-only">聊天内容</label><textarea id="love-input" rows={3} value={text} disabled={busy} maxLength={16000} onChange={event => { setText(event.target.value); requestKey.current = null }} placeholder="粘贴你们的聊天，标注「我 / 对方」，或直接告诉我你的困惑…" />
            {!!images.length && <div className="love-attachments">{images.map((image, index) => <button type="button" disabled={saving} key={index} onClick={() => setImages(current => current.filter((_, i) => i !== index))}><img src={image.url} alt={image.name} />移除</button>)}<button type="button" disabled={saving || !state.supports_images} onClick={() => void transcribeImages()}>{saving ? '正在识别…' : '识别截图 → 核对文字'}</button></div>}
            <div className="love-composer-actions"><label className={state.supports_images ? 'love-upload' : 'love-upload disabled'}><Icon name="upload" />{state.supports_images ? '上传聊天截图' : '截图待配置'}<input type="file" hidden accept="image/png,image/jpeg" multiple disabled={!state.supports_images || busy || saving} onChange={event => { void readImages(event.target.files); event.target.value = '' }} /></label><span className="love-composer-hint">截图先核对，再分析</span><button className="love-primary" disabled={!text.trim() || busy || !state.worker_online} type="submit">{busy ? '后台处理中…' : '发送'}<Icon name="send" /></button></div><small>由已配置的 AI 模型分析内容。每一条建议，都由你决定。</small>
          </form>
        </>}

        {state && (tab === 'memories' || tab === 'reminders') && <section className="love-library">
          <div className="love-section-head"><div><div className="love-eyebrow">{tab === 'memories' ? 'THE LITTLE THINGS MATTER' : 'MAKE ROOM FOR WHAT MATTERS'}</div><h1>{tab === 'memories' ? '关于 TA 的小事。' : '把在意，放进行动。'}</h1><p>{tab === 'memories' ? '已确认的偏好会参与相关性检索。随时纠正，也可以删除。' : '后台生成提醒并保留记录；系统通知需打开页面，微信需另行授权。'}</p></div><button className="love-primary" onClick={() => { setError(''); setDraft({ kind: tab === 'memories' ? 'memory' : 'reminder', title: '', quote: '', due_at: '' }) }}>＋ {tab === 'memories' ? '添加记忆' : '添加事项'}</button></div>
          {tab === 'memories' && <><div className="love-memory-summary"><div><Icon name="heart" /><strong>{state.item_counts.memory ?? 0}</strong><span>已确认的偏好</span></div><div><Icon name="chat" /><strong>{state.item_counts.memories_from_chat ?? 0}</strong><span>从聊天中记住</span></div><div><Icon name="shield" /><strong>由你掌控</strong><span>随时修改或删除</span></div></div><div className="love-search-wrap"><Icon name="search" /><input maxLength={200} className="love-search" aria-label="搜索偏好" placeholder="搜索已确认偏好…" value={memoryQuery} onChange={event => setMemoryQuery(event.target.value)} /></div></>}
          {tab === 'reminders' && <div className="love-inbox"><h2>提醒收件箱</h2>{!state.notices.length && <p>到期后，后台会在这里生成提醒记录。</p>}{state.notices.map(message => <article key={message.id}><div><strong>{message.title}</strong><small>{new Date(message.created_at).toLocaleString('zh-CN')} · {message.status === 'unread' ? '未读' : '已读'} · 微信：{({ not_connected: '未连接', accepted: '平台已接受', needs_activity: '需要重新互动', uncertain: '投递结果不确定' } as Record<string, string>)[message.weixin_status] ?? message.weixin_status}</small>{message.error && <p>{message.error}</p>}</div>{message.status === 'unread' && <button disabled={saving} onClick={() => void task(async () => { await api(`/notices/${message.id}/read`, 'POST'); await refresh() })}>已读</button>}{state.weixin.status === 'connected' && ['needs_activity', 'uncertain'].includes(message.weixin_status) && <button disabled={saving} onClick={() => void task(async () => { await api(`/notices/${message.id}/retry`, 'POST'); await refresh() })}>重试微信</button>}</article>)}</div>}
          {tab === 'reminders' && <div className="love-filter-tabs" aria-label="事项状态筛选">{[{ value: 'all', label: '全部事项' }, { value: 'active', label: '待跟进' }, { value: 'completed', label: '已完成' }, { value: 'cancelled', label: '已取消' }].map(filter => <button key={filter.value} className={itemFilter === filter.value ? 'selected' : ''} aria-pressed={itemFilter === filter.value} onClick={() => setItemFilter(filter.value)}>{filter.label}</button>)}</div>}
          {!items.length ? <div className="love-empty"><span className="love-empty-icon"><Icon name={tab === 'memories' ? 'heart' : 'calendar'} /></span><h3>{tab === 'memories' ? '值得记住的小事，都在这里。' : '给重要的事，留个位置。'}</h3><p>{tab === 'memories' ? '还没有匹配的偏好，可以从咨询中确认，也可以手动添加。' : '当前筛选下还没有事项。添加一个提醒，把关心落到行动。'}</p></div> : <div className="love-item-grid">{items.map(item => <article className={`love-item ${item.status}`} key={item.id}><div className="love-item-meta"><span>{item.kind === 'memory' ? '已确认记忆' : item.due_at ? new Date(item.due_at).toLocaleString('zh-CN') : ''}</span><small>{{ active: '有效', completed: '已完成', cancelled: '已取消' }[item.status]}</small></div><h2>{item.title}</h2>{item.quote && <blockquote>“{item.quote}”</blockquote>}<small>{item.source_turn_id ? '来源：你提供或核对的聊天文字' : '来源：手动添加'} · 修订 {item.revision}</small><div className="love-item-actions"><button disabled={saving} onClick={() => { setError(''); setDraft({ kind: item.kind as 'memory' | 'reminder', title: item.title, quote: item.quote, id: item.id, source_turn_id: item.source_turn_id ?? undefined, due_at: localDate(item.due_at) }) }}>编辑{item.kind === 'reminder' ? ' / 改期' : ''}</button>{item.kind === 'reminder' && item.status === 'active' && <><button disabled={saving} onClick={() => void changeItem(item, 'completed')}>完成</button><button disabled={saving} onClick={() => void changeItem(item, 'cancelled')}>取消</button></>}<button disabled={saving} onClick={() => void changeItem(item, 'delete')}>删除</button></div></article>)}</div>}
        </section>}

        {state && tab === 'dates' && <section className="love-library">
          <div className="love-section-head"><div><div className="love-eyebrow">A LITTLE TIME, JUST FOR TWO</div><h1>留一点时间，给彼此。</h1><p>结合已确认偏好规划参考行程。真实场所、价格和预约情况需核实。</p></div></div>
          <form className="love-date-form" onSubmit={event => { event.preventDefault(); void send(`请规划约会。城市/区域：${planForm.city}；总预算：${planForm.budget}元；时间：${planForm.when}；其他要求：${planForm.preferences || '无'}。请先读取已确认偏好，给出参考安排和待核实事项。`, 'date') }}>
            <label>城市 / 区域<input required maxLength={100} value={planForm.city} onChange={event => { setPlanForm({ ...planForm, city: event.target.value }); requestKey.current = null }} placeholder="例如：武汉 · 武昌" /></label>
            <label>两人总预算（元）<input required type="number" min={1} max={100000} value={planForm.budget} onChange={event => { setPlanForm({ ...planForm, budget: event.target.value }); requestKey.current = null }} /></label>
            <label>约会日期与时间<input required type="datetime-local" value={planForm.when} onChange={event => { setPlanForm({ ...planForm, when: event.target.value }); requestKey.current = null }} /></label>
            <label>偏好与限制<input maxLength={1000} value={planForm.preferences} onChange={event => { setPlanForm({ ...planForm, preferences: event.target.value }); requestKey.current = null }} placeholder="例如：安静、室内、步行少一点" /></label>
            <button className="love-primary" disabled={busy || !state.worker_online}>{busy ? '后台规划中…' : '结合偏好，生成方案'}</button>
          </form>
          {!!pending.length && <p className="love-thinking">规划任务正在后台处理，完成后会显示在下方和聊天记录中。</p>}
          <h2 className="love-subheading">已保存方案</h2>
          {!plans.length && <div className="love-empty love-empty-compact"><span className="love-empty-icon"><Icon name="calendar" /></span><div><h3>下一次相处，从一个小计划开始。</h3><p>填好时间与预算，挑一个适合你们的方案保存。</p></div></div>}
          <div className="love-item-grid">{plans.map(plan => <article className="love-item" key={plan.id}><h2>{plan.title}</h2><p>{plan.details.budget}</p><ol>{plan.details.schedule?.map((entry, i) => <li key={i}>{entry}</li>)}</ol><p>{plan.details.reason}</p><small>状态：{{ active: '计划中', completed: '已完成', cancelled: '已取消' }[plan.status]}</small><div className="love-item-actions"><button disabled={saving} onClick={() => setDraft({ kind: 'reminder', title: `准备约会：${plan.title}`, quote: '', due_at: '' })}>加一个准备提醒</button><button disabled={saving} onClick={() => void changeItem(plan, 'completed')}>完成</button><button disabled={saving} onClick={() => void changeItem(plan, 'delete')}>删除</button></div></article>)}</div>
          {!!dateTurns.length && <><h2 className="love-subheading">规划结果</h2><div className="love-conversation">{dateTurns.map(renderTurn)}</div></>}
        </section>}

        {state && tab === 'settings' && <SettingsPanel
          key={`${state.account.username ?? 'guest'}:${settingsSection.key}`} section={settingsSection.key} state={state}
          profile={profile} onProfile={setProfile} saving={saving} busy={busy} imports={imports}
          hasMoreImports={collection.hasMore} loadingImports={collection.loading} onLoadMoreImports={collection.loadMore}
          onSaveProfile={() => task(async () => { await api('/profile', 'PUT', profile); await refresh(); setNotice('偏好已保存，下一轮生效。') })}
          onAccount={() => setAuthMode('register')}
          onNotifications={() => { if (!('Notification' in window)) { setError('当前浏览器不支持系统通知'); return } void Notification.requestPermission().then(permission => { if (permission === 'granted') { localStorage.setItem('xinyu-notifications', 'enabled'); setNotice('已开启当前浏览器的系统通知。') } else setNotice('未开启系统通知，仍可在提醒收件箱查看。') }) }}
          onPrivacy={() => setPrivacyManualFor(privacyKey)}
          onImport={file => task(async () => { const form = new FormData(); form.append('file', file); await api('/imports', 'POST', form); await refresh(); setNotice('已导入，可在资料检索中查看。') })}
          onView={file => task(async () => { setRawDocument(await api<{ title: string; text: string }>(`/items/${file.id}/content`)) })}
          onDelete={file => changeItem(file, 'delete')}
          onClear={() => task(async () => { await api('/history', 'DELETE'); setOlder([]); await refresh(); setNotice('已清空本空间数据。') })}
        />}
        {collectionKind && tab !== 'settings' && collection.hasMore && <button className="love-load-more" disabled={collection.loading} onClick={() => void collection.loadMore()}>加载更多资料</button>}
      </div>
    </main>

    {privacyOpen && state?.privacy && <PrivacyDialog key={privacyKey} value={state.privacy} onRefresh={refresh} onClose={closePrivacy} onSaved={allowed => { closePrivacy(); setNotice(allowed ? 'AI 处理授权已保存，可以继续提交内容。' : '设置已保存，AI 处理保持关闭。') }} />}
    {draft && <div className="love-modal-backdrop"><section className="love-modal" role="dialog" aria-modal="true" aria-labelledby="love-modal-title"><h2 id="love-modal-title">{draft.kind === 'memory' ? '确认这条记忆' : '安排提醒'}</h2><p>核对后保存，不会通知伴侣。</p><form onSubmit={saveDraft}>{error && <p role="alert" className="love-alert">{error}</p>}<label>内容<textarea autoFocus required maxLength={500} rows={3} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>{draft.quote && <blockquote>来源：“{draft.quote}”</blockquote>}{draft.kind === 'reminder' && <label>提醒时间（设备时区）<input required type="datetime-local" value={draft.due_at} onChange={event => setDraft({ ...draft, due_at: event.target.value })} /><small>后台生成提醒记录；外部推送取决于通道连接。</small></label>}<div className="love-modal-actions"><button type="button" disabled={saving} onClick={() => setDraft(null)}>暂不保存</button><button className="love-primary" disabled={saving || !draft.title.trim()}>确认保存</button></div></form></section></div>}
    {transcript !== null && <div className="love-modal-backdrop"><section className="love-modal" role="dialog" aria-modal="true" aria-labelledby="transcript-title"><h2 id="transcript-title">核对截图文字</h2><p>确认双方身份、发言顺序和文字，可直接修改。</p><textarea className="love-transcript-editor" rows={12} value={transcript} onChange={event => setTranscript(event.target.value)} /><div className="love-modal-actions"><button onClick={() => setTranscript(null)}>返回</button><button className="love-primary" disabled={!transcript.trim()} onClick={() => { setText(current => (current ? current + '\n\n' : '') + transcript); setTranscript(null); setImages([]); requestKey.current = null }}>确认并放入输入框</button></div></section></div>}
    {rawDocument && <div className="love-modal-backdrop"><section className="love-modal" role="dialog" aria-modal="true" aria-labelledby="raw-title"><h2 id="raw-title">{rawDocument.title}</h2><textarea className="love-transcript-editor" aria-label="聊天原文" rows={14} readOnly value={rawDocument.text} /><div className="love-modal-actions"><button onClick={() => setRawDocument(null)}>关闭原文</button></div></section></div>}
    {authMode && <AuthDialog username={state?.account.username ?? null} initialMode={authMode} onClose={() => setAuthMode(null)} onRefresh={reloadSpace} onNotice={setNotice} onBusy={setSaving} />}
  </div>
}
