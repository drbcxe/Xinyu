import { useCallback, useEffect, useState } from 'react'
import { api } from '../relationshipApi'

type Usage = { requests: number; request_limit: number; charged_tokens: number; reserved_tokens: number; token_limit: number; resets_at: string; cost_note: string; usage_by_purpose: { purpose: string; calls: number; charged_tokens: number }[]; storage?: {used_bytes: number; reserved_bytes: number; limit_bytes: number; remaining_bytes: number; task_reserve_bytes: number; by_category: Record<string,number>} }
const purposeNames: Record<string, string> = { advice: '沟通分析', ocr: '截图识别', rerank: '资料重排' }
const categoryNames: Record<string,string> = {items:'已保存资料',conversations:'咨询与反馈',jobs:'任务输入',graph:'临时执行记录',metadata:'账号与通知'}
function size(value:number) { return value<1024?`${value} B`:value<1024**2?`${Math.ceil(value/1024)} KB`:`${(value/1024**2).toFixed(2)} MB` }
export default function UsagePanel() {
  const [usage, setUsage] = useState<Usage | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const refresh = useCallback(async () => {
    setLoading(true); setError('')
    try { setUsage(await api<Usage>('/usage')) } catch (err) { setError((err as Error).message) } finally { setLoading(false) }
  }, [])
  useEffect(() => { const timer = setTimeout(() => { void refresh() }, 0); return () => clearTimeout(timer) }, [refresh])
  return <div className="love-settings-note love-usage"><h2>今日用量</h2>{error && <p role="alert">{error}</p>}{usage && <><div className="love-usage-meters"><label>分析 / 识别任务 <strong>{usage.requests} / {usage.request_limit}</strong><progress value={usage.requests} max={usage.request_limit} /></label><label>模型 Token 预算 <strong>{(usage.charged_tokens + usage.reserved_tokens).toLocaleString()} / {usage.token_limit.toLocaleString()}</strong><progress value={usage.charged_tokens + usage.reserved_tokens} max={usage.token_limit} /></label></div><p>已记账 {usage.charged_tokens.toLocaleString()} Token · 执行中预留 {usage.reserved_tokens.toLocaleString()} Token</p><p>{usage.usage_by_purpose.map(row => `${purposeNames[row.purpose] ?? row.purpose} ${row.calls} 次`).join(' · ') || '今天还没有调用模型'}</p><small>额度重置：{new Date(usage.resets_at).toLocaleString('zh-CN')}。{usage.cost_note}。</small>{usage.storage && <section className="love-storage" aria-label="空间容量"><h3>空间容量</h3><div className="love-usage-meters"><label>已用与执行预留 <strong>{size(usage.storage.used_bytes+usage.storage.reserved_bytes)} / {size(usage.storage.limit_bytes)}</strong><progress value={usage.storage.used_bytes+usage.storage.reserved_bytes} max={usage.storage.limit_bytes} /></label></div><p>已保存 {size(usage.storage.used_bytes)} · 执行预留 {size(usage.storage.reserved_bytes)} · 可新增 {size(usage.storage.remaining_bytes)}</p><dl>{Object.entries(usage.storage.by_category).map(([key,value])=><div key={key}><dt>{categoryNames[key] ?? key}</dt><dd>{size(value)}</dd></div>)}</dl><small>新分析会预留 {size(usage.storage.task_reserve_bytes)} 执行空间，结束后释放未用部分。接近上限时会提前清理已完成任务的临时状态，保留咨询结果；你也可以先导出，再删除旧记录。</small></section>}</>}<button disabled={loading} onClick={() => void refresh()}>{loading ? '正在读取…' : '刷新用量'}</button></div>
}
