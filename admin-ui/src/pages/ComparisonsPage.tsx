import { useEffect, useState } from 'react'
import { Link, Navigate, useOutletContext, useSearchParams } from 'react-router'

import { getArtifactRuns, getArtifacts } from '../api'
import ComparisonPanel from '../components/ComparisonPanel'
import type { Artifact, ExperimentRun, Project } from '../types'

function ComparisonWorkspace({ projectId, artifact }: { projectId: string; artifact: Artifact }) {
  const [revision, setRevision] = useState(0)
  const [snapshot, setSnapshot] = useState<{ revision: number; items: ExperimentRun[]; error: string } | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const items = await getArtifactRuns(projectId, artifact.id, controller.signal)
        if (!controller.signal.aborted) setSnapshot({ revision, items, error: '' })
      } catch (error) {
        if (!controller.signal.aborted) setSnapshot({ revision, items: [], error: error instanceof Error ? error.message : '无法读取实验记录' })
      }
    }
    void load()
    return () => controller.abort()
  }, [projectId, artifact.id, revision])
  if (snapshot?.revision !== revision) return <p className="route-message" role="status">正在读取可选实验…</p>
  if (snapshot.error) return <div className="error-panel" role="alert"><p>{snapshot.error}</p><button className="text-button" type="button" onClick={() => setRevision((count) => count + 1)}>重新读取 →</button></div>
  return <ComparisonPanel projectId={projectId} artifact={artifact} runs={snapshot.items} />
}

export default function ComparisonsPage() {
  const project = useOutletContext<Project>()
  const [params, setParams] = useSearchParams()
  const [revision, setRevision] = useState(0)
  const [snapshot, setSnapshot] = useState<{ key: string; items: Artifact[]; error: string } | null>(null)
  const key = `${project.id}:${revision}`
  const base = `/projects/${encodeURIComponent(project.id)}`
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const items = await getArtifacts(project.id, controller.signal)
        if (!controller.signal.aborted) setSnapshot({ key, items, error: '' })
      } catch (error) {
        if (!controller.signal.aborted) setSnapshot({ key, items: [], error: error instanceof Error ? error.message : '无法读取文件列表' })
      }
    }
    void load()
    return () => controller.abort()
  }, [project.id, key])
  const current = snapshot?.key === key ? snapshot : null
  const artifactId = params.get('artifact')
  const selected = current?.items.find((item) => item.id === artifactId)
  if (current && !current.error && !artifactId && current.items.length > 0) {
    return <Navigate to={`${base}/comparisons?artifact=${encodeURIComponent(current.items[0].id)}`} replace />
  }
  return (
    <>
      <div className="page-heading"><div><p className="eyebrow">FROM RECORDS TO INSIGHT</p><h1>实验比较</h1><p className="page-description">在同一份实验文件内选择两组结果，核对条件后再比较。</p></div><Link className="refresh-button" to={`${base}/files`}>管理实验文件 →</Link></div>
      {!current ? <p className="route-message" role="status">正在读取文件列表…</p>
        : current.error ? <div className="error-panel route-message" role="alert"><p>{current.error}</p><button className="text-button" type="button" onClick={() => setRevision((count) => count + 1)}>重新读取 →</button></div>
          : current.items.length === 0 ? <section className="project-panel empty-projects route-message"><h2>先上传一份实验文件</h2><p>导入至少两条实验记录后，就能选择基线和候选进行比较。</p><Link className="text-button" to={`${base}/files`}>前往上传 CSV →</Link></section>
            : <>
              <section className="project-panel comparison-file-picker"><label htmlFor="comparison-file">当前实验文件</label><select id="comparison-file" value={selected?.id ?? ''} onChange={(event) => setParams({ artifact: event.target.value })}>
                {!selected && <option value="" disabled>请选择一个有效文件</option>}{current.items.map((item) => <option key={item.id} value={item.id}>{item.original_name} · {item.row_count} 条 · {item.sha256.slice(0, 8)}</option>)}
              </select>{selected && <Link className="text-button" to={`${base}/files/${selected.id}`}>查看该文件的实验记录 →</Link>}</section>
              {!selected ? <div className="error-panel" role="alert">当前地址指定的文件不属于这个项目或已不存在，请从上方重新选择。</div> : <ComparisonWorkspace key={`${project.id}:${selected.id}`} projectId={project.id} artifact={selected} />}
            </>}
    </>
  )
}
