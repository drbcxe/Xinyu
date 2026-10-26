export type Candidate = { kind: 'memory' | 'reminder'; title: string; quote: string }
export type Plan = { title: string; budget: string; schedule: string[]; reason: string; needs_confirmation: string[] }
export type Trace = { tool: string; status: string; milliseconds: number; result: unknown }
export type RagSource = { citation_id: string; source_id: string; revision: number; kind: 'memory' | 'chat_log'; title: string; offset: number; end_offset: number; quote: string; retrievers: string[]; vector_score?: number; bm25_score?: number; rrf_score?: number }
export type Retrieval = { sources: RagSource[]; mode: string; warnings: string[]; milliseconds: number; reranked?: boolean }
export type GraphStep = { node: string; milliseconds: number; mode?: string; sources?: number; valid?: boolean }
export type Advice = { transcript?: string; analysis: string; uncertainty: string; replies: string[]; candidates: Candidate[]; date_plans?: Plan[]; tool_trace?: Trace[]; sources?: RagSource[]; retrieved_sources?: RagSource[]; graph_trace?: GraphStep[]; retrieval_trace?: Retrieval[]; execution?: { engine: string; checkpointed: boolean; resumed: boolean; model_steps: number; tool_calls: number; usage: Record<string, number> } }
export type Turn = { id: string; content: string; result: Advice; created_at: string }
export type Item = { id: string; kind: 'memory' | 'reminder' | 'date_plan' | 'chat_log'; title: string; quote: string; source_turn_id: string | null; due_at: string | null; status: 'active' | 'completed' | 'cancelled'; revision: number; details: Partial<Plan> & { text?: string } }
export type Job = { id: string; status: 'queued' | 'running' | 'succeeded' | 'failed'; error: string; turn_id: string | null; created_at: string }
export type Notice = { id: string; title: string; status: string; weixin_status: string; error: string; created_at: string }
export type Profile = { partner_name: string; style: string }
export type State = { profile: Profile; turns: Turn[]; items: Item[]; jobs: Job[]; notices: Notice[]; supports_images: boolean; server_time: string; worker_online: boolean; account: { username: string | null }; weixin: { status: string; interface_ready: boolean } }

export class ApiError extends Error {
  code: string
  details: Record<string, unknown>
  constructor(message: string, code: string, details: unknown) {
    super(message)
    this.name = 'ApiError'; this.code = code
    this.details = details && typeof details === 'object' && !Array.isArray(details) ? details as Record<string, unknown> : {}
  }
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const form = body instanceof FormData
  const response = await fetch(`/api/relationship${path}`, {
    method, credentials: 'same-origin',
    headers: body && !form ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? form ? body : JSON.stringify(body) : undefined,
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(data?.error?.message ?? '请求失败，请检查后端连接', data?.error?.code ?? 'REQUEST_FAILED', data?.error?.details)
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
