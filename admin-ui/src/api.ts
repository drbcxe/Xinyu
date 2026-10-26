import type { Artifact, ArtifactUploadResult, ChatEvent, ChatMessage, ChatMessages, ChatRun, ChatRunInput, ChatThread, ComparedRun, ComparisonResult, ExperimentRun, HealthStatus, MemoryKey, Project, ProjectCreate, ProjectMemory, SettingsStatus } from './types'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown

  constructor(message: string, status: number, code: string, details: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

async function requestJson<T>(
  path: string,
  parse: (value: unknown) => T,
  signal?: AbortSignal,
  options: RequestInit = {},
  timeoutMs = 10_000,
): Promise<T> {
  try {
    const timeout = AbortSignal.timeout(timeoutMs)
    const response = await fetch(path, {
      ...options,
      credentials: 'include',
      cache: 'no-store',
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    })

    if (!response.ok) {
      const body: unknown = response.headers.get('content-type')?.includes('application/json')
        ? await response.json() : null
      const error = isRecord(body) && isRecord(body.error) ? body.error : null
      const code = error && typeof error.code === 'string' ? error.code : ''
      let message = error && typeof error.message === 'string'
        ? error.message : '请求失败：HTTP ' + response.status
      if (code === 'PROJECT_EXISTS') message = '这个项目编号已存在，请换一个编号。'
      if (code === 'VALIDATION_ERROR') message = '请求参数不符合要求，请检查填写内容或所选文件。'
      if (response.status >= 500 && !error) message = '后端暂时无法处理请求，请确认数据库已启动、迁移已完成，再刷新页面。'
      throw new ApiError(message, response.status, code, error?.details)
    }
    if (!response.headers.get('content-type')?.includes('application/json')) {
      throw new Error('接口未返回 JSON，请检查 /api 代理配置')
    }
    return parse(await response.json())
  } catch (error) {
    if (signal?.aborted) throw error
    if (error instanceof Error && error.name === 'TimeoutError') {
      throw new Error(options.method === 'POST'
        ? `提交超过 ${timeoutMs / 1000} 秒，记录可能已经保存。请先刷新列表确认，再决定是否重新提交。`
        : `请求超过 ${timeoutMs / 1000} 秒，请确认后端已启动后重试`, { cause: error })
    }
    if (error instanceof TypeError) {
      throw new Error(options.method === 'POST'
        ? '连接中断，无法确认记录是否保存。请先刷新列表，并检查后端运行状态。'
        : '无法连接服务，请检查网络和后端运行状态', { cause: error })
    }
    throw error
  }
}

export function getHealth(signal?: AbortSignal): Promise<HealthStatus> {
  return requestJson('/api/health', (value) => {
    if (!isRecord(value) || typeof value.status !== 'string' || typeof value.version !== 'string') {
      throw new Error('健康检查接口返回格式不符合约定')
    }
    return { status: value.status, version: value.version }
  }, signal)
}

export function getSettingsStatus(signal?: AbortSignal): Promise<SettingsStatus> {
  return requestJson('/api/settings/status', (value) => {
    if (
      !isRecord(value)
      || typeof value.model_configured !== 'boolean'
      || typeof value.model_name !== 'string'
      || typeof value.base_url_host !== 'string'
    ) {
      throw new Error('模型配置接口返回格式不符合约定')
    }
    return {
      model_configured: value.model_configured,
      model_name: value.model_name,
      base_url_host: value.base_url_host,
    }
  }, signal)
}

function parseProject(value: unknown): Project {
  if (
    !isRecord(value)
    || typeof value.id !== 'string'
    || typeof value.name !== 'string'
    || typeof value.description !== 'string'
    || typeof value.created_at !== 'string'
    || Number.isNaN(Date.parse(value.created_at))
  ) {
    throw new Error('项目接口返回格式不符合约定')
  }
  return {
    id: value.id,
    name: value.name,
    description: value.description,
    created_at: value.created_at,
  }
}

export function getProjects(signal?: AbortSignal): Promise<Project[]> {
  return requestJson('/api/projects', (value) => {
    if (!isRecord(value) || !Array.isArray(value.items)) {
      throw new Error('项目列表接口返回格式不符合约定')
    }
    return value.items.map(parseProject)
  }, signal)
}

export function createProject(data: ProjectCreate, signal?: AbortSignal): Promise<Project> {
  return requestJson('/api/projects', parseProject, signal, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export function getProject(projectId: string, signal?: AbortSignal): Promise<Project> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}`, parseProject, signal)
}

function textField(value: Record<string, unknown>, key: string): string {
  if (typeof value[key] !== 'string') throw new Error('文件或实验接口返回格式不符合约定')
  return value[key]
}

function parseArtifact(value: unknown): Artifact {
  if (
    !isRecord(value)
    || typeof value.size_bytes !== 'number' || !Number.isSafeInteger(value.size_bytes) || value.size_bytes < 0
    || typeof value.row_count !== 'number' || !Number.isSafeInteger(value.row_count) || value.row_count < 1
  ) throw new Error('文件接口返回格式不符合约定')
  const createdAt = textField(value, 'created_at')
  const sha256 = textField(value, 'sha256')
  if (Number.isNaN(Date.parse(createdAt)) || !/^[a-f0-9]{64}$/.test(sha256)) {
    throw new Error('文件接口返回格式不符合约定')
  }
  return {
    id: textField(value, 'id'), project_id: textField(value, 'project_id'),
    original_name: textField(value, 'original_name'), sha256,
    size_bytes: value.size_bytes, row_count: value.row_count, created_at: createdAt,
  }
}

function parseRun(value: unknown): ExperimentRun {
  if (!isRecord(value) || typeof value.source_line !== 'number' || !Number.isSafeInteger(value.source_line) || value.source_line < 2) {
    throw new Error('实验接口返回格式不符合约定')
  }
  return {
    id: textField(value, 'id'), project_id: textField(value, 'project_id'),
    artifact_id: textField(value, 'artifact_id'), run_id: textField(value, 'run_id'),
    model_name: textField(value, 'model_name'), dataset_version: textField(value, 'dataset_version'),
    split_id: textField(value, 'split_id'), protocol_version: textField(value, 'protocol_version'),
    metric_name: textField(value, 'metric_name'), metric_unit: textField(value, 'metric_unit'),
    optimization: textField(value, 'optimization'), metric_value: textField(value, 'metric_value'),
    seed: textField(value, 'seed'), source_line: value.source_line,
  }
}

function parseItems<T>(value: unknown, parse: (item: unknown) => T): T[] {
  if (!isRecord(value) || !Array.isArray(value.items)) throw new Error('列表接口返回格式不符合约定')
  return value.items.map(parse)
}

export function getArtifacts(projectId: string, signal?: AbortSignal): Promise<Artifact[]> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/artifacts`,
    (value) => parseItems(value, parseArtifact), signal)
}

export function uploadArtifact(projectId: string, file: File, signal?: AbortSignal): Promise<ArtifactUploadResult> {
  const body = new FormData()
  body.append('file', file)
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/artifacts`, (value) => {
    if (!isRecord(value) || typeof value.reused !== 'boolean') throw new Error('上传接口返回格式不符合约定')
    return { ...parseArtifact(value), reused: value.reused }
  }, signal, { method: 'POST', body }, 60_000)
}

export function getArtifactRuns(projectId: string, artifactId: string, signal?: AbortSignal): Promise<ExperimentRun[]> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/runs`,
    (value) => parseItems(value, parseRun), signal)
}

export function getArtifact(projectId: string, artifactId: string, signal?: AbortSignal): Promise<Artifact> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}`, parseArtifact, signal)
}

function comparisonObject(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new Error('比较接口返回格式不符合约定')
  return value
}

function numericText(value: Record<string, unknown>, key: string): string {
  const text = textField(value, key)
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) {
    throw new Error('比较接口返回了无效数值')
  }
  return text
}

function optimization(value: Record<string, unknown>): 'min' | 'max' {
  if (value.optimization !== 'min' && value.optimization !== 'max') throw new Error('比较接口返回了无效优化方向')
  return value.optimization
}

function comparedRun(value: unknown): ComparedRun {
  const item = comparisonObject(value)
  if (typeof item.source_line !== 'number' || !Number.isSafeInteger(item.source_line) || item.source_line < 2) {
    throw new Error('比较接口返回了无效来源行号')
  }
  return {
    run_id: textField(item, 'run_id'), model_name: textField(item, 'model_name'),
    value: numericText(item, 'value'), source_line: item.source_line,
  }
}

function parseComparison(value: unknown): ComparisonResult {
  const item = comparisonObject(value)
  const metric = comparisonObject(item.metric)
  const calculation = comparisonObject(item.comparison)
  const source = comparisonObject(item.provenance)
  const createdAt = textField(item, 'created_at')
  const hash = textField(source, 'sha256')
  if (
    item.status !== 'succeeded' || Number.isNaN(Date.parse(createdAt)) || !/^[a-f0-9]{64}$/.test(hash)
    || !['improved', 'regressed', 'tied'].includes(String(calculation.outcome))
    || (calculation.improvement_pct !== null && typeof calculation.improvement_pct !== 'string')
    || (calculation.better_run_id !== null && typeof calculation.better_run_id !== 'string')
  ) throw new Error('比较接口返回格式不符合约定')
  const outcome = calculation.outcome
  if (outcome !== 'improved' && outcome !== 'regressed' && outcome !== 'tied') throw new Error('比较结果状态无效')
  const betterRunId = calculation.better_run_id
  if (betterRunId !== null && typeof betterRunId !== 'string') throw new Error('比较结果格式无效')
  return {
    id: textField(item, 'id'), project_id: textField(item, 'project_id'),
    artifact_id: textField(item, 'artifact_id'), created_at: createdAt, status: 'succeeded',
    metric: { name: textField(metric, 'name'), unit: textField(metric, 'unit'), optimization: optimization(metric) },
    baseline: comparedRun(item.baseline), candidate: comparedRun(item.candidate),
    comparison: {
      candidate_minus_baseline: numericText(calculation, 'candidate_minus_baseline'),
      improvement: numericText(calculation, 'improvement'),
      improvement_pct: calculation.improvement_pct === null ? null : numericText(calculation, 'improvement_pct'),
      outcome, better_run_id: betterRunId, scope: textField(calculation, 'scope'),
    },
    provenance: {
      original_name: textField(source, 'original_name'), sha256: hash,
      dataset_version: textField(source, 'dataset_version'), split_id: textField(source, 'split_id'),
      protocol_version: textField(source, 'protocol_version'), metric_name: textField(source, 'metric_name'),
      metric_unit: textField(source, 'metric_unit'), optimization: optimization(source), seed: textField(source, 'seed'),
    },
  }
}

export function createComparison(
  projectId: string, artifactId: string, baselineId: string, candidateId: string, signal?: AbortSignal,
): Promise<ComparisonResult> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/comparisons`, (value) => {
    const result = parseComparison(value)
    if (result.project_id !== projectId || result.artifact_id !== artifactId
      || result.baseline.run_id !== baselineId || result.candidate.run_id !== candidateId) {
      throw new Error('比较结果对应的项目、文件或实验与当前选择不一致')
    }
    return result
  }, signal, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ artifact_id: artifactId, baseline_run_id: baselineId, candidate_run_id: candidateId }),
  })
}

export function getComparisons(projectId: string, artifactId: string, signal?: AbortSignal): Promise<ComparisonResult[]> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/comparisons?artifact_id=${encodeURIComponent(artifactId)}`, (value) => {
    const items = parseItems(value, parseComparison)
    if (items.some((item) => item.project_id !== projectId || item.artifact_id !== artifactId)) {
      throw new Error('比较记录与当前项目或文件不一致')
    }
    return items
  }, signal)
}

function nullableText(item: Record<string, unknown>, key: string): string | null {
  return item[key] === null ? null : textField(item, key)
}

function parseThread(value: unknown): ChatThread {
  const item = comparisonObject(value)
  return {
    id: textField(item, 'id'), project_id: textField(item, 'project_id'), title: textField(item, 'title'),
    artifact_id: nullableText(item, 'artifact_id'), active_run_id: nullableText(item, 'active_run_id'),
    created_at: textField(item, 'created_at'), updated_at: textField(item, 'updated_at'),
  }
}

function parseChatEvent(value: unknown): ChatEvent {
  const item = comparisonObject(value)
  if (typeof item.sequence !== 'number' || !Number.isSafeInteger(item.sequence)) throw new Error('执行事件编号无效')
  return { sequence: item.sequence, type: textField(item, 'type'), payload: comparisonObject(item.payload), created_at: textField(item, 'created_at') }
}

function parseChatRun(value: unknown): ChatRun {
  const item = comparisonObject(value)
  if (item.status !== 'running' && item.status !== 'succeeded' && item.status !== 'failed') throw new Error('Agent 执行状态无效')
  if (!Array.isArray(item.evidence) || !Array.isArray(item.events) || (item.duration_ms !== null && typeof item.duration_ms !== 'number')) throw new Error('Agent 执行返回格式无效')
  const error = item.error === null ? null : comparisonObject(item.error)
  return {
    id: textField(item, 'id'), request_id: textField(item, 'request_id'), project_id: textField(item, 'project_id'),
    thread_id: textField(item, 'thread_id'), artifact_id: nullableText(item, 'artifact_id'), status: item.status,
    reply: nullableText(item, 'reply'), error: error ? { code: textField(error, 'code'), message: textField(error, 'message') } : null,
    evidence: item.evidence.map(parseComparison), events: item.events.map(parseChatEvent), model_name: textField(item, 'model_name'),
    usage: item.usage === null ? null : comparisonObject(item.usage), duration_ms: item.duration_ms,
    created_at: textField(item, 'created_at'), finished_at: nullableText(item, 'finished_at'),
  }
}

function parseChatMessage(value: unknown): ChatMessage {
  const item = comparisonObject(value)
  if (item.role !== 'user' && item.role !== 'assistant') throw new Error('聊天消息角色无效')
  return { id: textField(item, 'id'), run_id: textField(item, 'run_id'), role: item.role, content: textField(item, 'content'), created_at: textField(item, 'created_at') }
}

export function getThreads(projectId: string, signal?: AbortSignal): Promise<ChatThread[]> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/threads`, (value) => parseItems(value, parseThread), signal)
}

export function createThread(projectId: string, artifactId: string | null, signal?: AbortSignal): Promise<ChatThread> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/threads`, parseThread, signal, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: '新会话', artifact_id: artifactId }),
  })
}

export function getChatMessages(projectId: string, threadId: string, signal?: AbortSignal): Promise<ChatMessages> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/threads/${encodeURIComponent(threadId)}/messages`, (value) => {
    const item = comparisonObject(value)
    if (!Array.isArray(item.items) || !Array.isArray(item.runs)) throw new Error('聊天记录格式不符合约定')
    const runs = item.runs.map(parseChatRun)
    if (runs.some((run) => run.project_id !== projectId || run.thread_id !== threadId)) throw new Error('聊天记录与当前项目或会话不一致')
    return { items: item.items.map(parseChatMessage), active_run_id: nullableText(item, 'active_run_id'), runs }
  }, signal)
}

export function postChatRun(projectId: string, threadId: string, data: ChatRunInput, signal?: AbortSignal): Promise<{ run_id: string; status: string }> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/threads/${encodeURIComponent(threadId)}/runs`, (value) => {
    const item = comparisonObject(value)
    return { run_id: textField(item, 'run_id'), status: textField(item, 'status') }
  }, signal, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }, 360_000)
}

function parseMemory(value: unknown, projectId: string): ProjectMemory {
  const item = comparisonObject(value)
  const key = item.key
  if ((key !== 'default_baseline' && key !== 'preferred_metric' && key !== 'response_length' && key !== 'report_language')
    || typeof item.revision !== 'number' || !Number.isSafeInteger(item.revision) || item.revision < 1
    || typeof item.is_active !== 'boolean' || (item.source !== 'manual' && item.source !== 'agent')
    || item.project_id !== projectId) throw new Error('偏好记录格式或项目范围无效')
  return {
    id: textField(item, 'id'), project_id: projectId, key, value: textField(item, 'value'),
    revision: item.revision, is_active: item.is_active, source: item.source,
    supersedes_id: nullableText(item, 'supersedes_id'), source_quote: textField(item, 'source_quote'),
    source_message_id: nullableText(item, 'source_message_id'), created_at: textField(item, 'created_at'),
  }
}

export function getMemories(projectId: string, signal?: AbortSignal): Promise<ProjectMemory[]> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/memories`,
    (value) => parseItems(value, (row) => parseMemory(row, projectId)), signal)
}

export function getMemoryHistory(projectId: string, key: MemoryKey, signal?: AbortSignal): Promise<ProjectMemory[]> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/memories/${key}/history`,
    (value) => parseItems(value, (row) => parseMemory(row, projectId)), signal)
}

export function saveMemory(projectId: string, key: MemoryKey, value: string, expectedRevision: number, signal?: AbortSignal): Promise<ProjectMemory> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/memories/${key}`,
    (row) => parseMemory(row, projectId), signal, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value, expected_revision: expectedRevision }) })
}

export function deactivateMemory(projectId: string, key: MemoryKey, expectedRevision: number, signal?: AbortSignal): Promise<ProjectMemory> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/memories/${key}/deactivate`,
    (row) => parseMemory(row, projectId), signal, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_revision: expectedRevision }) })
}
