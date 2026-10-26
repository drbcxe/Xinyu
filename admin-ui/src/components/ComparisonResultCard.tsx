import { lazy, Suspense, useId } from 'react'
import type { ComparisonResult } from '../types'

const ComparisonChart = lazy(() => import('./ComparisonChart'))
const outcomeLabels = { improved: '候选方案改善', regressed: '候选方案退步', tied: '两组结果持平' }

export default function ComparisonResultCard({ result }: { result: ComparisonResult }) {
  const headingId = useId()
  const calculation = result.comparison
  return (
    <section className="comparison-result" aria-labelledby={headingId}>
      <div className="panel-title-row">
        <h3 id={headingId}>{result.metric.name} 比较结果</h3>
        <span className={'badge ' + (calculation.outcome === 'improved' ? 'green' : calculation.outcome === 'regressed' ? 'red' : 'neutral')}>
          {outcomeLabels[calculation.outcome]}
        </span>
      </div>
      <p className="comparison-context">{result.metric.unit} · {result.metric.optimization === 'min' ? '越小越好' : '越大越好'}</p>
      <div className="comparison-values">
        <div><span>基线 · {result.baseline.run_id}</span><strong>{result.baseline.value}</strong><small>{result.baseline.model_name}</small></div>
        <div><span>候选 · {result.candidate.run_id}</span><strong>{result.candidate.value}</strong><small>{result.candidate.model_name}</small></div>
        <div className="improvement-value"><span>相对改善</span><strong>{calculation.improvement_pct === null ? '不适用' : `${calculation.improvement_pct}%`}</strong><small>正值表示改善，负值表示退步</small></div>
      </div>
      <Suspense fallback={<p className="artifact-empty">正在加载指标图表…</p>}><ComparisonChart result={result} /></Suspense>
      <dl className="comparison-calculation">
        <div><dt>候选 − 基线</dt><dd>{calculation.candidate_minus_baseline}</dd></div>
        <div><dt>按优化方向计算的改善量</dt><dd>{calculation.improvement}</dd></div>
        <div><dt>当前比较中表现更好的实验</dt><dd>{calculation.better_run_id ?? '持平'}</dd></div>
      </dl>
      {calculation.improvement_pct === null && <p className="comparison-scope">基线为零或负数，本次只展示绝对变化，不计算相对百分比。</p>}
      <p className="comparison-scope">{calculation.scope}。</p>
      <details className="comparison-evidence">
        <summary>查看比较来源与评估条件</summary>
        <dl className="detail-list">
          <div><dt>原始文件</dt><dd>{result.provenance.original_name}</dd></div>
          <div><dt>项目 / 文件 ID</dt><dd>{result.project_id} / {result.artifact_id}</dd></div>
          <div><dt>基线来源</dt><dd>{result.baseline.run_id} · CSV 第 {result.baseline.source_line} 行</dd></div>
          <div><dt>候选来源</dt><dd>{result.candidate.run_id} · CSV 第 {result.candidate.source_line} 行</dd></div>
          <div><dt>数据版本</dt><dd>{result.provenance.dataset_version}</dd></div>
          <div><dt>划分 / 协议</dt><dd>{result.provenance.split_id} / {result.provenance.protocol_version}</dd></div>
          <div><dt>随机种子</dt><dd>{result.provenance.seed}</dd></div>
          <div><dt>SHA256</dt><dd><code>{result.provenance.sha256}</code></dd></div>
          <div><dt>结果编号</dt><dd>{result.id}</dd></div>
          <div><dt>保存时间</dt><dd>{new Date(result.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}</dd></div>
        </dl>
      </details>
    </section>
  )
}

