'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import type {
  Capabilities, Generation, Page, Parameter, Params, RenderJob, Template, TemplateVersion, Work, WorkVersion,
} from '@/types/core'
import { exportFrame, RENDERER_VERSION, sampleTimes } from '@/renderers/canvas/renderScene'
import { parameterValueLabel, shortId, symbolLabel } from '@/utils/format'
import CanvasPreview from './CanvasPreview'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'

function EmptyCanvas({ busy, label }: { busy: boolean; label?: string }) {
  return <div className="empty-canvas" role="img" aria-label={busy ? '正在载入画布' : '空画布，尚未生成作品'}>
    <p className="empty-canvas__title">{busy ? (label ?? '正在载入') : '画布尚未生成'}</p>
    <p className="empty-canvas__hint">{busy ? '请稍候。' : '选择模板后，作品会在这里出现。'}</p>
  </div>
}

export default function Creator() {
  const { user, openLogin } = useSession()
  const [templates, setTemplates] = useState<Template[]>([])
  const [selected, setSelected] = useState('')
  const [version, setVersion] = useState<TemplateVersion | null>(null)
  const [versionLoading, setVersionLoading] = useState(false)
  const [params, setParams] = useState<Params>({})
  const [generation, setGeneration] = useState<Generation | null>(null)
  const [capability, setCapability] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [versionError, setVersionError] = useState('')
  const [generateError, setGenerateError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [title, setTitle] = useState('我的 Canvas 作品')
  const [saved, setSaved] = useState('')
  const [attempt, setAttempt] = useState(0)
  const generationSequence = useRef(0)
  const saveLock = useRef(false)
  const checkpoint = useRef<{ generationId: string; jobId: string } | null>(null)
  const [jobId, setJobId] = useState('')

  const selectedTemplate = templates.find((item) => item.id === selected) ?? null
  const status = loading ? '正在载入模板'
    : versionLoading ? '正在载入参数'
      : pending ? '正在应用参数'
        : !capability ? '后端能力未就绪'
          : dirty && generation ? '修改未应用'
            : generation ? '画面已应用' : '等待生成'
  const statusTone = loading || versionLoading || pending ? 'is-pending'
    : !capability ? 'is-off'
      : dirty && generation ? 'is-dirty'
        : generation ? 'is-ready' : 'is-idle'

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setLoadError('')
    setCapability(false)
    Promise.all([
      request<Page<Template>>('/templates?backend=canvas2d&pageSize=100', { signal: controller.signal }),
      request<Capabilities>('/engine/capabilities', { signal: controller.signal }),
    ]).then(([page, caps]) => {
      if (controller.signal.aborted) return
      const available = caps.profile === 'core-demo' && caps.backends.some((backend) =>
        backend.backend === 'canvas2d' && backend.available && backend.executionModes.includes('browser_upload'))
      setCapability(available)
      if (!available) setLoadError('后端尚未启用 core-demo Canvas / browser_upload。生成与保存暂不可用。')
      const items = page.items.filter((item) => item.publishedVersionId && item.supportedBackends.includes('canvas2d'))
      setTemplates(items)
      setSelected((old) => items.some((item) => item.id === old) ? old : items[0]?.id ?? '')
    }).catch((cause) => {
      if (!controller.signal.aborted) setLoadError(errorMessage(cause))
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [attempt])

  useEffect(() => {
    generationSequence.current++
    setVersion(null)
    setDirty(true)
    setPending(false)
    setGenerateError('')
    const template = templates.find((item) => item.id === selected)
    setVersionError('')
    if (!template?.publishedVersionId) {
      setVersionLoading(false)
      return
    }
    const controller = new AbortController()
    setVersionLoading(true)
    request<TemplateVersion>(`/template-versions/${encodeURIComponent(template.publishedVersionId)}`, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return
        if (result.status !== 'published') throw new Error('该模板版本尚未发布，请重新加载模板。')
        setParams(Object.fromEntries(result.parameterSchema.map((param) => [param.name, param.default])))
        setVersion(result)
      }).catch((cause) => {
        if (!controller.signal.aborted) setVersionError(errorMessage(cause))
      }).finally(() => {
        if (!controller.signal.aborted) setVersionLoading(false)
      })
    return () => controller.abort()
  }, [selected, templates])

  useEffect(() => {
    const sequence = ++generationSequence.current
    if (!version || !user || !capability) {
      setPending(false)
      return
    }
    // Re-authentication with the same user preserves a failed save's checkpoint.
    if (generation?.ownerId === user.id && !dirty) {
      setPending(false)
      return
    }
    const controller = new AbortController()
    setDirty(true)
    setPending(true)
    setSaved('')
    const timer = setTimeout(async () => {
      try {
        for (const param of version.parameterSchema) {
          const value = params[param.name]
          if ((param.type === 'number' || param.type === 'integer')
            && (typeof value !== 'number' || !Number.isFinite(value) || value < param.min || value > param.max
              || (param.type === 'integer' && !Number.isInteger(value)))) {
            throw new Error(`${param.label}：请输入 ${param.min}～${param.max} ${param.type === 'integer' ? '之间的整数' : '之间的数值'}；修改未应用。`)
          }
        }
        const result = await request<Generation>('/generations', {
          method: 'POST', signal: controller.signal,
          body: JSON.stringify({ mode: 'template', templateVersionId: version.id, params, backend: 'canvas2d', seed: 42 }),
        })
        if (!controller.signal.aborted && generationSequence.current === sequence) {
          setGeneration(result)
          setDirty(false)
          setGenerateError('')
          setSaveError('')
          checkpoint.current = null
          setJobId('')
          setNotice('')
        }
      } catch (cause) {
        if (!controller.signal.aborted && generationSequence.current === sequence) {
          setGenerateError(errorMessage(cause))
          setDirty(true)
        }
      } finally {
        if (!controller.signal.aborted && generationSequence.current === sequence) setPending(false)
      }
    }, 300)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [version, params, user, capability])

  async function save() {
    if (saveLock.current || !generation || dirty || pending || !user || generation.ownerId !== user.id || !title.trim()) return
    saveLock.current = true
    setSaving(true)
    setSaveError('')
    setSaved('')
    setNotice('')
    const frozen = generation
    const frozenTitle = title.trim()
    try {
      let job: RenderJob
      if (checkpoint.current?.generationId === frozen.id) {
        job = await request<RenderJob>(`/render-jobs/${encodeURIComponent(checkpoint.current.jobId)}`)
      } else {
        setNotice('正在创建上传任务…')
        job = await request<RenderJob>('/render-jobs', {
          method: 'POST',
          body: JSON.stringify({
            generationId: frozen.id,
            backend: 'canvas2d',
            timesMs: sampleTimes(frozen.ir),
            executionMode: 'browser_upload',
            rendererVersion: RENDERER_VERSION,
            clientEnvironment: { userAgent: navigator.userAgent, deviceScaleFactor: 1 },
          }),
        })
        checkpoint.current = { generationId: frozen.id, jobId: job.id }
        setJobId(job.id)
      }
      if (job.generationId !== frozen.id) throw new Error('上传任务与 generation 不匹配，请新建任务。')
      if (job.status !== 'succeeded') {
        if (job.status !== 'running' || job.phase !== 'awaiting_frames') {
          throw new Error(`任务不可上传：${job.status}。${job.error?.message ?? ''} 请新建任务。`)
        }
        const times = sampleTimes(frozen.ir)
        for (const [index, timeMs] of times.entries()) {
          setNotice(`正在导出并上传 ${index + 1}/${times.length}（${timeMs}ms）…`)
          const file = await exportFrame(frozen.ir, timeMs)
          const body = new FormData()
          body.set('file', file, `frame-${timeMs}.png`)
          body.set('timeMs', String(timeMs))
          body.set('irHash', frozen.irHash)
          job = await request<RenderJob>(`/render-jobs/${encodeURIComponent(job.id)}/frames`, { method: 'POST', body })
        }
      }
      if (job.status !== 'succeeded' || job.manifest?.irHash !== frozen.irHash
        || !sampleTimes(frozen.ir).every((time) => job.manifest?.frames.some((frame) => frame.timeMs === time))) {
        throw new Error('上传任务尚未成功或帧清单不完整，作品未保存。可重试原任务。')
      }
      setNotice('PNG 上传完成，正在保存私有作品…')
      const created = await request<{ work: Work; version: WorkVersion }>('/works', {
        method: 'POST',
        body: JSON.stringify({ title: frozenTitle, generationId: frozen.id, renderJobId: job.id }),
      })
      setSaved(created.version.id)
      setNotice('')
    } catch (cause) {
      setSaveError(errorMessage(cause))
      setNotice('')
    } finally {
      saveLock.current = false
      setSaving(false)
    }
  }

  function change(name: string, value: string | number | boolean) {
    generationSequence.current++
    setDirty(true)
    setGenerateError('')
    setSaveError('')
    setParams((old) => ({ ...old, [name]: value }))
  }

  function resetDefaults() {
    if (!version) return
    generationSequence.current++
    setDirty(true)
    setGenerateError('')
    setSaveError('')
    setParams(Object.fromEntries(version.parameterSchema.map((param) => [param.name, param.default])))
  }

  function selectTemplate(id: string) {
    generationSequence.current++
    setDirty(true)
    setGenerateError('')
    setSaveError('')
    setSelected(id)
  }

  function renderParameter(param: Parameter) {
    const id = `param-${param.name}`
    const value = params[param.name]
    const helpId = param.description ? `${id}-help` : undefined
    const rangeId = (param.type === 'number' || param.type === 'integer') ? `${id}-range` : undefined
    const rangeText = (param.type === 'number' || param.type === 'integer')
      ? `${param.min} — ${param.max} · 步长 ${param.step}`
      : ''
    const describedBy = [helpId, rangeId].filter(Boolean).join(' ') || undefined
    return <div className="param" key={param.name}>
      <div className="param-head">
        <label className="param-label" htmlFor={id}>{param.label}</label>
        <output className="param-value" htmlFor={id}>{parameterValueLabel(value)}</output>
      </div>
      {param.type === 'boolean'
        ? <label className="check">
          <input id={id} type="checkbox" checked={value === true} aria-describedby={describedBy} onChange={(event) => change(param.name, event.target.checked)} />
          <span>{value === true ? '开启' : '关闭'}</span>
        </label>
        : param.type === 'enum'
          ? <select id={id} value={String(value ?? param.default)} aria-describedby={describedBy} onChange={(event) => change(param.name, event.target.value)}>
            {param.options.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
          : param.type === 'color'
            ? <div className="color-control">
              <input id={id} type="color" value={String(value ?? param.default)} aria-describedby={describedBy} onChange={(event) => change(param.name, event.target.value)} />
              <span className="mono">{String(value ?? param.default)}</span>
            </div>
            : <div className="number-control">
              <input
                type="range"
                aria-label={`${param.label}滑条`}
                aria-describedby={describedBy}
                min={param.min}
                max={param.max}
                step={param.step}
                value={typeof value === 'number' ? value : param.default}
                onChange={(event) => change(param.name, Number(event.target.value))}
              />
              <input
                id={id}
                type="number"
                min={param.min}
                max={param.max}
                step={param.step}
                value={String(value ?? '')}
                aria-describedby={describedBy}
                onChange={(event) => change(param.name, event.target.value === '' ? '' : Number(event.target.value))}
              />
            </div>}
      {param.description && <p className="param-help" id={helpId}>{param.description}</p>}
      {rangeId && <p className="param-range" id={rangeId}>{rangeText}</p>}
    </div>
  }

  return <section className="creator" aria-labelledby="creator-heading">
    <header className="creator-head">
      <div>
        <h2 id="creator-heading">创作台</h2>
        <p>选择文化主题模板，调整参数。画布只显示服务器返回的真实生成结果。</p>
      </div>
      <div className="capability" aria-live="polite">
        <span className={`status-mark${capability ? ' is-ok' : loading ? '' : ' is-off'}`} aria-hidden="true" />
        <span>{capability ? 'Canvas 2D 可用' : loading ? '正在检查能力' : 'Canvas 2D 未就绪'}</span>
        <span className="mono">core-demo</span>
      </div>
    </header>

    {loadError && <div className="creator-alert">
      <InlineNotice tone="error" title="创作资源加载失败">
        暂时无法载入模板与参数。请查看技术详情；若后端已启动，可能是对应接口尚未实现。
        <details className="notice__details">
          <summary>查看技术详情</summary>
          <p className="notice__detail">{loadError}</p>
        </details>
        <div className="notice__actions">
          <button type="button" className="button button--secondary button--small" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
        </div>
      </InlineNotice>
    </div>}

    <div className="studio">
      <aside className="rail rail--templates" aria-labelledby="templates-heading">
        <div className="rail-head">
          <h2 id="templates-heading">选择模板</h2>
          <span className="rail-count mono">{templates.length} 个</span>
        </div>
        <p className="rail-lead">已发布的文化主题模板。当前仅支持 Canvas 2D。</p>
        {loading
          ? <LoadingLines label="正在载入模板" />
          : templates.length === 0
            ? <EmptyState title="暂无可用模板">后端提供已发布模板后，这里会显示名称、主题与说明。</EmptyState>
            : <ul className="template-list">
              {templates.map((item) => <li key={item.id}>
                <button
                  type="button"
                  className={`template-item${selected === item.id ? ' is-selected' : ''}`}
                  aria-pressed={selected === item.id}
                  onClick={() => selectTemplate(item.id)}
                >
                  <span className="template-meta">{item.metadata.symbols?.map(symbolLabel).join(' · ') || item.metadata.timeTheme || '文化模板'}</span>
                  <span className="template-name">{item.metadata.name}</span>
                  {item.metadata.description && <span className="template-desc" title={item.metadata.description}>{item.metadata.description}</span>}
                </button>
              </li>)}
            </ul>}
        <button type="button" className="button button--secondary button--small rail-action" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>
          {loading ? '加载中…' : '重新加载模板'}
        </button>
      </aside>

      <section className="stage" aria-labelledby="stage-heading">
        <div className="stage-head">
          <h2 id="stage-heading">作品画布</h2>
          <span className={`stage-state ${statusTone}`}>{status}</span>
        </div>
        <div className="stage-mount">
          <div className="stage-surface">
            {generation
              ? <CanvasPreview ir={generation.ir} timeMs={generation.ir.durationMs} />
              : <EmptyCanvas
                busy={loading || versionLoading}
                label={loading ? '正在载入模板' : versionLoading ? '正在载入参数' : undefined}
              />}
          </div>
          <div className="stage-caption">
            <span>{selectedTemplate?.metadata.name ?? '未选择模板'}</span>
            <span className="mono">{generation ? `${generation.ir.viewport.width} × ${generation.ir.viewport.height}` : '800 × 600'}</span>
          </div>
        </div>
        <p className="stage-note">
          {generation
            ? generation.ir.durationMs > 0
              ? '当前显示结束帧；保存会依次导出 0 / 中间 / 结束三个采样帧。连续播放属于后续任务。'
              : '当前显示静态画面；保存会导出 1 个采样帧。导出的 PNG 使用当前画布尺寸。'
            : '画布逻辑尺寸为 800 × 600；页面显示时等比缩放，导出尺寸不随窗口变化。'}
        </p>
        {generation && <details className="tech-details">
          <summary>本次生成的技术信息</summary>
          <dl className="tech-list">
            <div><dt>生成编号</dt><dd className="mono">{generation.id}</dd></div>
            <div><dt>请求编号</dt><dd className="mono">{generation.requestId}</dd></div>
            <div><dt>IR 哈希</dt><dd className="mono">{shortId(generation.irHash, 16)}</dd></div>
            <div><dt>模板版本</dt><dd className="mono">{generation.templateVersionId ? shortId(generation.templateVersionId, 12) : '—'}</dd></div>
            <div><dt>种子</dt><dd className="mono">{generation.seed}</dd></div>
          </dl>
        </details>}
      </section>

      <aside className="rail rail--params" aria-labelledby="params-heading">
        <div className="rail-head">
          <h2 id="params-heading">调整参数</h2>
          {version && <span className="rail-count mono">{version.parameterSchema.length} 项</span>}
        </div>
        <p className="rail-lead">{version?.metadata.description || selectedTemplate?.metadata.description || '选择模板后，参数会在这里出现。'}</p>
        {versionLoading
          ? <LoadingLines label="正在载入参数" />
          : version
            ? <fieldset disabled={saving}>
              <legend className="sr-only">模板参数</legend>
              <div className="param-list">{version.parameterSchema.map(renderParameter)}</div>
              <div className="param-actions">
                <button type="button" className="button button--secondary button--small" onClick={resetDefaults} disabled={pending}>恢复默认</button>
                <span className={`apply-state ${pending ? '' : dirty && generation ? 'is-dirty' : generation ? 'is-ready' : ''}`}>
                  {pending ? '正在应用…' : dirty && generation ? '修改未应用' : generation ? '已应用' : '等待生成'}
                </span>
              </div>
            </fieldset>
            : versionError
              ? <InlineNotice tone="error" title="参数载入失败">{versionError}</InlineNotice>
              : <p className="rail-empty">{loading ? '正在载入模板…' : '尚未选择模板。'}</p>}
        {generateError && <InlineNotice tone="error" title="参数未应用">{generateError}</InlineNotice>}
      </aside>

      <section className="archive" aria-labelledby="archive-heading">
        <div className="archive-head">
          <h2 id="archive-heading">保存作品</h2>
          <p>上传完成且服务器确认后，才会显示为已保存。</p>
        </div>
        <div className="archive-body">
          <label className="field">
            <span>作品名称</span>
            <input maxLength={80} required value={title} disabled={saving} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <div className="archive-actions">
            {!user
              ? <button type="button" className="button button--primary" onClick={() => openLogin('登录后即可创建、上传并保存私有作品。')}>登录后保存</button>
              : <button
                type="button"
                className="button button--primary"
                onClick={() => void save()}
                disabled={saving || pending || dirty || generation === null || generation.ownerId !== user.id || !title.trim() || Boolean(saved)}
              >{saving ? '保存中…' : jobId ? '重试原任务并保存' : '保存私有作品'}</button>}
            {jobId && !saved && <button type="button" className="button button--secondary button--small" disabled={saving} onClick={() => {
              checkpoint.current = null
              setJobId('')
              setNotice('下次保存将创建新任务；原任务由后端按时效清理。')
            }}>下次新建上传任务</button>}
          </div>
        </div>
        <p className="archive-note">
          {!user ? '登录后可保存到你的私有作品。'
            : !generation ? '生成画面后即可保存。'
              : pending || dirty ? '参数尚未应用，保存按钮保持禁用。'
                : '保存时会固定当前生成结果、IR 哈希、渲染器版本和采样时刻。'}
        </p>
        {notice && <p className="archive-status" role="status">{notice}</p>}
        {saved && <p className="archive-success" role="status">
          作品已保存。<Link href={`/works/versions/${encodeURIComponent(saved)}`}>打开已保存的作品</Link>
        </p>}
        {saveError && <InlineNotice tone="error" title="保存未确认">
          {saveError}
          <p>编辑内容和任务编号已保留。若保存请求超时，请先查看“我的作品”，避免重复创建。</p>
        </InlineNotice>}
        {jobId && <details className="tech-details">
          <summary>上传任务编号</summary>
          <p className="mono archive-job">{jobId}</p>
        </details>}
      </section>
    </div>
  </section>
}
