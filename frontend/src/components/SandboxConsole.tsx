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

const SET_PARAM_SAMPLE = `{
  "version": 1,
  "ops": [
    { "op": "set_param", "name": "petalRadius", "value": 42 },
    { "op": "circle", "cx": 400, "cy": 300, "r": 36, "fill": "#E8A020", "stroke": "#6E8B5A", "strokeWidth": 3 }
  ]
}`

const POLYLINE_SAMPLE = `{
  "version": 1,
  "ops": [
    { "op": "polyline", "points": [[120, 460], [280, 390], [440, 430], [680, 340]], "stroke": "#3F5B47", "strokeWidth": 5 },
    { "op": "circle", "cx": 540, "cy": 180, "r": 46, "fill": "#A83E32", "opacity": 0.85 }
  ]
}`

export default function SandboxConsole() {
  const { user, loading, openLogin } = useSession()
  const [baseId, setBaseId] = useState('')
  const [basePrepBusy, setBasePrepBusy] = useState(false)
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

  async function prepareBaseGeneration(kind: 'template' | 'ir') {
    if (!user) { openLogin('登录后即可准备沙箱基底生成记录。'); return }
    if (basePrepBusy || busy) return
    setBasePrepBusy(true)
    setError('')
    setNotice('')
    try {
      const page = await request<{ items: Array<{ id: string; publishedVersionId: string | null }> }>('/templates?backend=canvas2d&pageSize=20')
      const tpl = page.items.find((item) => item.id === 'tpl-lotus' && item.publishedVersionId)
        ?? page.items.find((item) => Boolean(item.publishedVersionId))
      if (!tpl?.publishedVersionId) throw new Error('暂无已发布模板可用于生成基底。')
      const ver = await request<{ id: string; parameterSchema: Array<{ name: string; default: string | number | boolean }> }>(
        `/template-versions/${encodeURIComponent(tpl.publishedVersionId)}`,
      )
      const defaultParams = Object.fromEntries(ver.parameterSchema.map((param) => [param.name, param.default]))
      const tplGen = await request<Generation>('/generations', {
        method: 'POST',
        body: JSON.stringify({ mode: 'template', templateVersionId: ver.id, params: defaultParams, backend: 'canvas2d', seed: 42 }),
      })
      if (kind === 'template') {
        setBaseId(tplGen.id)
        setNotice(`已自动生成清莲模板基底（${shortId(tplGen.id, 12)}），可直接体验 set_param 或叠加图元。`)
      } else {
        const irGen = await request<Generation>('/generations', {
          method: 'POST',
          body: JSON.stringify({ mode: 'ir', baseGenerationId: tplGen.id, backend: 'canvas2d', ir: tplGen.ir }),
        })
        setBaseId(irGen.id)
        setNotice(`已自动生成清莲 IR 基底（${shortId(irGen.id, 12)}），可叠加 circle / polygon / polyline 图元。`)
      }
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBasePrepBusy(false)
    }
  }

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
        <span className="studio-eyebrow">受限 AST · 创意图形工坊</span>
        <h1 id="sandbox-heading">DSL 沙箱</h1>
        <p>基于结构化图形指令（circle / polygon / polyline / set_param），在安全隔离沙箱中衍生新画卷。</p>
      </div>
    </header>
    <EmptyState
      title="登录后使用 DSL 沙箱"
      action={<button type="button" className="button button--primary" onClick={() => openLogin('登录后即可提交 DSL 沙箱执行。')}>登录</button>}
    >
      沙箱执行会以你本人拥有的生成记录为基底，结果只归你所有。
    </EmptyState>
  </section>

  return <section className="admin-page sandbox-page" aria-labelledby="sandbox-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <span className="studio-eyebrow">受限 AST · 创意图形工坊</span>
        <h1 id="sandbox-heading">DSL 沙箱</h1>
        <p>以声明式图形 AST 叠加几何意象或微调模板绑定参数，由隔离进程安全校验并实时渲染画幅。</p>
      </div>
    </header>

    {notice && <InlineNotice tone="success">{notice}</InlineNotice>}
    {error && <InlineNotice tone="error" title="沙箱操作未完成">{error}</InlineNotice>}

    <div className="sandbox-workbench">
      <form className="editor-panel sandbox-workbench__editor" onSubmit={(event) => void submit(event)}>
        <div className="panel-head">
          <h2>编写图形指令</h2>
          <span className="diag-pill">DSL v1 · 安全隔离</span>
        </div>

        <div className="preset-block">
          <span className="preset-block__label">快捷准备基底画卷（免手动复制编号）</span>
          <div className="preset-chips">
            <button type="button" className="preset-chip" disabled={busy || basePrepBusy} onClick={() => void prepareBaseGeneration('template')}>
              {basePrepBusy ? '生成基底中…' : '一键生成：清莲模板基底'}
            </button>
            <button type="button" className="preset-chip" disabled={busy || basePrepBusy} onClick={() => void prepareBaseGeneration('ir')}>
              {basePrepBusy ? '生成基底中…' : '一键生成：清莲 IR 基底'}
            </button>
          </div>
        </div>

        <label className="field">
          <span>基底生成编号</span>
          <input value={baseId} onChange={(event) => setBaseId(event.target.value)} placeholder="点击上方按钮一键生成，或粘贴 gen-…" required
            disabled={busy} autoComplete="off" />
        </label>
        <p className="field-note">沙箱以你本人的生成记录为底稿派生新图层，不会覆写原基底。</p>

        <div className="preset-block">
          <span className="preset-block__label">DSL 预设片段快选</span>
          <div className="preset-chips">
            <button type="button" className="preset-chip" disabled={busy} onClick={() => setSource(SAMPLE_SOURCE)}>预设：金莲圆环与翠竹</button>
            <button type="button" className="preset-chip" disabled={busy} onClick={() => setSource(SET_PARAM_SAMPLE)}>预设：花瓣半径调参 (set_param)</button>
            <button type="button" className="preset-chip" disabled={busy} onClick={() => setSource(POLYLINE_SAMPLE)}>预设：水墨折线韵律</button>
          </div>
        </div>

        <label className="field">
          <span>DSL JSON AST</span>
          <textarea className="mono" rows={11} value={source} onChange={(event) => setSource(event.target.value)}
            required disabled={busy} spellCheck={false} />
        </label>
        <p className="field-note">支持 circle / polygon / polyline / set_param 指令（上限 16 KiB），所有坐标、色彩与透明度均经服务端严格校验。</p>
        <div className="form-actions">
          <button type="submit" className="button button--primary" disabled={busy}>
            {busy ? '提交中…' : '提交执行'}
          </button>
        </div>
      </form>

      <div className="sandbox-workbench__stage">
        {generation ? <section className="editor-panel sandbox-preview-card" aria-labelledby="sandbox-preview-heading">
          <div className="panel-head">
            <h2 id="sandbox-preview-heading">fragment 预览</h2>
            <span className="mono">{generation.backend ?? 'canvas2d'} · {generation.id}</span>
          </div>
          <div className="stage-mount sandbox-stage-mount">
            <div className="stage-surface">
              <CanvasPreview ir={generation.ir} timeMs={0} backend={generation.backend ?? 'canvas2d'} />
            </div>
          </div>
          <div className="form-actions sandbox-save-bar">
            <label className="field">
              <span>作品名称</span>
              <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={80} disabled={saving} required />
            </label>
            <button type="button" className="button button--primary" onClick={() => void saveWork()} disabled={saving || !generation}>
              {saving ? '保存中…' : '保存为私有作品'}
            </button>
          </div>
          {saveProgress && <p role="status" className="archive-status">{saveProgress}</p>}
          {savedVersion && <Link href={`/works/versions/${encodeURIComponent(savedVersion)}`} className="button button--secondary button--small">
            打开已保存的作品
          </Link>}
        </section> : <section className="editor-panel sandbox-empty-stage" aria-label="沙箱画布预览占位">
          <div className="panel-head">
            <h2>画卷预览台</h2>
            <span className="diag-pill">800 × 600</span>
          </div>
          <div className="empty-canvas">
            <p className="empty-canvas__title">{busy || (run && !TERMINAL.has(run.status)) ? '正在沙箱中绘制…' : '等待执行图形指令'}</p>
            <p className="empty-canvas__hint">在左侧一键生成基底并点击「提交执行」，衍生画卷将在此实时呈现并支持典藏保存。</p>
          </div>
        </section>}

        {run && <section className="editor-panel sandbox-telemetry-card" aria-labelledby="sandbox-run-heading">
          <div className="panel-head">
            <h2 id="sandbox-run-heading">执行结果</h2>
            <span className="mono status-chip">{run.status}</span>
          </div>
          {run.metrics && <div className="metric-grid">
            <div><span>墙钟时间</span><strong>{run.metrics.wallTimeMs} ms</strong></div>
            <div><span>CPU 时间</span><strong>{run.metrics.cpuTimeMs} ms</strong></div>
            <div><span>绘图调用</span><strong>{run.metrics.drawCalls}</strong></div>
            <div><span>执行步数</span><strong>{run.metrics.steps}</strong></div>
            <div><span>峰值内存</span><strong>{run.metrics.peakMemoryMiB == null ? '—' : `${run.metrics.peakMemoryMiB} MiB`}</strong></div>
          </div>}
          {run.error && <InlineNotice tone="error" title={run.error.code}>{run.error.message}</InlineNotice>}
          {run.auditFindings.length > 0 && <div className="sandbox-audit-box">
            <h3>审计发现</h3>
            <ul className="issue-list">
              {run.auditFindings.map((finding, index) => <li key={`${finding.ruleId}-${index}`}>
                <span className="mono">{finding.ruleId}</span> · 第 {finding.line} 行第 {finding.column} 列：{finding.message}
              </li>)}
            </ul>
          </div>}
          <details className="tech-details">
            <summary>查看执行编号与策略快照</summary>
            <dl className="tech-list">
              <div><dt>执行编号</dt><dd className="mono">{run.id}</dd></div>
              <div><dt>基底生成</dt><dd className="mono">{run.baseGenerationId}</dd></div>
              <div><dt>源码哈希</dt><dd className="mono">{shortId(run.sourceHash, 16)}</dd></div>
              <div><dt>策略修订</dt><dd className="mono">{run.policyRevision}</dd></div>
              <div><dt>创建时间</dt><dd>{formatDateTime(run.createdAt)}</dd></div>
              {run.finishedAt && <div><dt>完成时间</dt><dd>{formatDateTime(run.finishedAt)}</dd></div>}
            </dl>
            {run.policySnapshot && <pre>{JSON.stringify(run.policySnapshot, null, 2)}</pre>}
          </details>
        </section>}
      </div>
    </div>
  </section>
}
