import type { DrawingIR } from '../../types/core'
import { sampleScene, type SampledScene } from '../scene'
import type { Renderer } from '../types'

export const CSS_RENDERER_VERSION = 'codepoet-css-1.0.0'
const SVG_NS = 'http://www.w3.org/2000/svg'

export class CssRenderer implements Renderer {
  readonly backend = 'css' as const
  readonly rendererVersion = CSS_RENDERER_VERSION
  private host: HTMLElement | null
  private svg: SVGSVGElement | null = null
  private ir: DrawingIR | null = null
  private scene: SampledScene | null = null
  private paths: SVGPathElement[] = []
  private animations: Animation[] = []
  private pending: Promise<unknown> = Promise.resolve()
  private opacityTimes: number[] = []

  constructor(host: HTMLElement) { this.host = host }

  private enqueue<T>(operation: () => T | Promise<T>): Promise<T> {
    const next = this.pending.then(() => {
      if (!this.host) throw new Error('Renderer 已释放')
      return operation()
    })
    this.pending = next.catch(() => undefined)
    return next
  }

  load(ir: DrawingIR): Promise<void> {
    return this.enqueue(() => {
      const first = sampleScene(ir, 0)
      this.clear()
      this.ir = ir
      const svg = document.createElementNS(SVG_NS, 'svg')
      svg.setAttribute('width', String(ir.viewport.width))
      svg.setAttribute('height', String(ir.viewport.height))
      svg.setAttribute('viewBox', `0 0 ${ir.viewport.width} ${ir.viewport.height}`)
      svg.setAttribute('role', 'img')
      svg.setAttribute('aria-label', 'CSS 作品预览')
      svg.style.cssText = 'display:block;max-width:100%;height:auto;overflow:hidden'
      if (ir.canvas.background !== null) {
        const background = document.createElementNS(SVG_NS, 'rect')
        background.setAttribute('width', String(ir.viewport.width))
        background.setAttribute('height', String(ir.viewport.height))
        background.setAttribute('fill', ir.canvas.background)
        svg.append(background)
      }
      this.opacityTimes = [...new Set([0, ir.durationMs, ...ir.animations
        .filter(track => track.property === 'opacity')
        .flatMap(track => [track.startMs, track.endMs])])].sort((a, b) => a - b)
      for (const node of first.nodes) {
        const path = document.createElementNS(SVG_NS, 'path')
        path.dataset.nodeId = node.id
        path.setAttribute('stroke-linecap', 'butt')
        path.setAttribute('stroke-linejoin', 'miter')
        path.setAttribute('stroke-miterlimit', '10')
        svg.append(path)
        this.paths.push(path)
        // Paused WAAPI effects use the page's clock; no autonomous animation loop.
        const animation = path.animate([{ opacity: node.opacity }, { opacity: node.opacity }], {
          duration: Math.max(ir.durationMs, 1), fill: 'both', easing: 'linear',
        })
        animation.pause()
        this.animations.push(animation)
      }
      this.svg = svg
      this.host!.append(svg)
      this.draw(0)
    })
  }

  private draw(timeMs: number): void {
    if (!this.ir || !this.svg) throw new Error('请先加载 IR')
    const scene = sampleScene(this.ir, timeMs)
    const duration = Math.max(this.ir.durationMs, 1)
    // Include the exact seek point: ancestor opacity products need not be linear.
    const times = [...new Set([...this.opacityTimes, scene.timeMs])].sort((a, b) => a - b)
    const samples = times.map(time => sampleScene(this.ir!, time))
    scene.nodes.forEach((node, index) => {
      const path = this.paths[index]
      const d = node.points.map(([x, y], point) => `${point ? 'L' : 'M'}${x - scene.viewport.x} ${y - scene.viewport.y}`).join(' ')
        + (node.type === 'polyline' ? '' : ' Z')
      path.setAttribute('d', d)
      path.setAttribute('fill', node.style.fill ?? 'none')
      path.setAttribute('stroke', (node.style.strokeWidth ?? 1) > 0 ? node.style.stroke ?? 'none' : 'none')
      path.setAttribute('stroke-width', String(node.style.strokeWidth ?? 1))
      ;(this.animations[index].effect as KeyframeEffect).setKeyframes(samples.map((sample, step) => ({
        offset: times[step] / duration, opacity: sample.nodes[index].opacity,
      })))
      this.animations[index].currentTime = scene.timeMs
    })
    this.scene = scene
    this.svg.dataset.timeMs = String(scene.timeMs)
  }

  seek(timeMs: number): Promise<void> { return this.enqueue(() => this.draw(timeMs)) }

  toPNG(): Promise<Blob> {
    return this.enqueue(async () => {
      if (!this.svg || !this.scene) throw new Error('请先加载 IR')
      const clone = this.svg.cloneNode(true) as SVGSVGElement
      clone.style.cssText = 'display:block;overflow:hidden'
      clone.removeAttribute('role')
      clone.removeAttribute('aria-label')
      // WAAPI effects are not cloned. Freeze the same effective opacity explicitly.
      clone.querySelectorAll('path').forEach((path, index) => {
        path.style.opacity = String(this.scene!.nodes[index].opacity)
        path.removeAttribute('data-node-id')
      })
      const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml' }))
      try {
        const image = new Image()
        image.src = url
        await image.decode()
        if (!this.host) throw new Error('Renderer 已释放')
        // Canvas only rasterizes the frozen SVG; it never redraws IR geometry.
        const canvas = document.createElement('canvas')
        canvas.width = this.scene.viewport.width
        canvas.height = this.scene.viewport.height
        const ctx = canvas.getContext('2d')
        if (!ctx) throw new Error('浏览器不支持 PNG 编码')
        ctx.drawImage(image, 0, 0)
        return await new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => {
          canvas.width = 0
          canvas.height = 0
          if (!this.host) reject(new Error('Renderer 已释放'))
          else if (!blob) reject(new Error('PNG 导出失败，可保留现场后重试'))
          else if (blob.size > 5 * 1024 * 1024) reject(new Error('PNG 超过 5MiB'))
          else resolve(blob)
        }, 'image/png'))
      } finally { URL.revokeObjectURL(url) }
    })
  }

  private clear(): void {
    this.animations.forEach(animation => animation.cancel())
    this.animations = []
    this.paths = []
    this.svg?.remove()
    this.svg = null
    this.ir = null
    this.scene = null
  }

  dispose(): void { this.clear(); this.host = null }
}
