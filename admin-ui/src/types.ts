export interface HealthStatus {
  status: string
  version: string
}

export interface SettingsStatus {
  model_configured: boolean
  model_name: string
  base_url_host: string
}

export interface Project {
  id: string
  name: string
  description: string
  created_at: string
}

export interface ProjectCreate {
  id: string
  name: string
  description: string
}

export interface Artifact {
  id: string
  project_id: string
  original_name: string
  sha256: string
  size_bytes: number
  row_count: number
  created_at: string
}

export interface ArtifactUploadResult extends Artifact {
  reused: boolean
}

export interface ExperimentRun {
  id: string
  project_id: string
  artifact_id: string
  run_id: string
  model_name: string
  dataset_version: string
  split_id: string
  protocol_version: string
  metric_name: string
  metric_unit: string
  optimization: string
  metric_value: string
  seed: string
  source_line: number
}

export type ComparisonOutcome = 'improved' | 'regressed' | 'tied'

export interface ComparedRun {
  run_id: string
  model_name: string
  value: string
  source_line: number
}

export interface ComparisonResult {
  id: string
  project_id: string
  artifact_id: string
  created_at: string
  status: 'succeeded'
  metric: { name: string; unit: string; optimization: 'min' | 'max' }
  baseline: ComparedRun
  candidate: ComparedRun
  comparison: {
    candidate_minus_baseline: string
    improvement: string
    improvement_pct: string | null
    outcome: ComparisonOutcome
    better_run_id: string | null
    scope: string
  }
  provenance: {
    original_name: string
    sha256: string
    dataset_version: string
    split_id: string
    protocol_version: string
    metric_name: string
    metric_unit: string
    optimization: 'min' | 'max'
    seed: string
  }
}

export interface ChatThread {
  id: string
  project_id: string
  title: string
  artifact_id: string | null
  active_run_id: string | null
  created_at: string
  updated_at: string
}

export interface ChatEvent {
  sequence: number
  type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface ChatRun {
  id: string
  request_id: string
  project_id: string
  thread_id: string
  artifact_id: string | null
  status: 'running' | 'succeeded' | 'failed'
  reply: string | null
  error: { code: string; message: string } | null
  evidence: ComparisonResult[]
  events: ChatEvent[]
  model_name: string
  usage: Record<string, unknown> | null
  duration_ms: number | null
  created_at: string
  finished_at: string | null
}

export interface ChatMessage {
  id: string
  run_id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export interface ChatMessages {
  items: ChatMessage[]
  active_run_id: string | null
  runs: ChatRun[]
}

export interface ChatRunInput {
  message: string
  request_id: string
  artifact_id: string | null
}

export type MemoryKey = 'default_baseline' | 'preferred_metric' | 'response_length' | 'report_language'

export interface ProjectMemory {
  id: string
  project_id: string
  key: MemoryKey
  value: string
  revision: number
  is_active: boolean
  supersedes_id: string | null
  source: 'manual' | 'agent'
  source_quote: string
  source_message_id: string | null
  created_at: string
}
