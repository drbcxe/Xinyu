import { useState } from 'react'
import { Link } from 'react-router'
import type { Item, State } from '../relationshipApi'
import { settingsSections } from '../settingsSections'
import Icon from './Icon'
import UsagePanel from './UsagePanel'
import './SettingsPanel.css'

type Props = {
  section: string
  state: State
  profile: State['profile']
  onProfile: (value: State['profile']) => void
  saving: boolean
  busy: boolean
  imports: Item[]
  hasMoreImports: boolean
  loadingImports: boolean
  onLoadMoreImports: () => Promise<void>
  onSaveProfile: () => Promise<void>
  onAccount: () => void
  onNotifications: () => void
  onPrivacy: () => void
  onImport: (file: File) => Promise<void>
  onView: (item: Item) => Promise<void>
  onDelete: (item: Item) => Promise<void>
  onClear: () => Promise<void>
}

export default function SettingsPanel({ section, state, profile, onProfile, saving, busy, imports, hasMoreImports, loadingImports, onLoadMoreImports, onSaveProfile, onAccount, onNotifications, onPrivacy, onImport, onView, onDelete, onClear }: Props) {
  const [confirmClear, setConfirmClear] = useState(false)
  const current = settingsSections.find(item => item.key === section) ?? settingsSections[0]

  return <section className="settings-hub">
    <header className="settings-heading"><div className="love-eyebrow">YOUR SPACE, YOUR WAY</div><h1>偏好与设置</h1><p>按需要调整你的个人空间。</p></header>
    <div className="settings-layout">
      <nav className="settings-navigation" aria-label="设置分类">{settingsSections.map(item => <Link key={item.key} to={`/assistant/settings/${item.key}`} className={current.key === item.key ? 'selected' : ''} aria-current={current.key === item.key ? 'page' : undefined}><Icon name={item.icon} /><span><strong>{item.label}</strong><small>{item.description}</small></span><Icon name="arrow" /></Link>)}</nav>
      <div className="settings-content" aria-label={current.label}>
        <div className="settings-section-heading"><h2>{current.label}</h2><p>{current.key === 'preferences' ? '设置称呼与表达习惯，下一轮建议会参考这些偏好。' : current.description}</p></div>

        {current.key === 'preferences' && <form className="settings-card settings-profile" onSubmit={event => { event.preventDefault(); void onSaveProfile() }}><label>伴侣称呼<input required maxLength={80} value={profile.partner_name} onChange={event => onProfile({ ...profile, partner_name: event.target.value })} /></label><label>你希望怎样表达<textarea required rows={5} maxLength={500} value={profile.style} onChange={event => onProfile({ ...profile, style: event.target.value })} /><small>例如：简短自然、先表达理解、避免过度说教。</small></label><div className="settings-actions"><button className="love-primary" disabled={saving}>{saving ? '正在保存…' : '保存偏好'}</button></div></form>}

        {current.key === 'usage' && <UsagePanel key={state.account.username ?? 'guest'} />}

        {current.key === 'account' && <><div className="settings-card"><div className="settings-account"><span className="settings-account-avatar"><Icon name="user" /></span><div><h3>{state.account.username ?? '访客空间'}</h3><p>{state.account.username ? '在其他设备登录同一账号，即可访问你的空间。' : '注册会保留当前资料，登录后可在其他设备继续使用。'}</p></div></div><div className="settings-actions"><button className="love-primary" disabled={busy || saving} onClick={onAccount}>{state.account.username ? '管理账号与安全' : '注册 / 登录'}</button></div></div><div className="settings-card"><h3>安全管理</h3><ul className="settings-feature-list"><li><Icon name="shield" /><span>登录密码与已登录设备</span></li><li><Icon name="user" /><span>找回邮箱与密码恢复</span></li><li><Icon name="calendar" /><span>近期登录记录与账号注销</span></li></ul><p>{state.account.username ? '通过“管理账号与安全”查看和操作。' : '创建账号后，可以管理这些安全选项。'}</p></div></>}

        {current.key === 'connections' && <><div className="settings-card"><div className="settings-card-heading"><Icon name="bell" /><div><h3>提醒与浏览器通知</h3><p>{state.worker_online ? '提醒服务在线，到期事项会保存在“重要事项”中。' : '提醒服务暂时离线，恢复后会继续处理到期事项。'}</p></div><span className={`settings-status ${state.worker_online ? 'ready' : ''}`}>{state.worker_online ? '在线' : '离线'}</span></div><p>浏览器通知需要打开页面；关闭页面后，仍可在收件箱查看提醒。</p><div className="settings-actions"><button onClick={onNotifications}>开启本浏览器通知</button><Link to="/assistant/reminders">查看重要事项 <Icon name="arrow" /></Link></div></div><div className="settings-card"><div className="settings-card-heading"><Icon name="chat" /><div><h3>聊天截图识别</h3><p>{state.supports_images ? '可以上传截图，先核对文字，再交给助手分析。' : '截图识别暂未配置，你仍可以直接粘贴聊天文字。'}</p></div><span className={`settings-status ${state.supports_images ? 'ready' : ''}`}>{state.supports_images ? '已配置' : '未配置'}</span></div></div><div className="settings-card"><div className="settings-card-heading"><Icon name="send" /><div><h3>微信连接</h3><p>{state.weixin.status === 'connected' ? '当前空间已授权微信连接。' : '微信接入准备中，当前先在网页使用助手与提醒。'}</p></div><span className={`settings-status ${state.weixin.status === 'connected' ? 'ready' : ''}`}>{state.weixin.status === 'connected' ? '已连接' : '未连接'}</span></div><p>不会自动读取你与伴侣的私聊，也不会自动给伴侣发送消息。</p></div></>}

        {current.key === 'data' && <><div className="settings-card"><div className="settings-card-heading"><Icon name="upload" /><div><h3>聊天记录</h3><p>主动导入 UTF-8 TXT / JSON 文件，每个文件最大 1 MB。</p></div></div><div className="settings-actions"><label className={`settings-import ${saving ? 'disabled' : ''}`}><Icon name="upload" />导入聊天文件<input hidden type="file" accept=".txt,.json" disabled={saving} onChange={event => { const file = event.target.files?.[0]; if (file) void onImport(file); event.target.value = '' }} /></label><Link to="/assistant/knowledge">前往资料检索 <Icon name="arrow" /></Link></div>{imports.length ? <div className="settings-imports">{imports.map(file => <div key={file.id}><span title={file.title}>{file.title}</span><button disabled={saving} onClick={() => void onView(file)}>查看原文</button><button disabled={saving} onClick={() => void onDelete(file)}>删除</button></div>)}</div> : <p className="settings-caption">还没有导入的聊天记录。</p>}{hasMoreImports && <button className="settings-load-more" disabled={loadingImports} onClick={() => void onLoadMoreImports()}>{loadingImports ? '正在加载…' : '加载更多聊天记录'}</button>}</div><div className="settings-card"><div className="settings-card-heading"><Icon name="shield" /><div><h3>数据副本与处理授权</h3><p>导出当前空间的数据，或查看 AI 处理范围与保留期限。</p></div></div><div className="settings-actions"><a className="settings-export" href="/api/relationship/export" download>导出我的数据</a><button onClick={onPrivacy}>数据使用与保留</button></div></div><div className="settings-card settings-danger"><h3>清空空间内容</h3><p>删除对话、记忆、事项与导入记录，保留账号和表达偏好。操作前可以先导出副本。</p>{!confirmClear ? <button disabled={saving || busy} onClick={() => setConfirmClear(true)}>清空空间内容</button> : <div className="settings-clear-confirm"><p>本空间内容会永久删除，现有备份与供应商副本不在此操作范围内。</p><div className="settings-actions"><button disabled={saving} onClick={() => { void onClear().then(() => setConfirmClear(false)) }}>{saving ? '正在清理…' : '确认清空'}</button><button disabled={saving} onClick={() => setConfirmClear(false)}>保留数据</button></div></div>}</div></>}
      </div>
    </div>
  </section>
}
