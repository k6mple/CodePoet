import type { Backend, Capabilities, Generation, RenderJob, WorkDetail } from '../types/core'
import { browserSupportsRenderer, rendererVersion } from './registry'

export const backendLabel = (backend: string) => ({ canvas2d: 'Canvas 2D', css: 'CSS / SVG', webgl: 'WebGL', turtle: 'Turtle' })[backend] ?? backend

// Prefer a backend the current browser can actually draw; a template version
// may declare turtle/webgl before the browser adapter or capability exists.
export function preferredBrowserBackend(backends: readonly string[]): string {
  return backends.find(browserSupportsRenderer) ?? backends[0] ?? 'unavailable'
}

// One shared readiness rule for every control that can create or render a
// backend: server availability, local adapter probe, upload mode, and the
// exact renderer version (with the documented legacy Canvas exception).
export function capabilityReason(caps: Capabilities | undefined, backend: Backend): string {
  const item = caps?.backends.find(entry => entry.backend === backend)
  if (!item) return `当前部署没有 ${backendLabel(backend)} 能力声明。`
  if (!item.available) return item.unavailableReason || `当前部署未启用 ${backendLabel(backend)}。`
  if (backend === 'turtle') return item.executionModes.includes('server') ? '' : 'Turtle 需要服务器执行模式。'
  if (!browserSupportsRenderer(backend)) return `当前浏览器不支持 ${backendLabel(backend)}。`
  if (!item.executionModes.includes('browser_upload')) return `${backendLabel(backend)} 不支持浏览器上传。`
  const version = rendererVersion(backend)
  if (!item.rendererVersion) return `${backendLabel(backend)} 未声明渲染器版本。`
  if (item.rendererVersion !== version && !(backend === 'canvas2d' && item.rendererVersion === 'p0-03.1')) {
    return `渲染器版本不匹配：服务端 ${item.rendererVersion}，前端 ${version}。`
  }
  return ''
}

export function backendExecutable(caps: Capabilities | undefined, backend: Backend): boolean {
  return capabilityReason(caps, backend) === ''
}

// Missing backend is supported only for legacy Canvas responses.
export const generationBackend = (generation: Generation): Backend => generation.backend ?? 'canvas2d'
export const historicalBackend = (work: WorkDetail): Backend => work.renderJob.backend ?? generationBackend(work.generation)

export function reproductionUnavailable(work: WorkDetail): string | null {
  const backend = historicalBackend(work)
  if (work.generation.backend && work.generation.backend !== backend) return '历史生成记录与渲染任务的后端不一致。'
  if (work.renderJob.executionMode === 'server' || backend === 'turtle') return null
  if (!browserSupportsRenderer(backend)) return `当前浏览器没有 ${backendLabel(backend)} 适配器，仍可读取服务器保存的 PNG。`
  const version = work.renderJob.rendererVersion ?? work.renderJob.manifest?.rendererVersion
  if (version && version !== rendererVersion(backend)) return `历史渲染器版本 ${version} 当前不可执行，仍可读取服务器保存的 PNG。`
  return null
}
