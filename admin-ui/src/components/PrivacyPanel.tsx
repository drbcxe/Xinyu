import { useState } from 'react'
import { api } from '../relationshipApi'
import type { PrivacyStatus } from '../relationshipApi'
import './PrivacyPanel.css'

export default function PrivacyPanel({ value, compact = false, onRefresh }: { value: PrivacyStatus; compact?: boolean; onRefresh: () => Promise<unknown> }) {
  const [checked, setChecked] = useState(value.accepted)
  const [days, setDays] = useState(value.history_days)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  async function save(allow: boolean) {
    setBusy(true); setError(''); setSaved(false)
    try {
      await api('/privacy', 'PUT', { version: value.version, provider_fingerprint: value.provider_fingerprint, allow_model_processing: allow, history_days: days })
      setChecked(allow)
      await onRefresh(); setSaved(true)
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  return <section className={`privacy-panel${compact ? ' compact' : ''}`} aria-label="AI 数据与保留设置">
    <div className="privacy-heading"><span aria-hidden="true">◇</span><div><h2>{value.accepted ? '你的数据，由你决定' : '开始前，了解你的数据如何使用'}</h2><p>{value.accepted ? '已允许当前空间使用下列模型服务，可随时撤回。' : '阅读说明后自主选择。未开启时，仍可查看、导出和管理已有资料。'}</p></div></div>
    <div className="privacy-destinations"><span>文字分析：{value.destinations.text.model || '未配置'} · {value.destinations.text.host}</span><span>截图识别：{value.destinations.vision.model || '未配置'} · {value.destinations.vision.host}</span></div>
    <details open={!value.accepted}><summary>处理范围与数据保留</summary><ul>{value.disclosures.map(line => <li key={line}>{line}</li>)}</ul><p>{value.retention_note}</p></details>
    {!compact && <label className="privacy-retention">咨询与导入聊天保留<select value={days} disabled={busy} onChange={event => { setDays(Number(event.target.value) as PrivacyStatus['history_days']); setSaved(false) }}><option value={30}>30 天</option><option value={90}>90 天</option><option value={180}>180 天</option></select><small>缩短后，超过期限的内容会在后台清理；修改前可先导出。已确认偏好和重要事项由你手动删除。</small></label>}
    {!value.accepted && <label className="privacy-choice"><input type="checkbox" checked={checked} disabled={busy} onChange={event => setChecked(event.target.checked)} /><span>我已阅读，并允许本空间按上述范围使用 AI 模型处理我提交的内容。</span></label>}
    <div className="privacy-actions">{value.accepted ? <><button type="button" disabled={busy} onClick={() => void save(false)}>撤回 AI 处理授权</button>{!compact && <button type="button" className="love-primary" disabled={busy || days === value.history_days} onClick={() => void save(true)}>保存保留期限</button>}</> : <><button type="button" className="love-primary" disabled={!checked || busy} onClick={() => void save(true)}>{busy ? '正在保存…' : '确认并开启 AI 助手'}</button>{!compact && <button type="button" disabled={busy || days === value.history_days} onClick={() => void save(false)}>仅保存保留期限</button>}</>}</div>
    {error && <p className="privacy-error" role="alert">{error}</p>}{saved && <p role="status">选择已保存。撤回后已发出的请求无法追回，后续调用会停止。</p>}
  </section>
}
