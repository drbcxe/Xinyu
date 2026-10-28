import { useState } from 'react'
import { api } from '../relationshipApi'

type Feedback = { rating: 'helpful' | 'unhelpful'; category: string; comment: string }
export default function FeedbackControls({ turnId }: { turnId: string }) {
  const [open, setOpen] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>({ rating: 'helpful', category: 'general', comment: '' })
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  async function show() {
    setOpen(!open); setError('')
    if (!open) {
      setBusy(true)
      try {
        const next = await api<{ feedback: Feedback | null }>(`/feedback/${turnId}`)
        if (next.feedback) { setFeedback(next.feedback); setSaved(true) }
        else { setFeedback({ rating: 'helpful', category: 'general', comment: '' }); setSaved(false) }
      } catch (err) { setError((err as Error).message) }
      finally { setBusy(false) }
    }
  }
  async function save(remove = false) {
    setBusy(true); setError('')
    try { await api(`/feedback/${turnId}`, remove ? 'DELETE' : 'PUT', remove ? undefined : feedback); setSaved(!remove); setMessage(remove ? '反馈已移除' : ''); if (remove) setFeedback({ rating: 'helpful', category: 'general', comment: '' }) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }
  return <div className="love-feedback"><button type="button" disabled={busy} aria-expanded={open} onClick={() => void show()}>{open ? '收起反馈' : '这次建议有帮助吗？'}</button>{open && <div className="love-feedback-form">
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    <fieldset disabled={busy}><label>你的感受<select aria-label="反馈评价" value={feedback.rating} onChange={event => { setFeedback({ ...feedback, rating: event.target.value as Feedback['rating'] }); setSaved(false) }}><option value="helpful">有帮助</option><option value="unhelpful">还不够合适</option></select></label><label>主要原因<select aria-label="反馈原因" value={feedback.category} onChange={event => { setFeedback({ ...feedback, category: event.target.value }); setSaved(false) }}>{[['general','整体感受'],['inaccurate','理解有误'],['tone','表达不自然'],['unsafe','不适当的建议'],['bug','功能问题']].map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>补充说明（可选）<textarea rows={2} maxLength={1000} value={feedback.comment} onChange={event => { setFeedback({ ...feedback, comment:event.target.value }); setSaved(false) }} placeholder="哪一点需要改善？不用重复粘贴完整聊天。" /></label><div><button type="button" disabled={busy} onClick={() => void save()}>{busy ? '正在保存…' : '保存反馈'}</button><button type="button" disabled={busy} onClick={() => void save(true)}>移除反馈</button>{saved && <small role="status">已保存，可随时修改</small>}</div></fieldset><small>反馈保存在你的空间中，不会自动发给伴侣或其他平台，也不会自动用于训练。</small>
  </div>}</div>
}
