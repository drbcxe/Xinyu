import { useEffect, useState } from 'react'
import { Link } from 'react-router'

import { getHealth, getSettingsStatus } from '../api'
import type { HealthStatus, SettingsStatus } from '../types'

interface Snapshot {
  health: HealthStatus | null
  settings: SettingsStatus | null
  errors: string[]
  checkedAt: string
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请稍后重试'
}

export default function OverviewPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshCount, setRefreshCount] = useState(0)

  useEffect(() => {
    const controller = new AbortController()

    async function loadStatus() {
      const [health, settings] = await Promise.allSettled([
        getHealth(controller.signal),
        getSettingsStatus(controller.signal),
      ])
      if (controller.signal.aborted) return

      const errors: string[] = []
      if (health.status === 'rejected') {
        errors.push('后端服务：' + errorMessage(health.reason))
      }
      if (settings.status === 'rejected') {
        errors.push('模型配置：' + errorMessage(settings.reason))
      }

      setSnapshot({
        health: health.status === 'fulfilled' ? health.value : null,
        settings: settings.status === 'fulfilled' ? settings.value : null,
        errors,
        checkedAt: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
      })
      setLoading(false)
    }

    void loadStatus()
    return () => controller.abort()
  }, [refreshCount])

  function refreshStatus() {
    setLoading(true)
    setSnapshot(null)
    setRefreshCount((count) => count + 1)
  }

  const health = snapshot?.health
  const settings = snapshot?.settings
  const serviceOk = health?.status === 'ok'
  const connectionLabel = loading ? '正在检查连接'
    : serviceOk ? '后端已连接' : health ? '服务状态异常' : '后端未连接'

  return (
        <section aria-busy={loading}>
          <div className="page-heading">
            <div>
              <p className="eyebrow">RESEARCH WORKSPACE</p>
              <h1>系统概览</h1>
              <p className="page-description">查看服务连接与模型配置，准备好你的研究工作台。</p>
            </div>
            <button className="refresh-button" type="button" onClick={refreshStatus} disabled={loading}>
              <svg className={loading ? 'spinning' : undefined} viewBox="0 0 24 24" aria-hidden="true">
                <path d="M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.5-1L20 9M4 15l2.4 3A7 7 0 0 0 17.9 17" />
              </svg>
              {loading ? '检查中…' : '刷新状态'}
            </button>
          </div>

          <div className="connection-summary" role="status" aria-live="polite">
            <span className={'status-dot ' + (loading ? 'pending' : serviceOk ? 'success' : 'danger')} />
            <span>{connectionLabel}</span>
            <span className="last-checked">
              {snapshot ? '最近检查 ' + snapshot.checkedAt : '正在读取最新状态…'}
            </span>
          </div>

          {Boolean(snapshot?.errors.length) && (
            <div className="error-panel" role="alert">
              <strong>部分状态暂时无法获取</strong>
              <ul>{snapshot?.errors.map((error) => <li key={error}>{error}</li>)}</ul>
              <p>请确认后端服务正在运行，并检查前端代理配置，然后点击“刷新状态”。</p>
            </div>
          )}

          <div className="status-grid">
            <section className="status-card" aria-labelledby="service-heading">
              <div className="card-heading">
                <div className="card-icon service-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <rect x="4" y="3" width="16" height="7" rx="2" />
                    <rect x="4" y="14" width="16" height="7" rx="2" />
                    <path d="M8 6.5h.01M8 17.5h.01M12 6.5h4M12 17.5h4" />
                  </svg>
                </div>
                <span className={'badge ' + (loading ? 'neutral' : serviceOk ? 'green' : 'red')}>
                  {loading ? '检查中' : serviceOk ? '已连接' : health ? '状态异常' : '未连接'}
                </span>
              </div>
              <h2 id="service-heading">后端服务</h2>
              <p className="card-description">应用接口的运行状态与当前版本。</p>
              <div className="card-value">
                {loading ? '正在检查…' : serviceOk ? '运行正常' : health ? '状态异常' : '等待连接'}
              </div>
              <dl className="detail-list">
                <div><dt>服务状态</dt><dd>{loading ? '—' : health?.status ?? '未获取'}</dd></div>
                <div><dt>项目版本</dt><dd>{loading ? '—' : health ? 'v' + health.version : '未获取'}</dd></div>
              </dl>
              <p className="card-footnote">此项检查仅表示接口服务是否响应。</p>
            </section>

            <section className="status-card" aria-labelledby="model-heading">
              <div className="card-heading">
                <div className="card-icon model-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <rect x="5" y="5" width="14" height="14" rx="4" />
                    <path d="M9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4M9 9h6v6H9z" />
                  </svg>
                </div>
                <span className={'badge ' + (loading ? 'neutral' : !settings ? 'red' : settings.model_configured ? 'green' : 'amber')}>
                  {loading ? '检查中' : !settings ? '未获取' : settings.model_configured ? '已填写' : '待配置'}
                </span>
              </div>
              <h2 id="model-heading">模型配置</h2>
              <p className="card-description">大模型服务的配置完整性检查。</p>
              <div className="card-value">
                {loading ? '正在读取…' : !settings ? '等待连接' : settings.model_configured ? '配置项已齐全' : '待补充配置'}
              </div>
              <dl className="detail-list">
                <div><dt>模型名称</dt><dd>{loading ? '—' : settings ? settings.model_name || '尚未填写' : '未获取'}</dd></div>
                <div><dt>服务主机</dt><dd>{loading ? '—' : settings ? settings.base_url_host || '尚未填写' : '未获取'}</dd></div>
              </dl>
              <p className="card-footnote">仅展示配置状态，不显示 API Key。</p>
            </section>
          </div>

          <section className="next-step" aria-labelledby="next-step-heading">
            <span className="step-number" aria-hidden="true">01</span>
            <div>
              <p className="section-label">接下来的开发步骤</p>
              <h2 id="next-step-heading">先建立你的研究项目</h2>
              <p>保存项目背景，为接下来的实验文件和结果比较做好准备。</p>
              <Link className="text-button" to="/projects">进入项目管理 →</Link>
            </div>
          </section>

        </section>
  )
}
