import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'

import { createProject } from '../api'

export default function CreateProjectPage() {
  const navigate = useNavigate()
  const [id, setId] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)
  const [formError, setFormError] = useState('')
  const idInput = useRef<HTMLInputElement>(null)
  const nameInput = useRef<HTMLInputElement>(null)
  const submission = useRef<AbortController | null>(null)
  useEffect(() => () => submission.current?.abort(), [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submission.current) return
    setFormError('')
    const projectId = id.trim()
    const projectName = name.trim()
    if (!/^[A-Z][A-Z0-9_-]{0,31}$/.test(projectId)) {
      setFormError('编号须以大写字母开头，最多 32 位，可包含大写字母、数字、下划线和连字符。')
      idInput.current?.focus()
      return
    }
    if (!projectName) {
      setFormError('请填写项目名称，不能只包含空格。')
      nameInput.current?.focus()
      return
    }
    const controller = new AbortController()
    submission.current = controller
    setCreating(true)
    try {
      const project = await createProject({ id: projectId, name: projectName, description: description.trim() }, controller.signal)
      if (!controller.signal.aborted) navigate(`/projects/${encodeURIComponent(project.id)}`, { state: { notice: `项目「${project.name}」已创建。` } })
    } catch (error) {
      if (!controller.signal.aborted) setFormError(error instanceof Error ? error.message : '创建失败，请稍后重试。')
    } finally {
      submission.current = null
      if (!controller.signal.aborted) setCreating(false)
    }
  }

  return (
    <>
      <Link className="back-link" to="/projects">← 返回项目列表</Link>
      <div className="page-heading"><div><p className="eyebrow">A NEW BEGINNING</p><h1>新建研究项目</h1><p className="page-description">写下项目目标和背景，开始积累你的实验记录。</p></div></div>
      <div className="standalone-form">
        <section className="project-panel create-panel" aria-labelledby="create-heading">
          <p className="section-label">A NEW BEGINNING</p>
          <h2 id="create-heading">项目资料</h2>
          <p className="form-intro">先写下目标和背景，给后续实验一个清晰的起点。</p>
          <form onSubmit={(event) => { void submit(event) }}>
            <fieldset disabled={creating}>
              <label htmlFor="project-id">项目编号 <span className="required-label">必填</span></label>
              <input
                ref={idInput} id="project-id" name="id" value={id}
                onChange={(event) => setId(event.target.value.toUpperCase())}
                placeholder="例如 P001" required maxLength={32}
                autoComplete="off" spellCheck={false} aria-describedby="project-id-hint"
              />
              <p id="project-id-hint" className="field-hint">大写字母开头，可含数字、下划线和连字符；创建后不可修改。</p>

              <label htmlFor="project-name">项目名称 <span className="required-label">必填</span></label>
              <input
                ref={nameInput} id="project-name" name="name" value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如 合成数据演示项目" required maxLength={100}
              />

              <label htmlFor="project-description">项目说明 <span className="optional-label">选填</span></label>
              <textarea
                id="project-description" name="description" value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="研究目标、数据范围，或你希望解决的问题…"
                rows={5} maxLength={2000}
              />
              <p className="field-hint character-count">{description.length} / 2000</p>
            </fieldset>
            {formError && <div className="error-panel form-error" role="alert">{formError}</div>}
            <button className="primary-button" type="submit" disabled={creating}>
              {creating ? '正在保存…' : '创建项目'}
            </button>
            <p className="field-hint save-note">创建成功后进入项目工作台，项目资料保存在本地数据库中。</p>
          </form>
        </section>
      </div>
    </>
  )
}
