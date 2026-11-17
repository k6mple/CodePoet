'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import { renderRequest, uploadRenderFrames } from '@/api/render'
import { getSandboxRun, submitSandboxRun } from '@/api/sandbox'
import type { Generation, RenderJob, Work, WorkVersion } from '@/types/core'
import type { SandboxRun } from '@/types/sandbox'
import { sampleTimes } from '@/renderers/canvas/renderScene'
import { formatDateTime, shortId } from '@/utils/format'
import CanvasPreview from './CanvasPreview'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'

const TERMINAL = new Set(['succeeded', 'failed', 'timed_out', 'cancelled'])

const SAMPLE_SOURCE = `{
  "version": 1,
  "ops": [
    { "op": "circle", "cx": 400, "cy": 300, "r": 90, "fill": "#E8A020", "stroke": "#6E8B5A", "strokeWidth": 4 },
    { "op": "polygon", "points": [[160, 520], [640, 520], [400, 580]], "fill": "#6E8B5A" }
  ]
}`

export default function SandboxConsole() {
  const { user, loading, openLogin } = useSession()
  const [baseId, setBaseId] = useState('')
  const [source, setSource] = useState(SAMPLE_SOURCE)
  const [run, setRun] = useState<SandboxRun | null>(null)
  const [generation, setGeneration] = useState<Generation | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [title, setTitle] = useState('DSL 沙箱作品')
  const [saving, setSaving] = useState(false)
  const [saveProgress, setSaveProgress] = useState('')
  const [savedVersion, setSavedVersion] = useState('')

  useEffect(() => {
    if (!run || TERMINAL.has(run.status)) return
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const next = await getSandboxRun(run.id)
        if (!cancelled) setRun(next)
      } catch (cause) {
        if (!cancelled) setError(errorMessage(cause))
      }
    }, 800)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [run])

  useEffect(() => {
    if (!run?.generationId) { setGeneration(null); return }
    let cancelled = false
    request<Generation>(`/generations/${encodeURIComponent(run.generationId)}`)
      .then((value) => { if (!cancelled) setGeneration(value) })
      .catch((cause) => { if (!cancelled) setError(errorMessage(cause)) })
    return () => { cancelled = true }
  }, [run?.generationId])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!user) { openLogin('登录后即可提交 DSL 沙箱执行。'); return }
    setBusy(true)
    setError('')
    setNotice('')
    setRun(null)
    setGeneration(null)
    setSavedVersion('')
    try {
      const created = await submitSandboxRun(baseId.trim(), source)
      setRun(created)
      setNotice('已受理。服务端正在隔离 worker 中执行，页面会自动轮询结果。')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  async function saveWork() {
    if (!generation) return
    if (!title.trim()) { setError('请填写作品名称。'); return }
    setSaving(true)
    setError('')
    setNotice('')
    setSavedVersion('')
    const controller = new AbortController()
    try {
      const times = sampleTimes(generation.ir)
      let job = await request<RenderJob>('/render-jobs', {
        method: 'POST',
        body: JSON.stringify(renderRequest(generation, times)),
        signal: controller.signal,
      })
      job = await uploadRenderFrames(job, generation, times, setSaveProgress, controller.signal)
      const created = await request<{ work: Work; version: WorkVersion }>('/works', {
        method: 'POST',
        body: JSON.stringify({ generationId: generation.id, renderJobId: job.id, title: title.trim() }),
        signal: controller.signal,
      })
      setSavedVersion(created.version.id)
      setNotice('fragment 已保存为私有作品。')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setSaving(false)
      setSaveProgress('')
    }
  }

  if (loading) return <section className="admin-page"><LoadingLines label="正在恢复登录" /></section>
  if (!user) return <section className="admin-page">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="sandbox-heading">DSL 沙箱</h1>
        <p>提交受限 JSON AST，在隔离 worker 中追加图元；不执行任意代码。</p>
      </div>
    </header>
    <EmptyState
      title="登录后使用 DSL 沙箱"
      action={<button type="button" className="button button--primary" onClick={() => openLogin('登录后即可提交 DSL 沙箱执行。')}>登录</button>}
    >
      沙箱执行会以你本人拥有的 generation 为基底，结果只归你所有。
    </EmptyState>
  </section>

  return <section className="admin-page" aria-labelledby="sandbox-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="sandbox-heading">DSL 沙箱</h1>
        <p>DSL v1 只解释 circle / polygon / polyline / set_param；源码以 JSON AST 提交，不执行任意代码。</p>
      </div>
    </header>

    {notice && <InlineNotice tone="success">{notice}</InlineNotice>}
    {error && <InlineNotice tone="error" title="沙箱操作未完成">{error}</InlineNotice>}

    <form className="editor-panel" onSubmit={(event) => void submit(event)}>
      <div className="panel-head">
        <h2>提交执行</h2>
        <span className="mono">POST /sandbox-runs</span>
      </div>
      <label className="field">
        <span>基底生成编号</span>
        <input value={baseId} onChange={(event) => setBaseId(event.target.value)} placeholder="gen-…" required
          disabled={busy} autoComplete="off" />
      </label>
      <p className="field-note">必须是当前账号自己拥有的 generation；可从创作台「本次生成的技术信息」复制生成编号。沙箱不会修改基底。</p>
      <label className="field">
        <span>DSL JSON AST</span>
        <textarea className="mono" rows={12} value={source} onChange={(event) => setSource(event.target.value)}
          required disabled={busy} spellCheck={false} />
      </label>
      <p className="field-note">源码上限 16 KiB。所有坐标/半径/颜色/透明度按 IR schema 校验；越界或未知字段整份拒绝。</p>
      <div className="form-actions">
        <button type="submit" className="button button--primary" disabled={busy}>
          {busy ? '提交中…' : '提交执行'}
        </button>
      </div>
    </form>

    {run && <>
      <section className="editor-panel" aria-labelledby="sandbox-run-heading">
        <div className="panel-head">
          <h2 id="sandbox-run-heading">执行结果</h2>
          <span className="mono">{run.status}</span>
        </div>
        <dl className="tech-list">
          <div><dt>执行编号</dt><dd className="mono">{run.id}</dd></div>
          <div><dt>基底生成</dt><dd className="mono">{run.baseGenerationId}</dd></div>
          <div><dt>源码哈希</dt><dd className="mono">{shortId(run.sourceHash, 16)}</dd></div>
          <div><dt>策略修订</dt><dd className="mono">{run.policyRevision}</dd></div>
          <div><dt>创建时间</dt><dd>{formatDateTime(run.createdAt)}</dd></div>
          {run.finishedAt && <div><dt>完成时间</dt><dd>{formatDateTime(run.finishedAt)}</dd></div>}
        </dl>
        {run.policySnapshot && <details className="tech-details">
          <summary>本次生效的策略快照</summary>
          <pre>{JSON.stringify(run.policySnapshot, null, 2)}</pre>
        </details>}
        {run.metrics && <div className="metric-grid">
          <div><span>墙钟时间</span><strong>{run.metrics.wallTimeMs} ms</strong></div>
          <div><span>CPU 时间</span><strong>{run.metrics.cpuTimeMs} ms</strong></div>
          <div><span>绘图调用</span><strong>{run.metrics.drawCalls}</strong></div>
          <div><span>执行步数</span><strong>{run.metrics.steps}</strong></div>
          <div><span>峰值内存</span><strong>{run.metrics.peakMemoryMiB == null ? '—' : `${run.metrics.peakMemoryMiB} MiB`}</strong></div>
        </div>}
        {run.error && <InlineNotice tone="error" title={run.error.code}>{run.error.message}</InlineNotice>}
        {run.auditFindings.length > 0 && <div>
          <h3>审计发现</h3>
          <ul className="issue-list">
            {run.auditFindings.map((finding, index) => <li key={`${finding.ruleId}-${index}`}>
              <span className="mono">{finding.ruleId}</span> · 第 {finding.line} 行第 {finding.column} 列：{finding.message}
            </li>)}
          </ul>
        </div>}
      </section>

      {generation && <section className="editor-panel" aria-labelledby="sandbox-preview-heading">
        <div className="panel-head">
          <h2 id="sandbox-preview-heading">fragment 预览</h2>
          <span className="mono">{generation.backend ?? 'canvas2d'} · {generation.id}</span>
        </div>
        <div className="stage-surface">
          <CanvasPreview ir={generation.ir} timeMs={0} backend={generation.backend ?? 'canvas2d'} />
        </div>
        <div className="form-actions">
          <label className="field">
            <span>作品名称</span>
            <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={80} disabled={saving} required />
          </label>
          <button type="button" className="button button--primary" onClick={() => void saveWork()} disabled={saving || !generation}>
            {saving ? '保存中…' : '保存为私有作品'}
          </button>
        </div>
        {saveProgress && <p role="status">{saveProgress}</p>}
        {savedVersion && <Link href={`/works/versions/${encodeURIComponent(savedVersion)}`} className="button button--secondary button--small">
          打开已保存的作品
        </Link>}
      </section>}
    </>}
  </section>
}
