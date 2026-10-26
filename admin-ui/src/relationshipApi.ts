export type Candidate = { kind: 'memory' | 'reminder'; title: string; quote: string }
export type Plan = { title: string; budget: string; schedule: string[]; reason: string; needs_confirmation: string[] }
export type Trace = { tool: string; status: string; milliseconds: number; result: unknown }
export type Advice = { transcript?: string; analysis: string; uncertainty: string; replies: string[]; candidates: Candidate[]; date_plans?: Plan[]; tool_trace?: Trace[] }
export type Turn = { id: string; content: string; result: Advice; created_at: string }
export type Item = { id: string; kind: 'memory' | 'reminder' | 'date_plan' | 'chat_log'; title: string; quote: string; source_turn_id: string | null; due_at: string | null; status: 'active' | 'completed' | 'cancelled'; revision: number; details: Partial<Plan> & { text?: string } }
export type Job = { id: string; status: 'queued' | 'running' | 'succeeded' | 'failed'; error: string; turn_id: string | null; created_at: string }
export type Notice = { id: string; title: string; status: string; weixin_status: string; error: string; created_at: string }
export type Profile = { partner_name: string; style: string }
export type State = { profile: Profile; turns: Turn[]; items: Item[]; jobs: Job[]; notices: Notice[]; supports_images: boolean; server_time: string; worker_online: boolean; account: { username: string | null }; weixin: { status: string; interface_ready: boolean } }

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const form = body instanceof FormData
  const response = await fetch(`/api/relationship${path}`, {
    method, credentials: 'same-origin',
    headers: body && !form ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? form ? body : JSON.stringify(body) : undefined,
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(data?.error?.message ?? '请求失败，请检查后端连接')
  if (!data) throw new Error('接口未返回 JSON，请检查 /api 代理')
  return data as T
}

export function localDate(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}

export const toolNames: Record<string, string> = {
  search_memories: '检索已确认偏好', get_upcoming_items: '查询重要事项',
  propose_memory: '提出记忆候选', propose_reminder: '提出提醒候选',
  plan_date_context: '读取约会约束与偏好', search_chat_history: '检索导入聊天',
}
