import type { Backend, Generation, RenderJob, WorkDetail } from '../types/core'
import { hasBrowserRenderer, rendererVersion } from './registry'

export const backendLabel = (backend: string) => ({ canvas2d: 'Canvas 2D', css: 'CSS / SVG', webgl: 'WebGL', turtle: 'Turtle' })[backend] ?? backend

// Prefer a backend the current browser can actually draw; a template version
// may declare turtle/webgl before the browser adapter or capability exists.
export function preferredBrowserBackend(backends: readonly string[]): string {
  return backends.find(hasBrowserRenderer) ?? backends[0] ?? 'unavailable'
}

// Missing backend is supported only for legacy Canvas responses.
export const generationBackend = (generation: Generation): Backend => generation.backend ?? 'canvas2d'
export const historicalBackend = (work: WorkDetail): Backend => work.renderJob.backend ?? generationBackend(work.generation)

export function reproductionUnavailable(work: WorkDetail): string | null {
  const backend = historicalBackend(work)
  if (work.generation.backend && work.generation.backend !== backend) return '历史生成记录与渲染任务的后端不一致。'
  if (work.renderJob.executionMode === 'server' || backend === 'turtle') return null
  if (!hasBrowserRenderer(backend)) return `当前浏览器没有 ${backendLabel(backend)} 适配器，仍可读取服务器保存的 PNG。`
  const version = work.renderJob.rendererVersion ?? work.renderJob.manifest?.rendererVersion
  if (version && version !== rendererVersion(backend)) return `历史渲染器版本 ${version} 当前不可执行，仍可读取服务器保存的 PNG。`
  return null
}
