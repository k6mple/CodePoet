'use client'
import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import type { Capabilities, Generation, Page, Params, RenderJob, Template, TemplateVersion, Work, WorkVersion } from '@/types/core'
import { exportFrame, RENDERER_VERSION, sampleTimes } from '@/renderers/canvas/renderScene'
import CanvasPreview from './CanvasPreview'
import { useSession } from './SessionProvider'

export default function Creator() {
  const { user } = useSession()
  const [templates, setTemplates] = useState<Template[]>([])
  const [selected, setSelected] = useState('')
  const [version, setVersion] = useState<TemplateVersion | null>(null)
  const [params, setParams] = useState<Params>({})
  const [generation, setGeneration] = useState<Generation | null>(null)
  const [capability, setCapability] = useState(false)
  const [error, setError] = useState('')
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
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(''); setCapability(false)
    Promise.all([
      request<Page<Template>>('/templates?backend=canvas2d&pageSize=100', { signal: controller.signal }),
      request<Capabilities>('/engine/capabilities', { signal: controller.signal }),
    ]).then(([page, caps]) => {
      if (controller.signal.aborted) return
      const available = caps.profile === 'core-demo' && caps.backends.some((b) => b.backend === 'canvas2d' && b.available && b.executionModes.includes('browser_upload'))
      setCapability(available)
      if (!available) setError('后端尚未启用 core-demo Canvas/browser_upload。')
      const items = page.items.filter((item) => item.publishedVersionId && item.supportedBackends.includes('canvas2d'))
      setTemplates(items)
      setSelected((old) => items.some((item) => item.id === old) ? old : items[0]?.id ?? '')
    }).catch((cause) => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [attempt])
  useEffect(() => {
    generationSequence.current++
    setVersion(null); setDirty(true); setPending(false)
    const template = templates.find((item) => item.id === selected)
    if (!template?.publishedVersionId) return
    const controller = new AbortController()
    request<TemplateVersion>(`/template-versions/${encodeURIComponent(template.publishedVersionId)}`, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return
        if (result.status !== 'published') throw new Error('该模板版本尚未发布，请重新加载模板')
        setParams(Object.fromEntries(result.parameterSchema.map((p) => [p.name, p.default])))
        setVersion(result)
      }).catch((cause) => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [selected, templates])
  useEffect(() => {
    const sequence = ++generationSequence.current
    if (!version || !user || !capability) { setPending(false); return }
    // Re-authentication with the same user preserves a failed save's checkpoint.
    if (generation?.ownerId === user.id && !dirty) { setPending(false); return }
    const controller = new AbortController()
    setDirty(true); setPending(true); setSaved('')
    const timer = setTimeout(async () => {
      try {
        for (const p of version.parameterSchema) {
          const value = params[p.name]
          if ((p.type === 'number' || p.type === 'integer') && (typeof value !== 'number' || !Number.isFinite(value) || value < p.min || value > p.max || (p.type === 'integer' && !Number.isInteger(value)))) {
            throw new Error(`${p.label}：请输入 ${p.min}～${p.max} ${p.type === 'integer' ? '之间的整数' : '之间的数值'}；修改未应用。`)
          }
        }
        const result = await request<Generation>('/generations', { method: 'POST', signal: controller.signal,
          body: JSON.stringify({ mode: 'template', templateVersionId: version.id, params, backend: 'canvas2d', seed: 42 }) })
        if (!controller.signal.aborted && generationSequence.current === sequence) {
          setGeneration(result); setDirty(false); setError(''); checkpoint.current = null; setJobId(''); setNotice('')
        }
      } catch (cause) { if (!controller.signal.aborted && generationSequence.current === sequence) setError(errorMessage(cause)) }
      finally { if (!controller.signal.aborted && generationSequence.current === sequence) setPending(false) }
    }, 300)
    return () => { clearTimeout(timer); controller.abort() }
  }, [version, params, user, capability])

  async function save() {
    if (saveLock.current || !generation || dirty || pending || !user || generation.ownerId !== user.id || !title.trim()) return
    saveLock.current = true; setSaving(true); setError(''); setSaved('')
    const frozen = generation
    const frozenTitle = title.trim()
    try {
      let job: RenderJob
      if (checkpoint.current?.generationId === frozen.id) {
        job = await request<RenderJob>(`/render-jobs/${encodeURIComponent(checkpoint.current.jobId)}`)
      } else {
        setNotice('创建上传任务…')
        job = await request<RenderJob>('/render-jobs', { method: 'POST', body: JSON.stringify({
          generationId: frozen.id, backend: 'canvas2d', timesMs: sampleTimes(frozen.ir), executionMode: 'browser_upload',
          rendererVersion: RENDERER_VERSION, clientEnvironment: { userAgent: navigator.userAgent, deviceScaleFactor: 1 },
        }) })
        checkpoint.current = { generationId: frozen.id, jobId: job.id }; setJobId(job.id)
      }
      if (job.generationId !== frozen.id) throw new Error('上传任务与 generation 不匹配，请新建任务')
      if (job.status !== 'succeeded') {
        if (job.status !== 'running' || job.phase !== 'awaiting_frames') throw new Error(`任务不可上传：${job.status}。${job.error?.message ?? ''} 请新建任务。`)
        const times = sampleTimes(frozen.ir)
        for (const [index, timeMs] of times.entries()) {
          setNotice(`正在导出并上传 ${index + 1}/${times.length}（${timeMs}ms）…`)
          const file = await exportFrame(frozen.ir, timeMs)
          const body = new FormData()
          body.set('file', file, `frame-${timeMs}.png`); body.set('timeMs', String(timeMs)); body.set('irHash', frozen.irHash)
          job = await request<RenderJob>(`/render-jobs/${encodeURIComponent(job.id)}/frames`, { method: 'POST', body })
        }
      }
      if (job.status !== 'succeeded' || job.manifest?.irHash !== frozen.irHash || !sampleTimes(frozen.ir).every((time) => job.manifest?.frames.some((frame) => frame.timeMs === time))) {
        throw new Error('上传任务尚未成功或帧清单不完整，作品未保存。可重试原任务。')
      }
      setNotice('PNG 上传完成，正在保存私有作品…')
      const created = await request<{ work: Work; version: WorkVersion }>('/works', { method: 'POST',
        body: JSON.stringify({ title: frozenTitle, generationId: frozen.id, renderJobId: job.id }) })
      setSaved(created.version.id); setNotice('作品已由服务器保存。')
    } catch (cause) { setError(errorMessage(cause)); setNotice('保存未确认成功。编辑内容和任务 ID 已保留。若保存请求超时，请先检查“我的作品”，避免重复创建。') }
    finally { saveLock.current = false; setSaving(false) }
  }
  function change(name: string, value: string | number | boolean) {
    generationSequence.current++; setDirty(true); setParams((old) => ({ ...old, [name]: value }))
  }
  return <section aria-labelledby="creator-heading">
    <h2 id="creator-heading">模板创作 · Canvas</h2>
    <p>仅 core-demo / Canvas2D。选择模板后使用默认参数生成；修改参数 300ms 后应用。</p>
    <button disabled={saving || loading} onClick={() => setAttempt((n) => n + 1)}>{loading ? '加载中…' : '重新加载模板'}</button>
    {!loading && templates.length === 0 && <p>没有可用的已发布模板。请由后端提供模板种子与生成接口。</p>}
    {!user && <p>请先登录以生成、调参和保存作品。</p>}
    <fieldset disabled={saving}>
      <label>模板<select value={selected} onChange={(event) => { generationSequence.current++; setDirty(true); setSelected(event.target.value) }}>
        <option value="">选择已发布模板</option>{templates.map((item) => <option key={item.id} value={item.id}>{item.metadata.name}</option>)}
      </select></label>
      {version && <p>{version.metadata.description}</p>}
      {version?.parameterSchema.map((p) => <label key={p.name}>{p.label}
        {p.type === 'boolean' ? <input type="checkbox" checked={params[p.name] === true} onChange={(e) => change(p.name, e.target.checked)} />
          : p.type === 'enum' ? <select value={String(params[p.name])} onChange={(e) => change(p.name, e.target.value)}>{p.options.map((option) => <option key={option}>{option}</option>)}</select>
          : p.type === 'color' ? <input type="color" value={String(params[p.name])} onChange={(e) => change(p.name, e.target.value)} />
          : <><input aria-label={`${p.label}滑条`} type="range" min={p.min} max={p.max} step={p.step} value={typeof params[p.name] === 'number' ? params[p.name] as number : p.min} onChange={(e) => change(p.name, Number(e.target.value))} />
            <input aria-label={`${p.label}数值`} type="number" min={p.min} max={p.max} step={p.step} value={String(params[p.name] ?? '')} onChange={(e) => change(p.name, e.target.value === '' ? '' : Number(e.target.value))} /></>}
        <small>{p.description}</small>
      </label>)}
      <button disabled={!version || !user || pending} onClick={() => { if (version) { generationSequence.current++; setDirty(true); setParams(Object.fromEntries(version.parameterSchema.map((p) => [p.name, p.default]))) } }}>恢复默认并生成</button>
    </fieldset>
    <p role="status">{pending ? '正在应用参数…' : dirty && generation ? '修改未应用，当前仍显示上一份生成结果。' : generation ? '当前画面已应用。' : '等待生成。'}</p>
    {error && <p className="error" role="alert">{error}</p>}
    {generation && <><CanvasPreview ir={generation.ir} timeMs={generation.ir.durationMs} /><p className="note">generationId={generation.id} · requestId={generation.requestId}</p>
      {generation.ir.durationMs > 0 && <p>当前显示结束帧；保存 0 / 中间 / 结束三个采样 PNG。连续播放属于后续任务。</p>}
      <fieldset disabled={saving}><label>作品名称<input maxLength={80} required value={title} onChange={(e) => setTitle(e.target.value)} /></label></fieldset>
      <button disabled={saving || pending || dirty || !user || generation.ownerId !== user.id || !title.trim() || Boolean(saved)} onClick={() => void save()}>{saving ? '保存中…' : checkpoint.current ? '重试原任务并保存' : '保存私有作品'}</button>
      {jobId && !saved && <button disabled={saving} onClick={() => { checkpoint.current = null; setJobId(''); setNotice('下次保存将创建新任务；原任务由后端按时效清理。') }}>下次新建上传任务</button>}
    </>}
    {notice && <p role="status">{notice}</p>}{jobId && <p className="note">renderJobId={jobId}</p>}
    {saved && <Link href={`/works/versions/${encodeURIComponent(saved)}`}>打开已保存的作品与真实 PNG</Link>}
  </section>
}
