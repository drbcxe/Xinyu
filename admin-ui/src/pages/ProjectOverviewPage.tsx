import { Link, useOutletContext } from 'react-router'

import NavigationNotice from '../components/NavigationNotice'
import Icon from '../components/Icon'
import type { Project } from '../types'

export default function ProjectOverviewPage() {
  const project = useOutletContext<Project>()
  const base = `/projects/${encodeURIComponent(project.id)}`
  return (
    <>
      <NavigationNotice />
      <div className="page-heading"><div><p className="eyebrow">PROJECT WORKSPACE</p><h1>{project.name}</h1><p className="page-description">项目编号 {project.id} · 在这里继续你的研究。</p></div></div>
      <section className="project-agent-banner"><div><p className="hero-eyebrow"><span />YOUR RESEARCH COPILOT</p><h2>一个问题，<br /><span>开启新的进展。</span></h2><p>让心语读取实验、调用工具，把数据变成有来源的答复。</p><Link className="hero-primary" to={`${base}/chat`}>与心语对话 <Icon name="arrow" /></Link></div><div className="banner-intelligence" aria-hidden="true"><div className="banner-orbit" /><div className="banner-orbit second" /><span><Icon name="spark" /></span><small>THINK. CONNECT. DISCOVER.</small></div></section>
      <div className="workspace-entries">
        <Link className="workspace-entry" to={`${base}/files`}><span className="entry-icon blue"><Icon name="file" /></span><span className="section-label">01 / DATA</span><h2>文件与实验</h2><p>集中管理每份实验数据，保留每条记录的完整来源。</p><span className="card-entry">管理实验文件 <Icon name="arrow" /></span></Link>
        <Link className="workspace-entry" to={`${base}/comparisons`}><span className="entry-icon violet"><Icon name="chart" /></span><span className="section-label">02 / INSIGHTS</span><h2>实验比较</h2><p>对齐评估条件，让每一份改善都清晰可见。</p><span className="card-entry">打开实验比较 <Icon name="arrow" /></span></Link>
        <Link className="workspace-entry" to={`${base}/memories`}><span className="entry-icon silver"><Icon name="memory" /></span><span className="section-label">03 / MEMORY</span><h2>项目偏好</h2><p>记住你的研究习惯，让每一次新对话都能接续。</p><span className="card-entry">管理项目偏好 <Icon name="arrow" /></span></Link>
      </div>
      <section className="project-panel project-background"><div className="panel-title-row"><h2>关于这个项目</h2><span className="project-code">{project.id}</span></div><p className="selected-description">{project.description || '暂无项目说明'}</p><p className="project-time">创建于 {new Date(project.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}</p></section>
    </>
  )
}
