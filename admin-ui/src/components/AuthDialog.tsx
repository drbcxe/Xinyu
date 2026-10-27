import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../relationshipApi'
import Icon from './Icon'
import EmailPanel from './EmailPanel'
import './AuthDialog.css'

type Captcha = { challenge_id: string; image: string; expires_in: number }
type DeviceSession = { id: string; current: boolean; device_label: string; created_at: string; expires_at: string }
type SecurityEvent = { id: string; event: string; device_label: string; created_at: string }
type Props = { username: string | null; initialMode: 'login' | 'register'; onClose: () => void; onRefresh: () => Promise<void>; onNotice: (message: string) => void; onBusy: (busy: boolean) => void }
const eventNames: Record<string, string> = { registered: '创建账号', logged_in: '登录成功', login_failed: '密码验证失败', logged_out: '退出登录', session_revoked: '撤销设备会话', other_sessions_revoked: '退出其他设备', password_changed: '修改密码', password_change_failed: '修改密码验证失败', email_bind_failed: '邮箱绑定验证失败', email_verification_requested: '申请邮箱验证', email_verified: '邮箱验证成功', password_reset_requested: '申请密码找回', password_reset: '通过邮箱重置密码' }
const dateTime = (value: string) => new Date(value).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

export default function AuthDialog({ username, initialMode, onClose, onRefresh, onNotice, onBusy }: Props) {
  const [mode, setMode] = useState<'login' | 'register' | 'account' | 'password' | 'email' | 'recovery'>(username ? 'account' : initialMode)
  const [form, setForm] = useState({ username: '', password: '', confirm_password: '', current_password: '', captcha_code: '', remember_me: false })
  const [showPassword, setShowPassword] = useState(false)
  const [captcha, setCaptcha] = useState<Captcha | null>(null)
  const [expiresAt, setExpiresAt] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const [lockedUntil, setLockedUntil] = useState(0)
  const [busy, setBusy] = useState(false)
  const [loadingCode, setLoadingCode] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [sessions, setSessions] = useState<DeviceSession[]>([])
  const [events, setEvents] = useState<SecurityEvent[]>([])
  const [accountLoading, setAccountLoading] = useState(true)

  const showError = useCallback((err: unknown) => {
    setError(err instanceof Error ? err.message : '操作失败，请稍后重试')
    if (err instanceof ApiError) {
      const retry = Number(err.details.retry_after_seconds ?? 0)
      if (retry > 0) setLockedUntil(Date.now() + retry * 1000)
      if (typeof err.details.remaining_attempts === 'number') setError(`${err.message}，还可尝试 ${err.details.remaining_attempts} 次。`)
    }
  }, [])

  const loadCaptcha = useCallback(async () => {
    setLoadingCode(true); setCaptcha(null)
    setForm(previous => ({ ...previous, captcha_code: '' }))
    try {
      const next = await api<Captcha>('/auth/captcha')
      const receivedAt = Date.now()
      setCaptcha(next); setNow(receivedAt); setExpiresAt(receivedAt + next.expires_in * 1000)
    } catch (err) { showError(err) } finally { setLoadingCode(false) }
  }, [showError])

  const loadAccount = useCallback(async () => {
    setAccountLoading(true)
    try {
      const [devices, history] = await Promise.all([api<DeviceSession[]>('/auth/sessions'), api<SecurityEvent[]>('/auth/events')])
      setSessions(devices); setEvents(history)
    } catch (err) { showError(err) } finally { setAccountLoading(false) }
  }, [showError])

  useEffect(() => {
    if (mode === 'email' || mode === 'recovery') return
    const timer = setTimeout(() => { void (mode === 'account' ? loadAccount() : loadCaptcha()) }, 0)
    return () => clearTimeout(timer)
  }, [mode, loadAccount, loadCaptcha])
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  const retryIn = Math.max(0, Math.ceil((lockedUntil - now) / 1000))
  const codeIn = Math.max(0, Math.ceil((expiresAt - now) / 1000))
  const isNewPassword = mode === 'register' || mode === 'password'
  const passwordLength = Array.from(form.password).length
  const passwordOkay = passwordLength >= 15 && passwordLength <= 128 && new Set(form.password).size >= 4
  const confirmationOkay = form.confirm_password === form.password

  async function action(work: () => Promise<void>) {
    setBusy(true); onBusy(true); setError(''); setMessage('')
    try { await work() } catch (err) { showError(err) } finally { setBusy(false); onBusy(false) }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!captcha || !codeIn || busy || retryIn) return
    if (isNewPassword && !confirmationOkay) { setError('两次输入的密码不一致'); return }
    await action(async () => {
      try {
        const verification = { captcha_id: captcha.challenge_id, captcha_code: form.captcha_code }
        if (mode === 'password') {
          await api('/auth/password', 'POST', { ...verification, current_password: form.current_password, new_password: form.password, confirm_password: form.confirm_password })
          await onRefresh(); setForm(previous => ({ ...previous, password: '', confirm_password: '', current_password: '', captcha_code: '' }))
          setMode('account'); setMessage('密码已更新，其他设备已退出；当前设备保留 8 小时会话。')
        } else {
          await api(`/auth/${mode}`, 'POST', { ...verification, username: form.username.trim(), password: form.password, ...(mode === 'register' ? { confirm_password: form.confirm_password } : {}), remember_me: form.remember_me })
          await onRefresh(); onNotice(mode === 'register' ? '账号创建成功，原访客记录已保留。' : '已登录并打开账号空间。'); onClose()
        }
      } catch (err) {
        // Every submitted challenge is treated as consumed, including wrong answers.
        await loadCaptcha(); throw err
      }
    })
  }

  function switchMode(next: typeof mode) {
    setMode(next); setError(''); setMessage(''); setShowPassword(false)
    setForm(previous => ({ ...previous, password: '', confirm_password: '', current_password: '', captcha_code: '' }))
  }

  return <div className="love-modal-backdrop">
    <section className={`love-modal auth-dialog ${mode === 'account' ? 'auth-account' : ''}`} role="dialog" aria-modal="true" aria-labelledby="account-title">
      <button className="auth-close" type="button" disabled={busy} onClick={onClose} aria-label="关闭账号窗口">×</button>
      <div className="auth-heading"><span className="auth-brand-icon"><Icon name="shield" /></span><span className="auth-eyebrow">XINYU · YOUR PRIVATE SPACE</span>
        <h2 id="account-title">{{ login: '欢迎回来。', register: '让在意，妥善保存。', account: '账号与安全', password: '设置一个新的密码', email: '绑定找回邮箱', recovery: '找回你的私人空间' }[mode]}</h2>
        <p>{mode === 'register' ? '注册后保留当前访客记录，也可以在其他设备登录。' : mode === 'login' ? '登录你的私人空间，接着聊那些重要的小事。' : mode === 'password' ? '验证当前密码后更新，其他设备会自动退出。' : mode === 'email' ? '验证一个你能长期使用的邮箱，给账号留一条找回路径。' : mode === 'recovery' ? '通过提前验证的邮箱，重新设置密码。' : `${username}，在这里管理登录设备与密码。`}</p>
      </div>
      {error && <div className="auth-error" role="alert">{error}</div>}
      {message && <div className="auth-success" role="status">{message}</div>}
      {mode === 'email' || mode === 'recovery' ? <EmailPanel purpose={mode === 'email' ? 'bind' : 'reset'} onBack={() => switchMode(mode === 'email' ? 'account' : 'login')} onBusy={value => { setBusy(value); onBusy(value) }} /> : mode === 'account' ? <>
        <div className="auth-security-summary"><Icon name="shield" /><div><strong>私人空间已关联账号</strong><span>密码受保护 · 登录设备可以随时退出</span></div><button disabled={busy} onClick={() => switchMode('password')}>修改密码 →</button></div>
        <button className="auth-back" disabled={busy} onClick={() => switchMode('email')}>绑定 / 更换找回邮箱 →</button>
        <div className="auth-section-title"><h3>登录设备 <small>{sessions.length} / 5</small></h3><button disabled={busy || accountLoading} onClick={() => void action(async () => { const result = await api<{ revoked_count: number }>('/auth/logout-all', 'POST'); await loadAccount(); setMessage(`已退出 ${result.revoked_count} 个其他会话。`) })}>退出其他设备</button></div>
        <p className="auth-caption">根据浏览器识别设备；同一设备的不同浏览器会分别显示。</p>
        {accountLoading ? <p className="auth-caption" role="status">正在读取会话…</p> : <div className="auth-device-list">{sessions.map(session => <article key={session.id}><span className="auth-device-icon"><Icon name="user" /></span><div><strong>{session.device_label} {session.current && <em>当前设备</em>}</strong><small>{dateTime(session.created_at)} 登录 · {dateTime(session.expires_at)} 到期</small></div>{!session.current && <button disabled={busy} onClick={() => void action(async () => { await api(`/auth/sessions/${session.id}`, 'DELETE'); await loadAccount(); setMessage('该设备已退出。') })}>退出</button>}</article>)}</div>}
        <div className="auth-section-title"><h3>最近安全动态</h3><span>最多 20 条</span></div>
        <div className="auth-event-list">{events.map(event => <div key={event.id}><span className={event.event.endsWith('failed') ? 'failed' : ''} /><div><strong>{eventNames[event.event] ?? '账号操作'}</strong><small>{event.device_label}</small></div><time>{dateTime(event.created_at)}</time></div>)}</div>
        <div className="auth-footer"><span>退出后记录仍保存在账号中。</span><button className="auth-logout" disabled={busy} onClick={() => void action(async () => { await api('/auth/logout', 'POST'); await onRefresh(); onNotice('已退出账号，登录后可恢复记录。'); onClose() })}>退出当前账号</button></div>
      </> : <>
        {mode !== 'password' && <div className="auth-tabs" aria-label="账号操作"><button type="button" className={mode === 'login' ? 'selected' : ''} disabled={busy} onClick={() => switchMode('login')}>登录</button><button type="button" className={mode === 'register' ? 'selected' : ''} disabled={busy} onClick={() => switchMode('register')}>创建账号</button></div>}
        <form onSubmit={event => void submit(event)}>
          <fieldset disabled={busy}>
            {mode !== 'password' && <label>用户名<input required name="username" autoComplete="username" minLength={3} maxLength={40} pattern="[A-Za-z0-9_\-]{3,40}" placeholder="3–40 位字母、数字、下划线或连字符" value={form.username} onChange={event => setForm({ ...form, username: event.target.value })} /></label>}
            {mode === 'password' && <label>当前密码<input required type="password" name="current-password" autoComplete="current-password" maxLength={128} value={form.current_password} onChange={event => setForm({ ...form, current_password: event.target.value })} /></label>}
            <label>{mode === 'password' ? '新密码' : '密码'}<span className="auth-password-field"><input required type={showPassword ? 'text' : 'password'} name={isNewPassword ? 'new-password' : 'password'} autoComplete={isNewPassword ? 'new-password' : 'current-password'} minLength={isNewPassword ? 15 : 1} maxLength={128} placeholder={isNewPassword ? '推荐使用 15 位以上的长口令' : '请输入账号密码'} value={form.password} onChange={event => setForm({ ...form, password: event.target.value })} /><button type="button" aria-label={showPassword ? '隐藏密码' : '显示密码'} onClick={() => setShowPassword(!showPassword)}>{showPassword ? '隐藏' : '显示'}</button></span></label>
            {isNewPassword && <><div className={`auth-password-hint ${passwordOkay ? 'valid' : ''}`}><span /><small>{passwordOkay ? '长度符合要求，避免使用常见密码。' : '15–128 位；可以用一句好记的长口令。'}</small></div><label>确认{mode === 'password' ? '新' : ''}密码<input required type="password" autoComplete="new-password" minLength={15} maxLength={128} value={form.confirm_password} onChange={event => setForm({ ...form, confirm_password: event.target.value })} aria-invalid={!!form.confirm_password && !confirmationOkay} placeholder="再次输入，确认没有输错" /></label>{form.confirm_password && !confirmationOkay && <small className="auth-inline-error">两次输入还不一致</small>}</>}
            <div className="auth-code-heading"><span>安全验证码</span><small>{captcha ? codeIn ? `${codeIn} 秒后过期` : '已过期，请刷新' : '一次性使用'}</small></div>
            <div className="auth-captcha-row"><input required aria-label="图形验证码" autoComplete="off" spellCheck={false} inputMode="text" maxLength={5} placeholder="输入右侧字符" value={form.captcha_code} onChange={event => setForm({ ...form, captcha_code: event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '') })} /><button type="button" disabled={loadingCode || !!retryIn} className="auth-captcha-image" onClick={() => { setError(''); void loadCaptcha() }} aria-label="刷新图形验证码">{captcha ? <img src={captcha.image} alt="图形验证码，无法辨认时点击刷新" /> : <span>{loadingCode ? '正在加载…' : '点击重试'}</span>}</button></div>
            <div className="auth-code-footer"><small>不区分大小写 · 看不清可刷新</small><button type="button" disabled={loadingCode || !!retryIn} onClick={() => { setError(''); void loadCaptcha() }}>换一张 ↻</button></div>
            {mode !== 'password' && <label className="auth-remember"><input type="checkbox" checked={form.remember_me} onChange={event => setForm({ ...form, remember_me: event.target.checked })} /><span>在这台设备上保持登录 30 天</span></label>}
            <button className="love-primary auth-submit" disabled={busy || loadingCode || !captcha || !codeIn || !!retryIn || (isNewPassword && (!passwordOkay || !confirmationOkay))}>{busy ? '正在验证…' : retryIn ? `请 ${retryIn} 秒后重试` : mode === 'register' ? '创建账号并保留记录' : mode === 'password' ? '确认修改密码' : '登录私人空间'}<Icon name="arrow" /></button>
          </fieldset>
        </form>
        {mode === 'login' && <button className="auth-back" disabled={busy} onClick={() => switchMode('recovery')}>忘记密码？通过已验证邮箱找回 →</button>}
        <div className="auth-bottom-note"><Icon name="shield" /><p>{mode === 'password' ? '修改成功会撤销旧会话，并为当前设备创建新会话。' : mode === 'register' ? '注册后可在账号管理中绑定邮箱，用于密码找回。' : '默认会话最长 8 小时；登录已有账号不会自动合并访客记录。'}</p></div>
        {mode === 'password' && <button className="auth-back" disabled={busy} onClick={() => switchMode('account')}>← 返回账号管理</button>}
      </>}
    </section>
  </div>
}
