import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useParams } from 'react-router'

import { getProject } from '../api'
import type { Project } from '../types'

interface Snapshot { key: string; project: Project | null; error: string }

export default function ProjectLayout() {
  const { projectId = '' } = useParams()
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [revision, setRevision] = useState(0)
  const key = `${projectId}:${revision}`
  const valid = /^[A-Z][A-Z0-9_-]{0,31}$/.test(projectId)
  useEffect(() => {
    if (!valid) return
    const controller = new AbortController()
    async function load() {
      try {
        const project = await getProject(projectId, controller.signal)
        if (!controller.signal.aborted) setSnapshot({ key, project, error: '' })
      } catch (error) {
        if (!controller.signal.aborted) setSnapshot({ key, project: null, error: error instanceof Error ? error.message : '无法读取项目' })
      }
    }
    void load()
    return () => controller.abort()
  }, [projectId, valid, key])

  if (!valid) return <section className="route-message"><h1>项目地址无效</h1><Link className="text-button" to="/projects">返回项目列表 →</Link></section>
  if (snapshot?.key !== key) return <p className="route-message" role="status">正在打开项目 {projectId}…</p>
  if (!snapshot.project) return <section className="route-message"><h1>暂时无法打开项目</h1><div className="error-panel" role="alert">{snapshot.error}</div><div className="page-actions"><button className="refresh-button" type="button" onClick={() => setRevision((current) => current + 1)}>重新读取</button><Link className="text-button" to="/projects">返回项目列表 →</Link></div></section>
  const base = `/projects/${encodeURIComponent(projectId)}`
  return (
    <>
      <div className="project-context-bar"><Link className="back-link" to="/projects">← 全部项目</Link><span><strong>{snapshot.project.name}</strong><span className="project-code">{snapshot.project.id}</span></span></div>
      <nav className="project-tabs" aria-label="项目功能">
        <NavLink end to={base}>项目工作台</NavLink><NavLink to={`${base}/chat`}>Agent 对话</NavLink><NavLink to={`${base}/files`}>文件与实验</NavLink><NavLink to={`${base}/comparisons`}>实验比较</NavLink><NavLink to={`${base}/memories`}>项目偏好</NavLink>
      </nav>
      <Outlet context={snapshot.project} />
    </>
  )
}
