import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../relationshipApi'

type Captcha = { challenge_id: string; image: string }
type ReceiptStatus = { status: string; pending_indexes: number; note: string }
export default function AccountDeletionPanel({ onBack, onBusy, onRefresh }: { onBack: () => void; onBusy: (busy: boolean) => void; onRefresh: () => Promise<void> }) {
  const [captcha, setCaptcha] = useState<Captcha | null>(null)
  const [form, setForm] = useState({ current_password: '', confirmation: '', captcha_code: '' })
  const [receipt, setReceipt] = useState('')
  const [status, setStatus] = useState<ReceiptStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const refreshCaptcha = useCallback(async () => {
    const next = await api<Captcha>('/auth/captcha')
    setCaptcha(next); setForm(previous => ({ ...previous, captcha_code: '' }))
  }, [])
  useEffect(() => {
    const timer = setTimeout(() => { void refreshCaptcha().catch(err => setError(err.message)) }, 0)
    return () => clearTimeout(timer)
  }, [refreshCaptcha])
  const refreshStatus = useCallback(async () => {
    if (receipt) setStatus(await api<ReceiptStatus>('/privacy/deletion/status', 'POST', { receipt }))
  }, [receipt])
  useEffect(() => {
    if (!receipt || status?.status === 'completed') return
    const timer = setInterval(() => { void refreshStatus().catch(err => setError(err.message)) }, 10_000)
    return () => clearInterval(timer)
  }, [receipt, refreshStatus, status?.status])
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!captcha || busy) return
    setBusy(true); onBusy(true); setError('')
    try {
      const result = await api<{ receipt: string }>('/auth/account', 'DELETE', { ...form, captcha_id: captcha.challenge_id })
      setReceipt(result.receipt); setForm({ current_password:'',confirmation:'',captcha_code:'' }); setCaptcha(null)
      await onRefresh().catch(() => setError('账号已注销，页面状态暂时未刷新，请重新打开页面。'))
    } catch (err) {
      setError((err as Error).message)
      try { await refreshCaptcha() } catch { setCaptcha(null) }
    } finally { setBusy(false); onBusy(false) }
  }
  return <div className="auth-email-panel">{error && <div className="auth-error" role="alert">{error}</div>}{receipt ? <>
    <div className="auth-success" role="status">账号已注销，原有会话已退出。</div><p className="auth-caption">{status?.status === 'completed' ? '在线数据、检索索引与图检查点清理已确认完成。' : '在线内容已移除，检索和执行临时副本正在后台清理。'}</p><p className="auth-caption">{status?.note ?? '已有备份和模型平台的留存需单独处理。'}</p><label>注销回执<input aria-label="注销回执" readOnly value={receipt} /></label><div className="love-modal-actions"><button disabled={copied} onClick={() => void navigator.clipboard.writeText(receipt).then(() => setCopied(true)).catch(() => setError('复制失败，可以手动选择回执复制'))}>{copied ? '已复制回执' : '复制回执'}</button><button onClick={() => void refreshStatus().catch(err => setError(err.message))}>查看清理状态</button></div>
  </> : <><div className="auth-error"><strong>注销后无法恢复账号中的在线内容。</strong><p>账号、聊天、偏好、事项、导入记录、反馈及微信授权都会移除，所有设备退出。可以先返回设置页导出数据。</p><p>检索副本在后台继续清理；已有备份和模型平台留存需单独处理。</p></div><form onSubmit={event => void submit(event)}><fieldset disabled={busy}><label>当前密码<input required type="password" autoComplete="current-password" maxLength={128} value={form.current_password} onChange={event => setForm({ ...form, current_password:event.target.value })} /></label><label>输入「注销账号」确认<input required aria-label="注销确认文字" value={form.confirmation} onChange={event => setForm({ ...form, confirmation:event.target.value })} /></label><div className="auth-code-heading"><span>安全验证码</span></div><div className="auth-captcha-row"><input required aria-label="图形验证码" maxLength={5} value={form.captcha_code} onChange={event => setForm({ ...form, captcha_code:event.target.value.toUpperCase() })} /><button type="button" className="auth-captcha-image" onClick={() => void refreshCaptcha().catch(err => setError(err.message))} aria-label="刷新图形验证码">{captcha ? <img src={captcha.image} alt="图形验证码" /> : '点击加载'}</button></div><button type="submit" className="love-danger auth-submit" disabled={busy || !captcha || form.confirmation !== '注销账号'}>{busy ? '正在注销…' : '永久注销账号'}</button></fieldset></form><button className="auth-back" disabled={busy} onClick={onBack}>← 返回账号管理</button></>}
  </div>
}
