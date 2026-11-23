import type { DrawingIR } from '../../types/core'
import {
  calligraphyFont,
  createPrng,
  linearGradientEndpoints,
  nodeBounds,
  parseHex,
  resolveGradientStops,
} from '../artStyle'
import { sampleScene, type SampledNode, type SampledScene } from '../scene'
import type { Renderer } from '../types'

export const CSS_RENDERER_VERSION = 'codepoet-css-1.0.0'
const SVG_NS = 'http://www.w3.org/2000/svg'

export class CssRenderer implements Renderer {
  readonly backend = 'css' as const
  readonly rendererVersion = CSS_RENDERER_VERSION
  private host: HTMLElement | null
  private svg: SVGSVGElement | null = null
  private defs: SVGDefsElement | null = null
  private ir: DrawingIR | null = null
  private scene: SampledScene | null = null
  private elements: SVGElement[] = []
  private animations: Animation[] = []
  private pending: Promise<unknown> = Promise.resolve()

  constructor(host: HTMLElement) { this.host = host }

  private enqueue<T>(operation: () => T | Promise<T>): Promise<T> {
    const next = this.pending.then(() => {
      if (!this.host) throw new Error('Renderer 已释放')
      return operation()
    })
    this.pending = next.catch(() => undefined)
    return next
  }

  private buildPaperLayer(svg: SVGSVGElement, defs: SVGDefsElement, scene: SampledScene): void {
    const paper = scene.paper
    if (!paper || paper === 'none') return
    const { width, height } = scene.viewport
    const rand = createPrng((scene.seed ^ 0x5A17C3) >>> 0)
    const bg = scene.background ? parseHex(scene.background) : [245, 240, 230]

    const vig = document.createElementNS(SVG_NS, 'radialGradient')
    vig.id = 'cp-paper-vignette'
    vig.setAttribute('cx', '48%')
    vig.setAttribute('cy', '44%')
    vig.setAttribute('r', '66%')
    const edgeAlpha = paper === 'aged' ? 0.11 : paper === 'silk' ? 0.08 : 0.075
    const s0 = document.createElementNS(SVG_NS, 'stop')
    s0.setAttribute('offset', '0%')
    s0.setAttribute('stop-color', '#fffcf4')
    s0.setAttribute('stop-opacity', paper === 'aged' ? '0.05' : '0.04')
    const s1 = document.createElementNS(SVG_NS, 'stop')
    s1.setAttribute('offset', '68%')
    s1.setAttribute('stop-color', '#d2c4aa')
    s1.setAttribute('stop-opacity', '0')
    const s2 = document.createElementNS(SVG_NS, 'stop')
    s2.setAttribute('offset', '100%')
    s2.setAttribute('stop-color', '#917e60')
    s2.setAttribute('stop-opacity', String(edgeAlpha))
    vig.append(s0, s1, s2)
    defs.append(vig)

    const paperGroup = document.createElementNS(SVG_NS, 'g')
    paperGroup.setAttribute('data-paper-layer', paper)
    const vigRect = document.createElementNS(SVG_NS, 'rect')
    vigRect.setAttribute('width', String(width))
    vigRect.setAttribute('height', String(height))
    vigRect.setAttribute('fill', 'url(#cp-paper-vignette)')
    paperGroup.append(vigRect)

    if (paper === 'silk') {
      const dParts: string[] = []
      for (let y = 2; y < height; y += 6) dParts.push(`M0 ${y} H${width}`)
      for (let x = 2; x < width; x += 8) dParts.push(`M${x} 0 V${height}`)
      const mesh = document.createElementNS(SVG_NS, 'path')
      mesh.setAttribute('d', dParts.join(' '))
      mesh.setAttribute('stroke', `rgb(${Math.max(0, bg[0] - 26)},${Math.max(0, bg[1] - 26)},${Math.max(0, bg[2] - 30)})`)
      mesh.setAttribute('stroke-width', '0.65')
      mesh.setAttribute('stroke-opacity', '0.048')
      mesh.setAttribute('fill', 'none')
      paperGroup.append(mesh)
    } else {
      const darkPaths: string[] = []
      const lightPaths: string[] = []
      const count = paper === 'aged' ? 200 : 150
      for (let i = 0; i < count; i += 1) {
        const x = (rand() * width).toFixed(1)
        const y = (rand() * height).toFixed(1)
        const len = 5 + rand() * 13
        const ang = rand() * Math.PI * 2
        const x2 = (Number(x) + Math.cos(ang) * len).toFixed(1)
        const y2 = (Number(y) + Math.sin(ang) * len).toFixed(1)
        if (rand() > 0.4) darkPaths.push(`M${x} ${y} L${x2} ${y2}`)
        else lightPaths.push(`M${x} ${y} L${x2} ${y2}`)
      }
      if (darkPaths.length) {
        const p = document.createElementNS(SVG_NS, 'path')
        p.setAttribute('d', darkPaths.join(' '))
        p.setAttribute('stroke', `rgb(${Math.max(0, bg[0] - 42)},${Math.max(0, bg[1] - 44)},${Math.max(0, bg[2] - 48)})`)
        p.setAttribute('stroke-width', '0.65')
        p.setAttribute('stroke-opacity', '0.06')
        p.setAttribute('fill', 'none')
        paperGroup.append(p)
      }
      if (lightPaths.length) {
        const p = document.createElementNS(SVG_NS, 'path')
        p.setAttribute('d', lightPaths.join(' '))
        p.setAttribute('stroke', '#fffdf7')
        p.setAttribute('stroke-width', '0.65')
        p.setAttribute('stroke-opacity', '0.07')
        p.setAttribute('fill', 'none')
        paperGroup.append(p)
      }
    }
    svg.append(paperGroup)
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

      const defs = document.createElementNS(SVG_NS, 'defs')
      svg.append(defs)

      if (ir.canvas.background !== null) {
        const background = document.createElementNS(SVG_NS, 'rect')
        background.setAttribute('width', String(ir.viewport.width))
        background.setAttribute('height', String(ir.viewport.height))
        background.setAttribute('fill', ir.canvas.background)
        svg.append(background)
      }

      this.buildPaperLayer(svg, defs, first)

      first.nodes.forEach((node, index) => {
        if (node.style.feather && node.style.feather > 0) {
          const filter = document.createElementNS(SVG_NS, 'filter')
          filter.id = `cp-blur-${index}`
          filter.setAttribute('x', '-30%')
          filter.setAttribute('y', '-30%')
          filter.setAttribute('width', '160%')
          filter.setAttribute('height', '160%')
          const blur = document.createElementNS(SVG_NS, 'feGaussianBlur')
          blur.setAttribute('stdDeviation', String(node.style.feather))
          filter.append(blur)
          defs.append(filter)
        }

        let el: SVGElement
        if (node.type === 'text') {
          el = document.createElementNS(SVG_NS, 'g')
          el.dataset.nodeId = node.id
        } else {
          const path = document.createElementNS(SVG_NS, 'path')
          path.dataset.nodeId = node.id
          path.setAttribute('stroke-linecap', node.style.lineCap ?? 'butt')
          path.setAttribute('stroke-linejoin', node.style.lineJoin ?? 'miter')
          path.setAttribute('stroke-miterlimit', '10')
          if (node.style.feather && node.style.feather > 0) {
            path.setAttribute('filter', `url(#cp-blur-${index})`)
          }
          el = path
        }

        svg.append(el)
        this.elements.push(el)
        // Paused WAAPI effects use the page's clock; no autonomous animation loop.
        const animation = el.animate([{ opacity: 0 }, { opacity: 1 }], {
          duration: 1, fill: 'both', easing: 'linear',
        })
        animation.pause()
        this.animations.push(animation)
      })
      this.svg = svg
      this.defs = defs
      this.host!.append(svg)
      this.draw(0)
    })
  }

  private syncGradient(node: SampledNode, index: number, vx: number, vy: number): string {
    const defs = this.defs!
    const gradId = `cp-grad-${index}`
    defs.querySelector(`#${gradId}`)?.remove()
    const gSpec = node.style.gradient!
    const bounds = nodeBounds(node.points, vx, vy)
    const stops = resolveGradientStops(gSpec, node.style.fill)
    let gradEl: SVGElement
    if (gSpec.type === 'radial') {
      gradEl = document.createElementNS(SVG_NS, 'radialGradient')
      gradEl.setAttribute('gradientUnits', 'userSpaceOnUse')
      gradEl.setAttribute('cx', String(bounds.minX + bounds.width * (gSpec.cx ?? 0.5)))
      gradEl.setAttribute('cy', String(bounds.minY + bounds.height * (gSpec.cy ?? 0.5)))
      gradEl.setAttribute('r', String(Math.max(1, Math.max(bounds.width, bounds.height) * (gSpec.r ?? 0.6))))
    } else {
      const [x0, y0, x1, y1] = linearGradientEndpoints(bounds, gSpec.angle ?? 90)
      gradEl = document.createElementNS(SVG_NS, 'linearGradient')
      gradEl.setAttribute('gradientUnits', 'userSpaceOnUse')
      gradEl.setAttribute('x1', String(x0))
      gradEl.setAttribute('y1', String(y0))
      gradEl.setAttribute('x2', String(x1))
      gradEl.setAttribute('y2', String(y1))
    }
    gradEl.id = gradId
    for (const s of stops) {
      const stopEl = document.createElementNS(SVG_NS, 'stop')
      stopEl.setAttribute('offset', `${(s.offset * 100).toFixed(2)}%`)
      stopEl.setAttribute('stop-color', s.hex)
      stopEl.setAttribute('stop-opacity', String(s.opacity))
      gradEl.append(stopEl)
    }
    defs.append(gradEl)
    return `url(#${gradId})`
  }

  private drawTextElement(group: SVGGElement, node: SampledNode, vx: number, vy: number): void {
    const geom = node.text
    if (!geom) return
    const [a, b, c, d, e, f] = node.matrix
    group.setAttribute('transform', `matrix(${a} ${b} ${c} ${d} ${e - vx} ${f - vy})`)
    while (group.firstChild) group.firstChild.remove()
    const fontSize = geom.fontSize ?? 20
    const letterSpacing = geom.letterSpacing ?? 4
    const writingMode = geom.writingMode ?? 'vertical'
    const chars = Array.from(geom.text)
    const fontStr = calligraphyFont(geom.fontFamily, fontSize)
    chars.forEach((ch, idx) => {
      const t = document.createElementNS(SVG_NS, 'text')
      const cx = writingMode === 'vertical' ? (geom.x ?? 0) : (geom.x ?? 0) + idx * (fontSize + letterSpacing)
      const cy = writingMode === 'vertical' ? (geom.y ?? 0) + idx * (fontSize + letterSpacing) : (geom.y ?? 0)
      t.setAttribute('x', String(cx))
      t.setAttribute('y', String(cy + fontSize * 0.86))
      t.setAttribute('text-anchor', writingMode === 'vertical' ? 'middle' : 'start')
      t.style.font = fontStr
      t.setAttribute('fill', node.style.fill ?? 'none')
      if (node.style.stroke && (node.style.strokeWidth ?? 0) > 0) {
        t.setAttribute('stroke', node.style.stroke)
        t.setAttribute('stroke-width', String(node.style.strokeWidth))
        t.setAttribute('stroke-linejoin', 'round')
      }
      t.textContent = ch
      group.append(t)
    })
  }

  private draw(timeMs: number): void {
    if (!this.ir || !this.svg) throw new Error('请先加载 IR')
    const scene = sampleScene(this.ir, timeMs)
    scene.nodes.forEach((node, index) => {
      const el = this.elements[index]
      if (node.type === 'text') {
        this.drawTextElement(el as SVGGElement, node, scene.viewport.x, scene.viewport.y)
      } else {
        const path = el as SVGPathElement
        const d = node.points.map(([x, y], point) => `${point ? 'L' : 'M'}${x - scene.viewport.x} ${y - scene.viewport.y}`).join(' ')
          + (node.type === 'polyline' ? '' : ' Z')
        path.setAttribute('d', d)
        const fillValue = node.style.gradient
          ? this.syncGradient(node, index, scene.viewport.x, scene.viewport.y)
          : (node.style.fill ?? 'none')
        path.setAttribute('fill', fillValue)
        path.setAttribute('stroke', (node.style.strokeWidth ?? 1) > 0 ? node.style.stroke ?? 'none' : 'none')
        path.setAttribute('stroke-width', String(node.style.strokeWidth ?? 1))
        path.setAttribute('stroke-linecap', node.style.lineCap ?? 'butt')
        path.setAttribute('stroke-linejoin', node.style.lineJoin ?? 'miter')
      }
      // Reuse a fixed linear opacity effect; the shared sampler supplies the
      // exact effective value, including products of animated ancestors.
      this.animations[index].currentTime = node.opacity
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
      const nodeEls = clone.querySelectorAll('[data-node-id]')
      nodeEls.forEach((el, index) => {
        ;(el as SVGElement).style.opacity = String(this.scene!.nodes[index].opacity)
        el.removeAttribute('data-node-id')
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
    this.elements = []
    this.svg?.remove()
    this.svg = null
    this.defs = null
    this.ir = null
    this.scene = null
  }

  dispose(): void { this.clear(); this.host = null }
}
