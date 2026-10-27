import { useCallback, useEffect, useState } from 'react'
import { api } from '../relationshipApi'

type Usage = { requests: number; request_limit: number; charged_tokens: number; reserved_tokens: number; token_limit: number; resets_at: string; cost_note: string; usage_by_purpose: { purpose: string; calls: number; charged_tokens: number }[] }
const purposeNames: Record<string, string> = { advice: '沟通分析', ocr: '截图识别', rerank: '资料重排' }
export default function UsagePanel() {
  const [usage, setUsage] = useState<Usage | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const refresh = useCallback(async () => {
    setLoading(true); setError('')
    try { setUsage(await api<Usage>('/usage')) } catch (err) { setError((err as Error).message) } finally { setLoading(false) }
  }, [])
  useEffect(() => { const timer = setTimeout(() => { void refresh() }, 0); return () => clearTimeout(timer) }, [refresh])
  return <div className="love-settings-note love-usage"><h2>今日用量</h2>{error && <p role="alert">{error}</p>}{usage && <><div className="love-usage-meters"><label>分析 / 识别任务 <strong>{usage.requests} / {usage.request_limit}</strong><progress value={usage.requests} max={usage.request_limit} /></label><label>模型 Token 预算 <strong>{(usage.charged_tokens + usage.reserved_tokens).toLocaleString()} / {usage.token_limit.toLocaleString()}</strong><progress value={usage.charged_tokens + usage.reserved_tokens} max={usage.token_limit} /></label></div><p>已记账 {usage.charged_tokens.toLocaleString()} Token · 执行中预留 {usage.reserved_tokens.toLocaleString()} Token</p><p>{usage.usage_by_purpose.map(row => `${purposeNames[row.purpose] ?? row.purpose} ${row.calls} 次`).join(' · ') || '今天还没有调用模型'}</p><small>额度重置：{new Date(usage.resets_at).toLocaleString('zh-CN')}。{usage.cost_note}。</small></>}<button disabled={loading} onClick={() => void refresh()}>{loading ? '正在读取…' : '刷新用量'}</button></div>
}
