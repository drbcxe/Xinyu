import { useEffect, useState } from 'react'
import { Link } from 'react-router'

import { getProjects } from '../api'
import Icon from '../components/Icon'
import ResearchVisual from '../components/ResearchVisual'
import type { Project } from '../types'

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('recent')
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const items = await getProjects(controller.signal)
        if (!controller.signal.aborted) setProjects(items)
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取项目列表')
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [revision])
  function refresh() { setLoading(true); setError(''); setRevision((current) => current + 1) }
  const search = query.trim().toLocaleLowerCase()
  const visibleProjects = projects.filter((project) => `${project.id} ${project.name} ${project.description}`.toLocaleLowerCase().includes(search)).sort((a, b) => sort === 'name' ? a.name.localeCompare(b.name, 'zh-CN') : Date.parse(b.created_at) - Date.parse(a.created_at))
  return (
    <div className="projects-home">
      <section className="home-hero" aria-labelledby="workspace-heading">
        <div className="hero-copy"><p className="hero-eyebrow"><span />XINYU · RESEARCH ARCHIVE</p><h1 id="workspace-heading">专注探索。<br /><span>让发现，发生。</span></h1><p className="hero-description">从实验数据到下一步洞见。<br />让 AI 连接你的项目、思考与每一次进展。</p><div className="hero-actions"><Link className="hero-primary" to="/projects/new">开启研究项目 <Icon name="arrow" /></Link><a className="hero-secondary" href="#project-library">探索工作空间 <Icon name="chevron" /></a></div><div className="hero-footnote"><Icon name="command" /><span>你的研究，由你掌控。</span></div></div>
        <ResearchVisual />
      </section>
      <div className="capability-strip" aria-label="工作空间能力"><div><span className="capability-icon blue"><Icon name="spark" /></span><span><strong>与你一起思考</strong><small>自然语言驱动，工具协同执行</small></span></div><div><span className="capability-icon violet"><Icon name="chart" /></span><span><strong>让结论有据可循</strong><small>真实数据比较，完整来源追溯</small></span></div><div><span className="capability-icon silver"><Icon name="memory" /></span><span><strong>每次对话，都能接续</strong><small>项目偏好与会话持久保存</small></span></div></div>
      <section id="project-library" className="project-library" aria-labelledby="library-heading">
      <div className="library-heading"><div><p className="eyebrow">YOUR WORKSPACE</p><h2 id="library-heading">我的研究项目 <span className="project-count">{loading ? '—' : error ? '!' : projects.length}</span></h2><p className="page-description">把一个想法，推进成下一次发现。</p></div><div className="page-actions"><button className="icon-button" type="button" disabled={loading} onClick={refresh} aria-label="刷新项目列表"><Icon name="refresh" className={loading ? 'spinning' : ''} /></button><Link className="primary-link" to="/projects/new"><Icon name="plus" />新建项目</Link></div></div>
      <div className="library-toolbar"><label className="project-search"><Icon name="search" /><span className="visually-hidden">搜索项目</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索项目名称、编号或关键词…" /></label><label className="project-sort"><span className="visually-hidden">项目排序</span><select value={sort} onChange={(event) => setSort(event.target.value)}><option value="recent">最近创建</option><option value="name">按名称排序</option></select></label></div>
      <p className="project-feedback" role="status">{loading ? '正在同步你的工作空间…' : error ? '项目列表暂不可用' : search ? `找到 ${visibleProjects.length} 个匹配项目` : `${projects.length} 个项目 · 选择一个，继续探索`}</p>
      {error && <div className="error-panel" role="alert">{error}{projects.length > 0 && <p>下方保留上次读取的项目列表。</p>}</div>}
      {!loading && !error && projects.length === 0 && <section className="project-panel empty-projects"><span className="empty-project-mark"><Icon name="folder" /></span><h3>你的下一次发现，从这里开始</h3><p>创建第一个研究项目，将想法、实验和 Agent 放进同一个工作空间。</p><Link className="primary-link" to="/projects/new"><Icon name="plus" />创建第一个项目</Link></section>}
      {!loading && projects.length > 0 && visibleProjects.length === 0 && <section className="project-panel empty-projects"><span className="empty-project-mark"><Icon name="search" /></span><h3>没有找到相关项目</h3><p>换一个关键词，或按项目编号查找。</p><button className="text-button" type="button" onClick={() => setQuery('')}>清除搜索 →</button></section>}
      {loading && projects.length === 0 && <div className="project-skeletons" aria-hidden="true"><div /><div /></div>}
      <ul className="projects-index-grid" aria-busy={loading}>{visibleProjects.map((project, index) => (
        <li key={project.id}><Link className={`project-item project-index-card project-tone-${index % 3}`} to={`/projects/${encodeURIComponent(project.id)}`}>
          <span className="project-card-top"><span className="project-folder-icon"><Icon name="folder" /></span><span className="project-code">{project.id}</span><Icon className="project-card-arrow" name="arrow" /></span><strong>{project.name}</strong>
          <span className="project-item-description">{project.description || '暂无项目说明'}</span>
          <span className="project-card-bottom"><span className="project-time">{new Date(project.created_at).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'short', day: 'numeric' })}</span><span className="card-entry">打开工作空间 <Icon name="arrow" /></span></span>
        </Link></li>
      ))}</ul>
      </section>
    </div>
  )
}
