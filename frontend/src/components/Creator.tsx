'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import { browserRenderer, uploadRenderFrames } from '@/api/render'
import type {
  Capabilities, CreationSession, DrawingIR, EditorState, Generation, Page, Parameter, Params, RenderJob, SemanticInput, Template, TemplateVersion, Work, WorkDetail, WorkVersion,
} from '@/types/core'
import { sampleTimes } from '@/renderers/canvas/renderScene'
import { parameterValueLabel, shortId, symbolLabel } from '@/utils/format'
import CanvasPreview from './CanvasPreview'
import { GenerationEvidence, QualityEvidence } from './GenerationEvidence'
import IRControls, { parseIR } from './IRControls'
import TimelinePlayer from './TimelinePlayer'
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
  const userId = user?.id
  const [mode, setMode] = useState<EditorState['mode']>('template')
  const [features, setFeatures] = useState<Capabilities['features']>()
  const [semantic, setSemantic] = useState<SemanticInput>({ text: '', entityIds: [] })
  const [semanticIdsText, setSemanticIdsText] = useState('')
  const [templateSeed, setTemplateSeed] = useState(42)
  const [draft, setDraft] = useState('')
  const [nodeId, setNodeId] = useState<string | null>(null)
  const [timelineTime, setTimelineTime] = useState(0)
  const [initialTimelineTime, setInitialTimelineTime] = useState(0)
  const [editWork, setEditWork] = useState<Work | null>(null)
  const [changeNote, setChangeNote] = useState('')
  const [initializing, setInitializing] = useState(true)
  const [restoreError, setRestoreError] = useState('')
  const [restoreAttempt, setRestoreAttempt] = useState(0)
  const [templateRequest, setTemplateRequest] = useState(0)
  const [quality, setQuality] = useState<RenderJob['qualityReport']>()
  const manualLock = useRef(false)
  const lastUser = useRef<string | undefined>(undefined)
  const loadedQuery = useRef('')
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
  const [sessionId, setSessionId] = useState('')
  const [sessionRevision, setSessionRevision] = useState(0)
  const [sessionBusy, setSessionBusy] = useState(false)
  const [sessionNotice, setSessionNotice] = useState('')
  const [sessionError, setSessionError] = useState('')
  const restoredTemplate = useRef<{ versionId: string; params?: Params } | null>(null)

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
    request<Capabilities>('/engine/capabilities', { signal: controller.signal }).then((caps) => {
      if (controller.signal.aborted) return
      setFeatures(caps.features)
      const available = caps.profile === 'core-demo' && caps.backends.some((backend) =>
        backend.backend === 'canvas2d' && backend.available && backend.executionModes.includes('browser_upload'))
      setCapability(available)
      if (!available) setLoadError('后端尚未启用 core-demo Canvas / browser_upload。生成与保存暂不可用。')
    }).catch((cause) => {
      if (!controller.signal.aborted) setLoadError(errorMessage(cause))
    })
    request<Page<Template>>('/templates?backend=canvas2d&pageSize=100', { signal: controller.signal }).then((page) => {
      if (controller.signal.aborted) return
      const items = page.items.filter((item) => item.publishedVersionId && item.supportedBackends.includes('canvas2d'))
      setTemplates(items)
      const requestedTemplate = typeof window !== 'undefined'
        ? new URLSearchParams(window.location.search).get('template')
        : null
      setSelected((old) => items.some((item) => item.id === old)
        ? old
        : requestedTemplate && items.some((item) => item.id === requestedTemplate)
          ? requestedTemplate
          : items[0]?.id ?? '')
    }).catch((cause) => {
      if (!controller.signal.aborted) setLoadError(errorMessage(cause))
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [attempt])

  useEffect(() => {
    if (initializing || mode !== 'template') return
    generationSequence.current++
    setVersion(null)
    if (!restoredTemplate.current) setDirty(true)
    setPending(false)
    setGenerateError('')
    const template = templates.find((item) => item.id === selected)
    setVersionError('')
    const versionId = restoredTemplate.current?.versionId ?? template?.publishedVersionId
    if (!versionId) {
      setVersionLoading(false)
      return
    }
    const controller = new AbortController()
    setVersionLoading(true)
    request<TemplateVersion>(`/template-versions/${encodeURIComponent(versionId)}`, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return
        if (result.status !== 'published' && !restoredTemplate.current) throw new Error('该模板版本尚未发布，请重新加载模板。')
        const restoredParams = restoredTemplate.current?.params
        setParams(restoredParams ?? Object.fromEntries(result.parameterSchema.map((param) => [param.name, param.default])))
        setVersion(result)
      }).catch((cause) => {
        if (!controller.signal.aborted) setVersionError(errorMessage(cause))
      }).finally(() => {
        if (!controller.signal.aborted) setVersionLoading(false)
      })
    return () => controller.abort()
  }, [selected, templates, mode, initializing, templateRequest])

  useEffect(() => {
    const sequence = ++generationSequence.current
    if (mode !== 'template' || initializing || !version || !userId || !capability) {
      setPending(false)
      return
    }
    // Re-authentication with the same user preserves a failed save's checkpoint.
    if (generation?.ownerId === userId && !dirty) {
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
          body: JSON.stringify({ mode: 'template', templateVersionId: version.id, params, backend: 'canvas2d', seed: templateSeed }),
        })
        if (!controller.signal.aborted && generationSequence.current === sequence) {
          acceptGeneration(result)
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
  }, [version, params, userId, capability, mode, initializing, templateSeed])

  useEffect(() => {
    const query = new URLSearchParams(window.location.search)
    const requested = query.get('session')
    const workId = query.get('work')
    const fromVersion = query.get('from')
    const entityId = query.get('entity')
    const draftVersion = query.get('draft')
    if (userId && lastUser.current && lastUser.current !== userId) {
      setGeneration(null)
      setDraft('')
      setSessionId('')
      setSessionRevision(0)
      setEditWork(null)
      setSemantic({ text: '', entityIds: [] })
      setSemanticIdsText('')
      setSaved('')
      checkpoint.current = null
      setJobId('')
      setDirty(true)
      restoredTemplate.current = null
      setTemplateRequest((value) => value + 1)
    }
    if (userId) lastUser.current = userId
    if (entityId && !requested && !fromVersion) {
      setMode('semantic')
      setSemantic((current) => current.entityIds.includes(entityId) ? current : { ...current, entityIds: [entityId] })
      setSemanticIdsText(entityId)
    }
    const restoreKey = JSON.stringify([userId, requested, workId, fromVersion, draftVersion])
    if (loadedQuery.current === restoreKey) return
    if (draftVersion && !requested && !fromVersion && userId) {
      loadedQuery.current = restoreKey
      restoredTemplate.current = { versionId: draftVersion }
      setMode('template')
      setDirty(true)
      setTemplateRequest((value) => value + 1)
    }
    if ((!requested && !fromVersion) || !userId) {
      setInitializing(false)
      return
    }
    setInitializing(true)
    setRestoreError('')
    const controller = new AbortController()
    const { signal } = controller
    async function restore() {
      try {
        if (requested) {
          const session = await request<CreationSession>(`/sessions/${encodeURIComponent(requested)}`, { signal })
          const state = session.state
          const restored = state.generationId
            ? await request<Generation>(`/generations/${encodeURIComponent(state.generationId)}`, { signal })
            : null
          signal.throwIfAborted()
          if (restored) acceptGeneration(restored)
          else setGeneration(null)
          setSessionId(session.id)
          setSessionRevision(session.revision)
          setTitle(session.name)
          setMode(state.mode)
          setSemantic(state.semanticInput)
          setSemanticIdsText(state.semanticInput.entityIds.join(' '))
          setTemplateSeed(state.seed)
          setDraft(state.draftIR ? JSON.stringify(state.draftIR, null, 2) : restored ? JSON.stringify(restored.ir, null, 2) : '')
          setDirty(!restored || Boolean(state.mode === 'ir' && state.draftIR
            && JSON.stringify(state.draftIR) !== JSON.stringify(restored.ir)))
          setNodeId(state.selectedNodeId)
          setTimelineTime(state.timelineTimeMs)
          setInitialTimelineTime(state.timelineTimeMs)
          restoredTemplate.current = state.mode === 'template' && state.templateVersionId
            ? { versionId: state.templateVersionId, params: state.params } : null
          setSessionNotice('已恢复创作会话。')
        } else if (fromVersion && workId) {
          const [work, detail] = await Promise.all([
            request<Work>(`/works/${encodeURIComponent(workId)}`, { signal }),
            request<WorkDetail>(`/work-versions/${encodeURIComponent(fromVersion)}`, { signal }),
          ])
          signal.throwIfAborted()
          if (work.ownerId !== userId || detail.workVersion.workId !== work.id) {
            throw new Error('该版本不属于你的目标作品。')
          }
          acceptGeneration(detail.generation)
          setMode('ir')
          setEditWork(work)
          setTitle(work.title)
        }
        loadedQuery.current = restoreKey
      } catch (cause) {
        if (!signal.aborted) setRestoreError(errorMessage(cause))
      } finally {
        if (!signal.aborted) setInitializing(false)
      }
    }
    void restore()
    return () => controller.abort()
  }, [userId, restoreAttempt])

  function acceptGeneration(result: Generation) {
    setGeneration(result)
    setDraft(JSON.stringify(result.ir, null, 2))
    setNodeId(null)
    setTimelineTime(0)
    setInitialTimelineTime(0)
    setDirty(false)
    setGenerateError('')
    setSaveError('')
    checkpoint.current = null
    setJobId('')
    setSaved('')
    setNotice('')
    setQuality(null)
  }

  async function runManual(path: string, payload: object, subgraph = false) {
    if (!userId || initializing || saveLock.current || manualLock.current) return
    manualLock.current = true
    setPending(true)
    setGenerateError('')
    setDirty(true)
    try {
      if (subgraph) {
        const result = await request<{ generation: Generation; changedNodeIds: string[]; removedNodeIds: string[] }>(path, {
          method: 'POST', body: JSON.stringify(payload),
        })
        acceptGeneration(result.generation)
        setNotice(`子树已应用：变更 ${result.changedNodeIds.length} 个节点，移除 ${result.removedNodeIds.length} 个节点。`)
      } else {
        acceptGeneration(await request<Generation>(path, { method: 'POST', body: JSON.stringify(payload) }))
      }
    } catch (cause) {
      setGenerateError(errorMessage(cause))
    } finally {
      manualLock.current = false
      setPending(false)
    }
  }

  async function saveProgress() {
    if (!user || sessionBusy || initializing || (mode === 'template' && !version)) return
    let draftIR: DrawingIR | null = null
    try {
      if (mode === 'ir' && draft) draftIR = parseIR(draft)
    } catch (cause) {
      setSessionError(errorMessage(cause))
      return
    }
    const state: EditorState = {
      mode,
      templateVersionId: mode === 'template' ? version?.id ?? null : generation?.templateVersionId ?? null,
      params: mode === 'template' ? params : generation?.params ?? {},
      semanticInput: semantic,
      draftIR,
      fragmentSource: '',
      backend: 'canvas2d',
      seed: mode === 'template' ? templateSeed : generation?.seed ?? 42,
      timelineTimeMs: timelineTime,
      selectedNodeId: nodeId,
      generationId: generation?.ownerId === user.id && (!dirty || mode === 'ir') ? generation.id : null,
    }
    const name = title.trim() || '未命名创作现场'
    setSessionBusy(true)
    setSessionError('')
    setSessionNotice('')
    try {
      if (sessionId) {
        const updated = await request<CreationSession>(`/sessions/${encodeURIComponent(sessionId)}`, {
          method: 'PATCH',
          body: JSON.stringify({ expectedRevision: sessionRevision, name, state }),
        })
        setSessionId(updated.id)
        setSessionRevision(updated.revision)
        setSessionNotice('进度已保存，服务器已追加快照。')
      } else {
        const created = await request<CreationSession>('/sessions', {
          method: 'POST',
          body: JSON.stringify({ name, state }),
        })
        setSessionId(created.id)
        setSessionRevision(created.revision)
        setSessionNotice('进度已保存，已创建创作会话。')
      }
    } catch (cause) {
      setSessionError(errorMessage(cause))
    } finally {
      setSessionBusy(false)
    }
  }

  async function save() {
    if (saveLock.current || initializing || manualLock.current || !generation || dirty || pending || !user
      || generation.ownerId !== user.id || !title.trim()) return
    saveLock.current = true
    setSaving(true)
    setSaveError('')
    setSaved('')
    setNotice('')
    const frozen = generation
    const frozenTitle = title.trim()
    const times = sampleTimes(frozen.ir)
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
            timesMs: times,
            executionMode: 'browser_upload',
            ...browserRenderer(),
          }),
        })
        checkpoint.current = { generationId: frozen.id, jobId: job.id }
        setJobId(job.id)
      }
      job = await uploadRenderFrames(job, frozen, times, setNotice)
      setQuality(job.qualityReport)
      setNotice('PNG 上传完成，正在保存私有作品…')
      const payload = { generationId: frozen.id, renderJobId: job.id }
      const path = editWork ? `/works/${encodeURIComponent(editWork.id)}/versions` : '/works'
      const created = await request<{ work: Work; version: WorkVersion }>(path, {
        method: 'POST',
        body: JSON.stringify(editWork
          ? { ...payload, expectedRevision: editWork.revision, changeNote: changeNote.trim() }
          : { ...payload, title: frozenTitle }),
      })
      if (editWork) setEditWork(created.work)
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
    if (id === selected && !restoredTemplate.current) return
    generationSequence.current++
    const wasRestored = Boolean(restoredTemplate.current)
    restoredTemplate.current = null
    if (wasRestored && id === selected) setTemplateRequest((value) => value + 1)
    setTemplateSeed(42)
    setDirty(true)
    setGenerateError('')
    setSaveError('')
    if (id !== selected) setSelected(id)
  }

  function switchMode(next: EditorState['mode']) {
    if (next === mode) return
    generationSequence.current++
    setGenerateError('')
    if (next === 'template') setDirty(true)
    setMode(next)
  }

  function changeSemantic(value: SemanticInput) {
    setSemantic(value)
    setDirty(true)
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
        <p>用模板参数、知识语义或 JSON IR 创作。画布显示服务器校验后的生成结果。</p>
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

    <div className="param-actions p1-modes" role="group" aria-label="创作模式">
      {([
        ['template', '模板参数'], ['semantic', '语义生成'], ['ir', 'JSON IR'],
      ] as const).map(([key, label]) => <button key={key} type="button"
        className={`button button--small button--${mode === key ? 'primary' : 'secondary'}`}
        aria-pressed={mode === key} disabled={initializing || saving || (pending && mode !== 'template')}
        onClick={() => switchMode(key)}>{label}</button>)}
    </div>
    {restoreError && <InlineNotice tone="error" title="无法恢复创作现场">
      {restoreError}
      <button type="button" className="button button--secondary button--small"
        onClick={() => setRestoreAttempt((value) => value + 1)}>重新加载现场</button>
    </InlineNotice>}
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
                  disabled={mode !== 'template' || initializing || saving}
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
            {generation?.ownerId === userId && generation
              ? generation.ir.durationMs > 0
                ? <TimelinePlayer key={generation.id} ir={generation.ir} linkEntities
                  entities={generation.provenance?.matchedEntities} paused={saving}
                  initialTime={initialTimelineTime} onTimeChange={setTimelineTime} />
                : <CanvasPreview ir={generation.ir} timeMs={0} />
              : <EmptyCanvas
                busy={loading || versionLoading}
                label={loading ? '正在载入模板' : versionLoading ? '正在载入参数' : undefined}
              />}
          </div>
          <div className="stage-caption">
            <span>{mode === 'template' ? selectedTemplate?.metadata.name ?? '未选择模板' : mode === 'semantic' ? '语义生成' : 'IR 创作'}</span>
            <span className="mono">{generation ? `${generation.ir.viewport.width} × ${generation.ir.viewport.height}` : '800 × 600'}</span>
          </div>
        </div>
        <p className="stage-note">
          {generation
            ? generation.ir.durationMs > 0
              ? '拖动时间轴定位叙事节点。保存时导出开头、中间、结尾三个 PNG 采样帧。'
              : '当前显示静态画面；保存会导出 1 个采样帧。导出的 PNG 使用当前画布尺寸。'
            : '画布逻辑尺寸为 800 × 600；页面显示时等比缩放，导出尺寸不随窗口变化。'}
        </p>
        {generation && generation.ownerId === userId && <details className="tech-details">
          <summary>本次生成的技术信息</summary>
          <dl className="tech-list">
            <div><dt>生成编号</dt><dd className="mono">{generation.id}</dd></div>
            <div><dt>请求编号</dt><dd className="mono">{generation.requestId}</dd></div>
            <div><dt>IR 哈希</dt><dd className="mono">{shortId(generation.irHash, 16)}</dd></div>
            <div><dt>模板版本</dt><dd className="mono">{generation.templateVersionId ? shortId(generation.templateVersionId, 12) : '—'}</dd></div>
            <div><dt>种子</dt><dd className="mono">{generation.seed}</dd></div>
          </dl>
        </details>}
        {generation && generation.ownerId === userId && <>
          <GenerationEvidence generation={generation} />
          <QualityEvidence generation={generation} report={quality} />
        </>}
      </section>

      <aside className="rail rail--params" aria-labelledby="params-heading">
        <div className="rail-head">
          <h2 id="params-heading">{mode === 'template' ? '调整参数' : mode === 'semantic' ? '输入创作意图' : '编辑 IR'}</h2>
          {mode === 'template' && version && <span className="rail-count mono">{version.parameterSchema.length} 项</span>}
        </div>
        {mode === 'template' && <>
        <p className="rail-lead">{version?.metadata.description || selectedTemplate?.metadata.description || '选择模板后，参数会在这里出现。'}</p>
        {version && (version.status === 'draft' || version.status === 'rejected') && <InlineNotice title="仅私有预览">
          此版本生成的作品无法送审。模板发布后，请用发布版本重新生成。
        </InlineNotice>}
        {versionLoading
          ? <LoadingLines label="正在载入参数" />
          : version
            ? <fieldset disabled={saving || initializing}>
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
        </>}
        {mode === 'semantic' && <form onSubmit={(event) => {
          event.preventDefault()
          if (!user) {
            openLogin('登录后即可按知识或文本生成。')
            return
          }
          void runManual('/generations', {
            mode: 'semantic', input: { text: semantic.text.trim(), entityIds: semantic.entityIds }, backend: 'canvas2d', seed: 42,
          })
        }}>
          <fieldset disabled={saving || pending || initializing || !capability || !features?.semanticGeneration}>
            <legend className="sr-only">语义生成</legend>
            <label className="field">典故或创作描述
              <textarea rows={5} maxLength={2000} value={semantic.text}
                onChange={(event) => changeSemantic({ ...semantic, text: event.target.value })} />
            </label>
            <label className="field">知识实体 ID（以空格分隔）
              <input value={semanticIdsText} onChange={(event) => {
                setSemanticIdsText(event.target.value)
                const ids = event.target.value.trim().split(/\s+/).filter(Boolean)
                changeSemantic({ ...semantic, entityIds: [...new Set(ids)] })
              }} />
            </label>
            <button type="submit" className="button button--primary button--small"
              disabled={!semantic.text.trim() && !semantic.entityIds.length}>{pending ? '正在生成…' : '按语义生成'}</button>
          </fieldset>
          {!features?.semanticGeneration && <p className="rail-lead">当前后端尚未启用语义生成。</p>}
        </form>}
        {mode === 'ir' && (generation && generation.ownerId === userId
          ? <IRControls key={generation.id} generation={generation} draft={draft} selected={nodeId}
            onSelect={setNodeId} disabled={saving || pending || initializing || !capability}
            subgraph={Boolean(features?.subgraphEditing)}
            onDraft={(value) => { setDraft(value); setDirty(true) }}
            onApply={(ir) => void runManual('/generations', {
              mode: 'ir', baseGenerationId: generation.id, ir, backend: 'canvas2d',
            })}
            onSubgraph={(edit) => void runManual(`/generations/${encodeURIComponent(generation.id)}/subgraph`, edit, true)}
          />
          : <p className="rail-lead">先生成一个作品，再以其 IR 为编辑基础。</p>)}
        {generateError && <InlineNotice tone="error" title="修改未应用">{generateError}</InlineNotice>}
      </aside>

      <section className="archive" aria-labelledby="archive-heading">
        <div className="archive-head">
          <h2 id="archive-heading">{editWork ? '保存作品新版本' : '保存作品'}</h2>
          <p>上传完成且服务器确认后，才会显示为已保存。</p>
        </div>
        <div className="archive-body">
          <label className="field">
            <span>作品名称</span>
            <input maxLength={80} required value={title} disabled={saving} readOnly={Boolean(editWork)}
              onChange={(event) => setTitle(event.target.value)} />
          </label>
          {editWork && <label className="field">版本说明
            <input maxLength={500} value={changeNote} disabled={saving} onChange={(event) => setChangeNote(event.target.value)} />
          </label>}
          <div className="archive-actions">
            {!user
              ? <button type="button" className="button button--primary" onClick={() => openLogin('登录后即可创建、上传并保存私有作品。')}>登录后保存</button>
              : <button
                type="button"
                className="button button--primary"
                onClick={() => void save()}
                disabled={initializing || saving || pending || dirty || generation === null || generation.ownerId !== user.id || !title.trim() || Boolean(saved)}
              >{saving ? '保存中…' : jobId ? '重试原任务并保存' : editWork ? '保存为新版本' : '保存私有作品'}</button>}
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
        <div className="archive-session">
          <div className="archive-session__head">
            <h3>保存创作进度</h3>
            <span className="mono">{sessionId ? `会话 ${shortId(sessionId)} · rev ${sessionRevision}` : '未创建会话'}</span>
          </div>
          <p className="archive-note">保存当前创作模式与编辑现场。每次保存由服务器追加快照，恢复入口在个人空间。</p>
          <div className="archive-actions">
            <button type="button" className="button button--secondary" disabled={initializing || saving || sessionBusy || pending || (mode === 'template' && !version) || !user} onClick={() => void saveProgress()}>
              {sessionBusy ? '保存进度中…' : sessionId ? '保存进度并追加快照' : '保存创作进度'}
            </button>
            <Link href="/space?tab=sessions" className="button button--link button--small">打开个人空间会话</Link>
          </div>
          {sessionNotice && <p className="archive-status" role="status">{sessionNotice}</p>}
          {sessionError && <InlineNotice tone="error" title="进度保存失败">{sessionError}</InlineNotice>}
        </div>
      </section>
    </div>
  </section>
}
