import { Link } from 'react-router'

export default function NotFoundPage() {
  return <section className="route-message"><p className="eyebrow">404</p><h1>找不到这个页面</h1><p className="page-description">地址可能已变化，你可以从项目列表重新进入。</p><Link className="text-button" to="/projects">返回项目列表 →</Link></section>
}
