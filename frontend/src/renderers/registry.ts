import { CanvasRenderer } from './canvas/renderer'
import type { Renderer } from './types'

export const registry = {
  create(backend: string, host: HTMLElement): Renderer {
    if (backend !== 'canvas2d') throw new Error(`尚未实现渲染后端：${backend}`)
    if (!(host instanceof HTMLCanvasElement)) throw new Error('Canvas Renderer 需要 canvas 元素')
    return new CanvasRenderer(host)
  },
}
