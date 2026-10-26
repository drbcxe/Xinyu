import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'

import { getArtifacts, uploadArtifact } from '../api'
import type { Artifact, Project } from '../types'

const MAX_BYTES = 5 * 1024 * 1024

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

export default function ArtifactPanel({ project }: { project: Project }) {
  const navigate = useNavigate()
  const base = `/projects/${encodeURIComponent(project.id)}`
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState('')
  const [revision, setRevision] = useState(0)
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const request = useRef<AbortController | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const items = await getArtifacts(project.id, controller.signal)
        if (!controller.signal.aborted) setArtifacts(items)
      } catch (error) {
        if (!controller.signal.aborted) setListError(messageOf(error))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [project.id, revision])
  useEffect(() => () => request.current?.abort(), [])

  function refresh() { setLoading(true); setListError(''); setRevision((count) => count + 1) }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (request.current || loading) return
    setUploadError('')
    if (!file) { setUploadError('请先选择一个 CSV 文件。'); return }
    if (!file.name.toLowerCase().endsWith('.csv')) { setUploadError('目前只支持 .csv 文件。'); return }
    if (file.size > MAX_BYTES) { setUploadError('文件不能超过 5 MiB。'); return }
    const controller = new AbortController()
    request.current = controller
    setUploading(true)
    try {
      const result = await uploadArtifact(project.id, file, controller.signal)
      if (controller.signal.aborted) return
      const notice = result.reused
        ? `相同内容已存在，已打开原文件「${result.original_name}」，没有重复导入。`
        : `已保存「${result.original_name}」，导入 ${result.row_count} 条实验记录。`
      navigate(`${base}/files/${result.id}`, { state: { notice } })
    } catch (error) {
      if (!controller.signal.aborted) setUploadError(messageOf(error))
    } finally {
      request.current = null
      if (!controller.signal.aborted) setUploading(false)
    }
  }

  return (
    <section className="files-page" aria-labelledby="files-heading">
      <div className="page-heading artifact-heading">
        <div><p className="eyebrow">EXPERIMENT RECORDS</p><h1 id="files-heading">文件与实验</h1><p className="page-description">上传实验文件，再打开文件查看具体记录。</p></div>
        <button className="refresh-button" type="button" disabled={loading || uploading} onClick={refresh}>{loading ? '读取中…' : '刷新文件'}</button>
      </div>
      <div className="artifact-grid">
        <section className="project-panel upload-panel" aria-labelledby="upload-heading">
          <h2 id="upload-heading">上传实验 CSV</h2>
          <p className="form-intro">UTF-8 / UTF-8 BOM，最大 5 MiB、10000 条记录。文件中每行的 project_id 必须为 {project.id}。</p>
          <form onSubmit={(event) => { void submit(event) }}>
            <label htmlFor="experiment-csv">选择文件</label>
            <input ref={fileInput} id="experiment-csv" type="file" name="file" accept=".csv,text/csv" required disabled={uploading} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setUploadError('') }} />
            {file && <p className="field-hint">已选：{file.name} · {(file.size / 1024).toFixed(1)} KiB</p>}
            {uploadError && <div className="error-panel form-error" role="alert">{uploadError}</div>}
            <button className="primary-button" type="submit" disabled={uploading || loading || !file}>{uploading ? '校验并导入中…' : '上传并查看实验'}</button>
          </form>
          <p className="field-hint">相同项目内按文件内容去重。改过内容的 CSV 会保存为新的文件记录。</p>
          <a className="text-button" href="/runs_demo.csv" download>下载合成演示 CSV（适用 P001） ↓</a>
        </section>
        <section className="project-panel" aria-labelledby="saved-files-heading" aria-busy={loading}>
          <div className="panel-title-row"><h2 id="saved-files-heading">已保存文件</h2><span className="section-label">{artifacts.length} 个文件</span></div>
          {loading && <p className="upload-notice" role="status">正在读取文件列表…</p>}
          {listError && <div className="error-panel" role="alert">{listError}{artifacts.length > 0 && <p>下方保留上次读取的文件列表。</p>}</div>}
          {!loading && !listError && artifacts.length === 0 && <p className="artifact-empty">还没有实验文件。上传后，可以打开文件查看实验记录。</p>}
          <ul className="project-list">{artifacts.map((artifact) => (
            <li key={artifact.id}><Link className="project-item file-entry" to={`${base}/files/${artifact.id}`}>
              <strong>{artifact.original_name}</strong>
              <span className="project-item-description">{artifact.row_count} 条实验 · {(artifact.size_bytes / 1024).toFixed(1)} KiB</span>
              <span className="project-time">{new Date(artifact.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })} · SHA256 {artifact.sha256.slice(0, 12)}…</span>
              <span className="card-entry">查看实验记录 →</span>
            </Link></li>
          ))}</ul>
        </section>
      </div>
    </section>
  )
}
