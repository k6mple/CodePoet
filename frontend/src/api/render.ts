import { request } from '@/api/client'
import { exportFrame, RENDERER_VERSION } from '@/renderers/canvas/renderScene'
import type { Generation, RenderJob } from '@/types/core'

export function browserRenderer() {
  return {
    rendererVersion: RENDERER_VERSION,
    clientEnvironment: { userAgent: navigator.userAgent, deviceScaleFactor: 1 },
  }
}

export async function uploadRenderFrames(
  initialJob: RenderJob,
  generation: Generation,
  times: number[],
  onProgress: (message: string) => void,
  signal?: AbortSignal,
) {
  let job = initialJob
  if (job.generationId !== generation.id || times.length === 0
    || job.timesMs.length !== times.length || !times.every((time, index) => job.timesMs[index] === time)) {
    throw new Error('上传任务与当前生成记录或采样时刻不匹配。')
  }
  if (job.status !== 'succeeded') {
    if (job.status !== 'running' || job.phase !== 'awaiting_frames') {
      throw new Error(`任务不可上传：${job.status}。${job.error?.message ?? ''}`)
    }
    for (const [index, timeMs] of times.entries()) {
      signal?.throwIfAborted()
      onProgress(`正在导出并上传 ${index + 1}/${times.length}（${timeMs}ms）…`)
      const file = await exportFrame(generation.ir, timeMs)
      signal?.throwIfAborted()
      const body = new FormData()
      body.set('file', file, `frame-${timeMs}.png`)
      body.set('timeMs', String(timeMs))
      body.set('irHash', generation.irHash)
      job = await request<RenderJob>(`/render-jobs/${encodeURIComponent(initialJob.id)}/frames`, {
        method: 'POST', body, signal,
      })
    }
  }
  if (job.status !== 'succeeded' || job.manifest?.irHash !== generation.irHash
    || !times.every((time) => job.manifest?.frames.some((frame) => frame.timeMs === time))) {
    throw new Error('上传任务尚未成功或帧清单不完整。可重试原任务。')
  }
  return job
}
