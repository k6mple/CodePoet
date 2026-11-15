import type { DrawingIR } from '../../types/core'
import type { Renderer } from '../types'
import { renderScene, RENDERER_VERSION } from './drawScene'

export class CanvasRenderer implements Renderer {
  readonly backend = 'canvas2d' as const
  readonly rendererVersion = RENDERER_VERSION
  private canvas: HTMLCanvasElement | null
  private ir: DrawingIR | null = null
  private pending: Promise<unknown> = Promise.resolve()

  constructor(canvas: HTMLCanvasElement) { this.canvas = canvas }

  private enqueue<T>(operation: (canvas: HTMLCanvasElement) => T | Promise<T>): Promise<T> {
    const next = this.pending.then(() => {
      if (!this.canvas) throw new Error('Renderer 已释放')
      return operation(this.canvas)
    })
    // One failed export must not poison later seeks or loads.
    this.pending = next.catch(() => undefined)
    return next
  }

  load(ir: DrawingIR): Promise<void> {
    return this.enqueue((canvas) => {
      this.ir = null
      canvas.width = ir.viewport.width
      canvas.height = ir.viewport.height
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('浏览器不支持 Canvas2D')
      renderScene(ctx, ir, 0)
      this.ir = ir
    })
  }

  seek(timeMs: number): Promise<void> {
    return this.enqueue((canvas) => {
      if (!this.ir) throw new Error('请先加载 IR')
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('浏览器不支持 Canvas2D')
      renderScene(ctx, this.ir, timeMs)
    })
  }

  toPNG(): Promise<Blob> {
    return this.enqueue((canvas) => {
      if (!this.ir) throw new Error('请先加载 IR')
      return new Promise<Blob>((resolve, reject) => canvas.toBlob((blob) => {
        if (!this.canvas) reject(new Error('Renderer 已释放'))
        else if (!blob) reject(new Error('PNG 导出失败，可保留现场后重试'))
        else if (blob.size > 5 * 1024 * 1024) reject(new Error('PNG 超过 5MiB'))
        else resolve(blob)
      }, 'image/png'))
    })
  }

  dispose(): void {
    this.ir = null
    if (this.canvas) {
      this.canvas.width = 0
      this.canvas.height = 0
      this.canvas = null
    }
  }
}
