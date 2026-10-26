import { useEffect } from 'react'
import { Link, NavLink, Outlet, useLocation, useMatch } from 'react-router'

import Icon from './Icon'

export default function AppShell() {
  const { pathname } = useLocation()
  const projectMatch = useMatch('/projects/:projectId/*')
  const projectId = projectMatch?.params.projectId
  const isProject = Boolean(projectId && projectId !== 'new')
  const base = `/projects/${encodeURIComponent(projectId ?? '')}`
  const crumbs: { label: string; to?: string }[] = pathname === '/overview'
    ? [{ label: '系统概览' }]
    : pathname === '/projects' || pathname === '/projects/'
      ? [{ label: '研究项目' }]
      : pathname === '/projects/new'
        ? [{ label: '研究项目', to: '/projects' }, { label: '新建项目' }]
        : isProject
          ? [{ label: '研究项目', to: '/projects' }, { label: projectId ?? '', to: base }]
          : [{ label: '页面未找到' }]
  if (isProject) {
    if (pathname.includes('/chat')) crumbs.push({ label: 'Agent 对话' })
    else if (pathname.includes('/memories')) crumbs.push({ label: '项目偏好' })
    else if (pathname.includes('/comparisons')) crumbs.push({ label: '实验比较' })
    else if (pathname.includes('/files/')) crumbs.push({ label: '文件与实验', to: `${base}/files` }, { label: '实验记录' })
    else if (pathname.endsWith('/files')) crumbs.push({ label: '文件与实验' })
    else crumbs.push({ label: '项目工作台' })
  }
  const pageTitle = crumbs.at(-1)?.label ?? '心语 Xinyu'
  useEffect(() => {
    window.scrollTo(0, 0)
    document.title = `${pageTitle} · 心语 Xinyu`
  }, [pathname, pageTitle])

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside className="sidebar" aria-label="工作台导航">
        <Link className="brand" to="/projects" aria-label="心语 Xinyu 首页">
          <span className="brand-mark" aria-hidden="true"><Icon name="command" /></span>
          <span>心语 Xinyu<small>历史科研模块</small></span>
        </Link>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="全局导航">
          <NavLink className="nav-link" to="/assistant"><Icon name="spark" />心语 · 恋爱助手</NavLink>
          <NavLink className="nav-link" to="/projects">
            <Icon name="grid" />研究项目
          </NavLink>
          <NavLink className="nav-link" to="/overview">
            <Icon name="settings" />系统概览
          </NavLink>
        </nav>
        {isProject && <div className="sidebar-project"><div className="nav-label">CURRENT PROJECT <span>{projectId}</span></div><nav aria-label="当前项目快捷导航">
          <NavLink className="nav-link" to={`${base}/chat`}><Icon name="spark" />Agent 对话</NavLink>
          <NavLink className="nav-link" to={`${base}/files`}><Icon name="file" />文件与实验</NavLink>
          <NavLink className="nav-link" to={`${base}/comparisons`}><Icon name="chart" />实验比较</NavLink>
          <NavLink className="nav-link" to={`${base}/memories`}><Icon name="memory" />项目偏好</NavLink>
        </nav></div>}
        <div className="sidebar-bottom"><Link className="sidebar-create" to="/projects/new"><span className="sidebar-create-icon"><Icon name="plus" /></span><strong>下一个发现，从这里开始。</strong><span>创建研究项目 <Icon name="arrow" /></span></Link>
          <div className="sidebar-identity"><span className="user-avatar">X</span><div><strong>个人工作空间</strong><small>心语 Xinyu · 本地开发</small></div><Icon name="shield" /></div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <nav aria-label="当前位置"><ol className="breadcrumbs">{crumbs.map((crumb, index) => (
            <li key={`${crumb.label}-${index}`}>
              {index > 0 && <span className="breadcrumb-divider" aria-hidden="true">/</span>}
              {crumb.to ? <Link to={crumb.to}>{crumb.label}</Link> : <span aria-current={index === crumbs.length - 1 ? 'page' : undefined}>{crumb.label}</span>}
            </li>
          ))}</ol></nav>
          <div className="topbar-actions"><span className="environment-tag"><span />个人工作空间</span><Link className="topbar-create" to="/projects/new" aria-label="新建研究项目"><Icon name="plus" /></Link><span className="topbar-avatar" aria-hidden="true">X</span></div>
        </header>
        <main id="main-content" className={`main-content${pathname.endsWith('/chat') ? ' chat-main' : ''}`} tabIndex={-1}>
          <Outlet />
          <footer className="page-footer"><span>心语 Xinyu <span className="footer-divider">/</span> 为每一次探索而造。</span><span>YOUR NEXT DISCOVERY STARTS HERE.</span></footer>
        </main>
      </div>
    </div>
  )
}
