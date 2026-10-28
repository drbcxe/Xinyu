import type { IconName } from './components/Icon'

export const settingsSections: { key: string; label: string; description: string; icon: IconName }[] = [
  { key: 'preferences', label: '表达偏好', description: '称呼与建议风格', icon: 'settings' },
  { key: 'usage', label: '用量与容量', description: '任务额度与空间占用', icon: 'memory' },
  { key: 'account', label: '账号安全', description: '登录、密码与设备', icon: 'shield' },
  { key: 'connections', label: '通知与连接', description: '提醒、截图与微信', icon: 'bell' },
  { key: 'data', label: '数据管理', description: '导入、导出与清理', icon: 'upload' },
]

export function getSettingsSection(pathname: string) {
  const key = pathname.split('/').filter(Boolean)[2]
  return settingsSections.find(section => section.key === key) ?? settingsSections[0]
}
