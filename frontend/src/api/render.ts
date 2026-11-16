import { request } from '@/api/client'
import { exportBackendFrame } from '@/renderers/export'
import { generationBackend } from '@/renderers/backend'
import { rendererVersion } from '@/renderers/registry'
import type { Generation, RenderJob } from '@/types/core'

export function browserRenderer(backend = 'canvas2d') {
  return {
    rendererVersion: rendererVersion(backend),
    clientEnvironment: { userAgent: navigator.userAgent, deviceScaleFactor: 1 },
  }
}

export function renderRequest(generation: Generation, times: number[]) {
  const backend = generationBackend(generation)
  return backend === 'turtle'
    ? { generationId: generation.id, backend, timesMs: times, executionMode: 'server' }
    : { generationId: generation.id, backend, timesMs: times, executionMode: 'browser_upload', ...browserRenderer(backend) }
}

function delay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  signal?.throwIfAborted()
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal?.reason ?? new DOMException('已取消', 'AbortError')) }
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve() }, milliseconds)
    signal?.addEventListener('abort', abort, { once: true })
  })
}

export async function pollRenderJob(initial: RenderJob, onProgress: (message: string) => void, signal?: AbortSignal): Promise<RenderJob> {
  let job = initial
  while (job.status === 'queued' || job.status === 'running') {
    signal?.throwIfAborted()
    onProgress(`服务器正在处理：${job.phase}…`)
    await delay(500, signal)
    const next = await request<RenderJob>(`/render-jobs/${encodeURIComponent(job.id)}`, { signal })
    if (next.id !== initial.id || next.generationId !== initial.generationId || next.backend !== initial.backend
      || next.executionMode !== initial.executionMode) throw new Error('服务器任务查询结果与原任务不一致。')
    job = next
  }
  if (job.status !== 'succeeded') throw new Error(`服务器任务未完成：${job.status}。${job.error?.message ?? ''}`)
  return job
}

export const cancelRenderJob = (jobId: string) => request<RenderJob>(`/render-jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })

export async function uploadRenderFrames(
  initialJob: RenderJob,
  generation: Generation,
  times: number[],
  onProgress: (message: string) => void,
  signal?: AbortSignal,
) {
  let job = initialJob
  const backend = generationBackend(generation)
  const jobBackend = job.backend ?? backend
  if (jobBackend !== backend || job.generationId !== generation.id || times.length === 0
    || job.timesMs.length !== times.length || !times.every((time, index) => job.timesMs[index] === time)) {
    throw new Error('上传任务与当前生成记录或采样时刻不匹配。')
  }
  if (job.executionMode === 'server' || backend === 'turtle') {
    if (job.executionMode !== 'server' || backend !== 'turtle') throw new Error('服务器任务必须使用服务器后端与执行模式。')
    job = await pollRenderJob(job, onProgress, signal)
  } else {
    const version = rendererVersion(backend)
    if (job.rendererVersion && job.rendererVersion !== version) throw new Error('上传任务的渲染器版本与当前适配器不一致。')
    if (job.status !== 'succeeded') {
      if (job.status !== 'running' || job.phase !== 'awaiting_frames') {
        throw new Error(`任务不可上传：${job.status}。${job.error?.message ?? ''}`)
      }
      for (const [index, timeMs] of times.entries()) {
        signal?.throwIfAborted()
        onProgress(`正在导出并上传 ${index + 1}/${times.length}（${timeMs}ms）…`)
        const file = await exportBackendFrame(backend, generation.ir, timeMs)
        signal?.throwIfAborted()
        const body = new FormData()
        body.set('file', file, `frame-${timeMs}.png`)
        body.set('timeMs', String(timeMs))
        body.set('irHash', generation.irHash)
        job = await request<RenderJob>(`/render-jobs/${encodeURIComponent(initialJob.id)}/frames`, {
          method: 'POST', body, signal,
        })
        if (job.id !== initialJob.id || job.generationId !== generation.id || (job.backend && job.backend !== backend)
          || (job.rendererVersion && job.rendererVersion !== version)) throw new Error('帧上传响应与当前任务、后端或版本不一致。')
      }
    }
  }
  if (job.status !== 'succeeded' || job.manifest?.irHash !== generation.irHash
    || (job.manifest.backend && job.manifest.backend !== backend)
    || (job.manifest.rendererVersion && job.manifest.rendererVersion !== job.rendererVersion)
    || !times.every((time) => job.manifest?.frames.some((frame) => frame.timeMs === time))) {
    throw new Error('上传任务尚未成功或帧清单不完整。可重试原任务。')
  }
  return job
}
