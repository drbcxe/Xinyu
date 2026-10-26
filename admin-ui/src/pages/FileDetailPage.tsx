import { useEffect, useState } from 'react'
import { Link, useOutletContext, useParams } from 'react-router'

import { getArtifact, getArtifactRuns } from '../api'
import NavigationNotice from '../components/NavigationNotice'
import RunsTable from '../components/RunsTable'
import type { Artifact, ExperimentRun, Project } from '../types'

interface Snapshot { key: string; artifact: Artifact | null; runs: ExperimentRun[]; error: string }

export default function FileDetailPage() {
  const project = useOutletContext<Project>()
  const { artifactId = '' } = useParams()
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [revision, setRevision] = useState(0)
  const key = `${project.id}:${artifactId}:${revision}`
  const base = `/projects/${encodeURIComponent(project.id)}`
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const [artifact, runs] = await Promise.all([getArtifact(project.id, artifactId, controller.signal), getArtifactRuns(project.id, artifactId, controller.signal)])
        if (!controller.signal.aborted) setSnapshot({ key, artifact, runs, error: '' })
      } catch (error) {
        if (!controller.signal.aborted) setSnapshot({ key, artifact: null, runs: [], error: error instanceof Error ? error.message : '无法读取实验文件' })
      }
    }
    void load()
    return () => controller.abort()
  }, [project.id, artifactId, key])
  const current = snapshot?.key === key ? snapshot : null
  return (
    <>
      <Link className="back-link" to={`${base}/files`}>← 返回文件列表</Link>
      <NavigationNotice />
      {!current ? <p className="route-message" role="status">正在读取实验文件…</p>
        : !current.artifact ? <section className="route-message"><h1>暂时无法打开文件</h1><div className="error-panel" role="alert">{current.error}</div><button className="refresh-button" type="button" onClick={() => setRevision((count) => count + 1)}>重新读取</button></section>
          : <>
            <div className="page-heading"><div><p className="eyebrow">EXPERIMENT RECORDS</p><h1>{current.artifact.original_name}</h1><p className="page-description">{current.artifact.row_count} 条实验 · {(current.artifact.size_bytes / 1024).toFixed(1)} KiB</p></div><Link className="primary-link" to={`${base}/comparisons?artifact=${encodeURIComponent(artifactId)}`}>比较此文件中的实验 →</Link></div>
            <section className="project-panel file-records-panel"><div className="panel-title-row"><h2>实验记录</h2><button className="refresh-button" type="button" onClick={() => setRevision((count) => count + 1)}>重新读取</button></div><p className="file-provenance">文件 SHA256：<code>{current.artifact.sha256}</code></p><RunsTable key={artifactId} runs={current.runs} /></section>
          </>}
    </>
  )
}
