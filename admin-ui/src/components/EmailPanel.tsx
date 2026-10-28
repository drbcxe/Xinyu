import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../relationshipApi'

type Captcha = { challenge_id: string; image: string; expires_in: number }
type Status = { email: string | null; verified: boolean; service_configured: boolean }
type Props = { purpose: 'bind' | 'reset'; onBack: () => void; onBusy: (value: boolean) => void }

export default function EmailPanel({ purpose, onBack, onBusy }: Props) {
  const [status, setStatus] = useState<Status | null>(null)
  const [form, setForm] = useState({ username: '', email: '', current_password: '', captcha_code: '', code: '', new_password: '', confirm_password: '' })
  const [captcha, setCaptcha] = useState<Captcha | null>(null)
  const [challenge, setChallenge] = useState<{ challenge_id: string; expires_in: number } | null>(null)
  const [submittedUsername, setSubmittedUsername] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [done, setDone] = useState(false)
  const [deadline, setDeadline] = useState(0)
  const [captchaDeadline, setCaptchaDeadline] = useState(0)
  const [cooldown, setCooldown] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const loadCaptcha = useCallback(async () => {
    setCaptcha(null)
    setForm(previous => ({ ...previous, captcha_code: '' }))
    const next = await api<Captcha>('/auth/captcha')
    setCaptcha(next); setCaptchaDeadline(Date.now() + next.expires_in * 1000)
  }, [])

  useEffect(() => {
    let active = true
    const start = async () => {
      try {
        if (purpose === 'bind') {
          const next = await api<Status>('/auth/email')
          if (active) setStatus(next)
        }
        if (active) await loadCaptcha()
      } catch (err) { if (active) setError((err as Error).message) }
    }
    void start()
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => { active = false; clearInterval(timer) }
  }, [purpose, loadCaptcha])

  useEffect(() => {
    if (purpose !== 'bind' || !challenge || done) return
    let active = true
    const timer = setInterval(() => {
      if (Date.now() >= deadline) { clearInterval(timer); return }
      void api<{ status: string; error: string }>(`/auth/email/delivery/${challenge.challenge_id}`).then(result => {
        if (!active) return
        if (result.status === 'sent') setMessage('邮件服务已接受验证邮件，请检查收件箱和垃圾邮件。')
        if (result.status === 'failed') setError('验证邮件发送失败，请确认 SMTP 配置或稍后重新申请。')
        if (['sent', 'failed', 'expired'].includes(result.status)) clearInterval(timer)
      }).catch(() => { /* Keep the request available until expiry; no address is disclosed. */ })
    }, 5000)
    return () => { active = false; clearInterval(timer) }
  }, [purpose, challenge, deadline, done])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true); onBusy(true); setError(''); setMessage('')
    try {
      if (!challenge) {
        if (!captcha || Date.now() >= captchaDeadline || Date.now() < cooldown) return
        const verification = { captcha_id: captcha.challenge_id, captcha_code: form.captcha_code, email: form.email.trim() }
        const result = await api<{ challenge_id: string; expires_in: number; message: string }>(purpose === 'bind' ? '/auth/email/send' : '/auth/recovery/send', 'POST', { ...verification, ...(purpose === 'bind' ? { current_password: form.current_password } : { username: form.username.trim() }) })
        setChallenge(result); setSubmittedUsername(form.username.trim()); setDeadline(Date.now() + result.expires_in * 1000); setCooldown(Date.now() + 60_000); setMessage(result.message)
        setForm(previous => ({ ...previous, current_password: '', code: '' }))
      } else {
        if (purpose === 'reset' && form.new_password !== form.confirm_password) throw new Error('两次输入的密码不一致')
        const result = await api<{ message?: string }>(purpose === 'bind' ? '/auth/email/verify' : '/auth/recovery/verify', 'POST', { challenge_id: challenge.challenge_id, code: form.code, ...(purpose === 'reset' ? { username: submittedUsername, new_password: form.new_password, confirm_password: form.confirm_password } : {}) })
        setDone(true); setMessage(result.message ?? '邮箱验证成功，已可用于找回密码。')
        setForm(previous => ({ ...previous, new_password: '', confirm_password: '', code: '' }))
      }
    } catch (err) {
      setError((err as Error).message)
      if (err instanceof ApiError && Number(err.details.retry_after_seconds) > 0) setCooldown(Date.now() + Number(err.details.retry_after_seconds) * 1000)
      if (!challenge) { try { await loadCaptcha() } catch { setCaptcha(null) } }
    } finally { setBusy(false); onBusy(false) }
  }

  const seconds = Math.max(0, Math.ceil((deadline - now) / 1000))
  const wait = Math.max(0, Math.ceil((cooldown - now) / 1000))
  return <div className="auth-email-panel">
    {status && <div className="auth-security-summary"><div><strong>{status.verified ? `已验证：${status.email}` : '尚未绑定找回邮箱'}</strong><span>{status.service_configured ? '新邮箱验证通过后生效' : '邮件服务待配置，请先在本地填写 SMTP'}</span></div></div>}
    {error && <div className="auth-error" role="alert">{error}</div>}
    {message && <div className="auth-success" role="status">{message}</div>}
    {!done && <form onSubmit={event => void submit(event)}><fieldset disabled={busy}>
      {!challenge ? <>
        {purpose === 'reset' && <label>用户名<input required autoComplete="username" maxLength={40} value={form.username} onChange={event => setForm({ ...form, username: event.target.value })} /></label>}
        <label>{purpose === 'bind' ? '找回邮箱' : '已验证的找回邮箱'}<input required type="email" autoComplete="email" maxLength={254} placeholder="name@example.com" value={form.email} onChange={event => setForm({ ...form, email: event.target.value })} /></label>
        {purpose === 'bind' && <label>当前密码<input required type="password" autoComplete="current-password" maxLength={128} value={form.current_password} onChange={event => setForm({ ...form, current_password: event.target.value })} /></label>}
        <div className="auth-code-heading"><span>安全验证码</span><small>看不清可点击图片刷新</small></div>
        <div className="auth-captcha-row"><input required aria-label="图形验证码" maxLength={5} value={form.captcha_code} onChange={event => setForm({ ...form, captcha_code: event.target.value.toUpperCase() })} /><button type="button" className="auth-captcha-image" disabled={busy} onClick={() => void loadCaptcha().catch(err => setError(err.message))} aria-label="刷新图形验证码">{captcha ? <img src={captcha.image} alt="图形验证码" /> : '点击加载'}</button></div>
        <button className="love-primary auth-submit" disabled={busy || !captcha || now >= captchaDeadline || !!wait || status?.service_configured === false}>{busy ? '正在申请…' : wait ? `请 ${wait} 秒后重试` : '发送邮箱验证码'}</button>
      </> : <>
        <p className="auth-caption">请查看收件箱和垃圾邮件。验证码剩余 {Math.floor(seconds / 60)} 分 {seconds % 60} 秒，最多尝试 5 次。</p>
        <label>6 位邮箱验证码<input required autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={form.code} onChange={event => setForm({ ...form, code: event.target.value.replace(/\D/g, '') })} /></label>
        {purpose === 'reset' && <><label>新密码<input required type="password" autoComplete="new-password" minLength={15} maxLength={128} placeholder="15–128 位，推荐一段长口令" value={form.new_password} onChange={event => setForm({ ...form, new_password: event.target.value })} /></label><label>确认新密码<input required type="password" autoComplete="new-password" minLength={15} maxLength={128} value={form.confirm_password} onChange={event => setForm({ ...form, confirm_password: event.target.value })} /></label></>}
        <button className="love-primary auth-submit" disabled={busy || !seconds}>{busy ? '正在验证…' : purpose === 'bind' ? '验证并绑定邮箱' : '重置密码并退出旧会话'}</button>
        <button type="button" className="auth-back" disabled={busy || !!wait} onClick={() => { setChallenge(null); setError(''); setMessage(''); void loadCaptcha().catch(err => setError(err.message)) }}>{wait ? `${wait} 秒后可重新申请` : '重新申请验证码'}</button>
      </>}
    </fieldset></form>}
    <div className="auth-bottom-note"><p>{purpose === 'bind' ? '只有验证成功的邮箱才能找回密码。更换邮箱需要再次验证当前密码。' : '需要你提前绑定的邮箱。重置后请重新登录，原有聊天和记忆会保留。'}</p></div>
    <button className="auth-back" disabled={busy} onClick={onBack}>{purpose === 'bind' ? '← 返回账号管理' : '← 返回登录'}</button>
  </div>
}
