import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { ApiError, createComparison, getComparisons } from '../api'
import ComparisonResultCard from './ComparisonResultCard'
import type { Artifact, ComparisonResult, ExperimentRun } from '../types'

const outcomeLabels = { improved: '候选方案改善', regressed: '候选方案退步', tied: '两组结果持平' }
const fieldLabels: Record<string, string> = {
  project_id: '项目编号', dataset_version: '数据版本', split_id: '数据划分',
  protocol_version: '评估协议', metric_name: '指标名称', metric_unit: '指标单位',
  optimization: '优化方向', seed: '随机种子',
}
interface Mismatch { field: string; baseline: string; candidate: string }

function mismatchesOf(error: unknown): Mismatch[] {
  if (!(error instanceof ApiError) || error.code !== 'INCOMPARABLE_RUNS'
    || typeof error.details !== 'object' || error.details === null || Array.isArray(error.details)) return []
  return Object.entries(error.details).flatMap(([field, pair]) => {
    if (typeof pair !== 'object' || pair === null || !('baseline' in pair) || !('candidate' in pair)
      || typeof pair.baseline !== 'string' || typeof pair.candidate !== 'string') return []
    return [{ field: fieldLabels[field] ?? field, baseline: pair.baseline, candidate: pair.candidate }]
  })
}

export default function ComparisonPanel({ projectId, artifact, runs }: { projectId: string; artifact: Artifact; runs: ExperimentRun[] }) {
  const [baselineId, setBaselineId] = useState(runs[0]?.run_id ?? '')
  const [candidateId, setCandidateId] = useState(runs[1]?.run_id ?? '')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [mismatches, setMismatches] = useState<Mismatch[]>([])
  const [result, setResult] = useState<ComparisonResult | null>(null)
  const [history, setHistory] = useState<ComparisonResult[]>([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState('')
  const [historyRevision, setHistoryRevision] = useState(0)
  const request = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const items = await getComparisons(projectId, artifact.id, controller.signal)
        if (!controller.signal.aborted) setHistory(items)
      } catch (reason) {
        if (!controller.signal.aborted) setHistoryError(reason instanceof Error ? reason.message : '无法读取比较记录')
      } finally {
        if (!controller.signal.aborted) setHistoryLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [projectId, artifact.id, historyRevision])

  useEffect(() => () => request.current?.abort(), [])

  function clearResult() { setResult(null); setError(''); setMismatches([]) }

  function refreshHistory() {
    setHistoryLoading(true)
    setHistoryError('')
    setHistoryRevision((current) => current + 1)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (request.current || historyLoading || !baselineId || !candidateId || baselineId === candidateId) return
    clearResult()
    const controller = new AbortController()
    request.current = controller
    setPending(true)
    try {
      const saved = await createComparison(projectId, artifact.id, baselineId, candidateId, controller.signal)
      if (controller.signal.aborted) return
      setResult(saved)
      setHistory((current) => [saved, ...current.filter((item) => item.id !== saved.id)].slice(0, 20))
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : '比较失败，请稍后重试')
        setMismatches(mismatchesOf(reason))
      }
    } finally {
      request.current = null
      if (!controller.signal.aborted) setPending(false)
    }
  }

  function showHistory(item: ComparisonResult) {
    setBaselineId(item.baseline.run_id)
    setCandidateId(item.candidate.run_id)
    setError('')
    setMismatches([])
    setResult(item)
  }

  return (
    <section className="project-panel comparison-panel" aria-labelledby="comparison-heading">
      <p className="section-label">FROM RECORDS TO INSIGHT</p>
      <h3 id="comparison-heading">比较两组实验</h3>
      <p className="form-intro">选择当前文件中的基线和候选方案。后端核对评估条件后计算变化，并保存结果。</p>
      {runs.length < 2 && <p className="comparison-scope">当前文件至少需要两条实验记录才能进行比较。</p>}
      <form className="comparison-form" onSubmit={(event) => { void submit(event) }}>
        <fieldset disabled={pending}>
          <div><label htmlFor="baseline-run">基线实验</label><select id="baseline-run" value={baselineId} onChange={(event) => { setBaselineId(event.target.value); clearResult() }} required>
            {runs.map((run) => <option key={run.id} value={run.run_id}>{run.run_id} · {run.model_name}</option>)}
          </select></div>
          <div><label htmlFor="candidate-run">候选实验</label><select id="candidate-run" value={candidateId} onChange={(event) => { setCandidateId(event.target.value); clearResult() }} required>
            {runs.map((run) => <option key={run.id} value={run.run_id}>{run.run_id} · {run.model_name}</option>)}
          </select></div>
        </fieldset>
        <button className="primary-button" type="submit" disabled={pending || historyLoading || runs.length < 2 || !baselineId || !candidateId || baselineId === candidateId}>
          {pending ? '正在核对并计算…' : '比较并保存结果'}
        </button>
      </form>
      {baselineId && baselineId === candidateId && <p className="field-hint">基线和候选需要选择不同的实验。</p>}
      <div role="status" aria-live="polite" className="comparison-status">{pending ? '正在核对评估条件并保存比较结果…' : result ? '已读取保存的比较结果。' : ''}</div>
      {error && <div className="error-panel" role="alert">
        <strong>本次比较未完成</strong><p>{error}</p>
        {mismatches.length > 0 && <div className="runs-table-scroll"><table className="mismatch-table">
          <caption>不一致的评估条件</caption><thead><tr><th scope="col">条件</th><th scope="col">基线</th><th scope="col">候选</th></tr></thead>
          <tbody>{mismatches.map((item) => <tr key={item.field}><th scope="row">{item.field}</th><td>{item.baseline}</td><td>{item.candidate}</td></tr>)}</tbody>
        </table></div>}
      </div>}
      {result && <ComparisonResultCard result={result} />}

      <section className="comparison-history" aria-labelledby="history-heading">
        <div className="panel-title-row"><h3 id="history-heading">最近比较记录</h3>
          <button className="refresh-button" type="button" disabled={historyLoading || pending} onClick={refreshHistory}>{historyLoading ? '读取中…' : '刷新记录'}</button>
        </div>
        <p className="field-hint">当前文件最近 20 条成功记录，点击可查看已保存的结果。</p>
        {historyError && <div className="error-panel" role="alert">{historyError}</div>}
        {!historyLoading && !historyError && history.length === 0 && <p className="artifact-empty">还没有比较记录。</p>}
        <ul className="comparison-history-list">{history.map((item) => (
          <li key={item.id}><button type="button" disabled={pending} className={'history-item' + (result?.id === item.id ? ' selected' : '')} aria-pressed={result?.id === item.id} onClick={() => showHistory(item)}>
            <span><strong>{item.baseline.run_id} → {item.candidate.run_id}</strong><small>{new Date(item.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}</small></span>
            <span>{outcomeLabels[item.comparison.outcome]}<small>{item.comparison.improvement_pct === null ? '未计算相对百分比' : `相对改善 ${item.comparison.improvement_pct}%`}</small></span>
          </button></li>
        ))}</ul>
      </section>
    </section>
  )
}
