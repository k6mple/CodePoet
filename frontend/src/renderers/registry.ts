import { CanvasRenderer } from './canvas/renderer'
import { RENDERER_VERSION } from './canvas/drawScene'
import { CssRenderer, CSS_RENDERER_VERSION } from './css/renderer'
import { WebglRenderer, WEBGL_RENDERER_VERSION } from './webgl/renderer'
import type { Renderer } from './types'

export function hasBrowserRenderer(backend: string): backend is 'canvas2d' | 'css' | 'webgl' {
  return backend === 'canvas2d' || backend === 'css' || backend === 'webgl'
}

export function rendererVersion(backend: string): string {
  if (backend === 'canvas2d') return RENDERER_VERSION
  if (backend === 'css') return CSS_RENDERER_VERSION
  if (backend === 'webgl') return WEBGL_RENDERER_VERSION
  throw new Error(`尚未实现渲染后端：${backend}`)
}

export const registry = {
  create(backend: string, host: HTMLElement): Renderer {
    if (backend === 'css') {
      if (host instanceof HTMLCanvasElement) throw new Error('CSS Renderer 需要容器元素')
      return new CssRenderer(host)
    }
    if (backend === 'webgl') {
      if (!(host instanceof HTMLCanvasElement)) throw new Error('WebGL Renderer 需要 canvas 元素')
      return new WebglRenderer(host)
    }
    if (backend !== 'canvas2d') throw new Error(`尚未实现渲染后端：${backend}`)
    if (!(host instanceof HTMLCanvasElement)) throw new Error('Canvas Renderer 需要 canvas 元素')
    return new CanvasRenderer(host)
  },
}
