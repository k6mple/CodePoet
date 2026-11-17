'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ApiError, errorMessage } from '@/api/client'
import { getPolicies, updatePolicies } from '@/api/sandbox'
import type { DslFunction, Policies, PolicyValues } from '@/types/sandbox'
import { formatDateTime } from '@/utils/format'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'

type NumericPolicyKey =
  | 'dslWallTimeMs'
  | 'dslCpuTimeSeconds'
  | 'dslMemoryMiB'
  | 'dslMaxDrawCalls'
  | 'dslMaxAstNodes'
  | 'dslMaxRangeIterations'

const WRITABLE_NUMERIC: { key: NumericPolicyKey; label: string; min: number; note: string }[] = [
  { key: 'dslWallTimeMs', label: 'DSL 墙钟上限（ms）', min: 100, note: '单次执行的墙钟时间上限，硬上限 5000ms。' },
  { key: 'dslCpuTimeSeconds', label: 'DSL CPU 上限（秒）', min: 1, note: '单次执行的 CPU 时间上限，硬上限 3s。' },
  { key: 'dslMemoryMiB', label: 'DSL 内存上限（MiB）', min: 64, note: 'worker 峰值内存上限，硬上限 512MiB。' },
  { key: 'dslMaxDrawCalls', label: 'DSL 绘图调用上限', min: 1, note: '单次 run 允许的 circle/polygon/polyline 调用数。' },
  { key: 'dslMaxAstNodes', label: 'DSL AST 节点上限', min: 100, note: '源码 AST 节点数上限，硬上限 2000。' },
  { key: 'dslMaxRangeIterations', label: 'DSL 范围迭代上限', min: 1, note: '单次范围迭代上限，硬上限 200。' },
]

const READ_ONLY_NUMERIC: { key: keyof PolicyValues; label: string }[] = [
  { key: 'maxIRNodes', label: 'IR 节点上限' },
  { key: 'maxVertices', label: 'IR 顶点上限' },
  { key: 'maxRequestBytes', label: '请求体上限（字节）' },
  { key: 'maxIRBytes', label: 'IR 字节上限' },
  { key: 'maxParameters', label: '参数数量上限' },
  { key: 'renderWallTimeMs', label: '渲染墙钟上限（ms）' },
  { key: 'turtleMemoryMiB', label: 'Turtle 内存上限（MiB）' },
  { key: 'browserMemoryMiB', label: '浏览器内存上限（MiB）' },
]

const DSL_FUNCTIONS: { key: DslFunction; label: string }[] = [
  { key: 'circle', label: 'circle' },
  { key: 'polygon', label: 'polygon' },
  { key: 'polyline', label: 'polyline' },
  { key: 'set_param', label: 'set_param' },
]

export default function PolicyEditor() {
  const { user, loading, openLogin } = useSession()
  const canRead = Boolean(user?.permissions.includes('policies.read'))
  const canWrite = Boolean(user?.permissions.includes('policies.write'))
  const [policies, setPolicies] = useState<Policies | null>(null)
  const [draft, setDraft] = useState<PolicyValues | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    if (!canRead) return
    let cancelled = false
    setBusy(true)
    setError('')
    getPolicies()
      .then((value) => {
        if (cancelled) return
        setPolicies(value)
        setDraft(structuredClone(value.values))
      })
      .catch((cause) => { if (!cancelled) setError(errorMessage(cause)) })
      .finally(() => { if (!cancelled) setBusy(false) })
    return () => { cancelled = true }
  }, [canRead, reloadKey])

  function setNumber(key: NumericPolicyKey, value: number) {
    setDraft((current) => current ? { ...current, [key]: value } as PolicyValues : current)
  }

  function toggleFunction(key: DslFunction, enabled: boolean) {
    setDraft((current) => {
      if (!current) return current
      const functions = enabled
        ? [...new Set([...current.enabledDslFunctions, key])]
        : current.enabledDslFunctions.filter((item) => item !== key)
      return { ...current, enabledDslFunctions: functions }
    })
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!policies || !draft || !canWrite) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const updated = await updatePolicies(policies.revision, draft)
      setPolicies(updated)
      setDraft(structuredClone(updated.values))
      setNotice(`策略已保存，revision ${updated.revision}。新值只影响之后受理的执行。`)
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setError('策略已被其他管理员修改。请重新加载当前版本后再保存。')
      } else {
        setError(errorMessage(cause))
      }
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <section className="admin-page"><LoadingLines label="正在恢复登录" /></section>
  if (!user) return <section className="admin-page">
    <header className="page-intro page-intro--compact">
      <div><h1 id="policies-heading">执行策略</h1><p>查看和收紧 DSL 沙箱限额与允许函数。</p></div>
    </header>
    <EmptyState
      title="登录后查看执行策略"
      action={<button type="button" className="button button--primary" onClick={() => openLogin('登录管理员账号后即可查看执行策略。')}>登录</button>}
    >
      执行策略仅对具有 `policies.read` 权限的账号开放。
    </EmptyState>
  </section>
  if (!canRead) return <section className="admin-page">
    <header className="page-intro page-intro--compact">
      <div><h1 id="policies-heading">执行策略</h1><p>查看和收紧 DSL 沙箱限额与允许函数。</p></div>
    </header>
    <EmptyState title="无权访问" action={<Link href="/" className="button button--secondary">返回创作台</Link>}>
      当前账号没有 `policies.read` 权限。请使用管理员账号。
    </EmptyState>
  </section>
  if (!policies || !draft) return <section className="admin-page">
    <header className="page-intro page-intro--compact">
      <div><h1 id="policies-heading">执行策略</h1><p>查看和收紧 DSL 沙箱限额与允许函数。</p></div>
    </header>
    {error && <InlineNotice tone="error" title="策略加载失败">{error}</InlineNotice>}
    {busy && <LoadingLines label="正在加载策略" />}
  </section>

  return <section className="admin-page" aria-labelledby="policies-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="policies-heading">执行策略</h1>
        <p>本期只允许修改 DSL 相关限额与允许函数；其余字段只读。硬上限不可突破。</p>
      </div>
      <span className="mono">revision {policies.revision} · {formatDateTime(policies.updatedAt)}</span>
    </header>

    {notice && <InlineNotice tone="success">{notice}</InlineNotice>}
    {error && <InlineNotice tone="error" title="策略操作未完成">{error}
      {error.includes('其他管理员修改') && <div className="form-actions">
        <button type="button" className="button button--secondary button--small" onClick={() => setReloadKey((value) => value + 1)}>重新加载</button>
      </div>}
    </InlineNotice>}

    <form onSubmit={(event) => void save(event)}>
      <section className="editor-panel" aria-labelledby="writable-policy-heading">
        <div className="panel-head">
          <h2 id="writable-policy-heading">可调整的 DSL 限额</h2>
          <span className="mono">PATCH /admin/policies</span>
        </div>
        <div className="editor-fields">
          {WRITABLE_NUMERIC.map((field) => <label className="field" key={field.key}>
            <span>{field.label}</span>
            <input type="number" value={draft[field.key]} min={field.min} max={policies.hardLimits[field.key]}
              onChange={(event) => setNumber(field.key, Number(event.target.value))} disabled={busy || !canWrite} />
            <span className="field-note">{field.note} 当前硬上限 {policies.hardLimits[field.key]}。</span>
          </label>)}
          <fieldset disabled={busy || !canWrite}>
            <legend className="field-label">允许的 DSL 函数</legend>
            <div className="checklist checklist--inline">
              {DSL_FUNCTIONS.map((item) => <label className="checklist-item" key={item.key}>
                <input type="checkbox" checked={draft.enabledDslFunctions.includes(item.key)}
                  onChange={(event) => toggleFunction(item.key, event.target.checked)} />
                {item.label}
              </label>)}
            </div>
            <p className="field-note">只能在本实例硬限允许的函数集合内取舍；关闭后新的 run 会被 AST 审计拒绝。</p>
          </fieldset>
        </div>
        <div className="form-actions">
          <button type="submit" className="button button--primary" disabled={busy || !canWrite}>
            {busy ? '保存中…' : canWrite ? '保存策略' : '只读账号，无法保存'}
          </button>
        </div>
      </section>

      <section className="editor-panel" aria-labelledby="readonly-policy-heading">
        <div className="panel-head">
          <h2 id="readonly-policy-heading">只读限额</h2>
          <span className="mono">不可通过 PATCH 修改</span>
        </div>
        <dl className="tech-list">
          {READ_ONLY_NUMERIC.map((field) => <div key={field.key}>
            <dt>{field.label}</dt>
            <dd className="mono">{String(draft[field.key])}（硬上限 {String(policies.hardLimits[field.key])}）</dd>
          </div>)}
        </dl>
      </section>

      <section className="editor-panel" aria-labelledby="audit-rules-heading">
        <div className="panel-head">
          <h2 id="audit-rules-heading">固定 AST 审计规则</h2>
          <span className="mono">{policies.auditRules.length} 条</span>
        </div>
        <ul className="issue-list">
          {policies.auditRules.map((rule) => <li key={rule.ruleId}>
            <label className="checklist-item">
              <input type="checkbox" checked={rule.enabled} disabled readOnly />
              <span className="mono">{rule.ruleId}</span>：{rule.description}
            </label>
          </li>)}
        </ul>
      </section>
    </form>
  </section>
}
