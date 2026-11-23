import type { DrawingIR } from '../../types/core'
import {
  drawTextNode2D,
  linearGradientEndpoints,
  nodeBounds,
  paintMaterialTexture,
  paintPaperBackground,
  resolveGradientStops,
} from '../artStyle'
import { sampleScene } from '../scene'

export const RENDERER_VERSION = 'codepoet-canvas-1.0.0'

// Keep the synchronous entry point for existing previews and their single clock.
// Legacy flat IR 1.0 documents hit the exact original Canvas2D path bit-for-bit.
export function renderScene(ctx: CanvasRenderingContext2D, ir: DrawingIR, timeMs = 0) {
  const scene = sampleScene(ir, timeMs)
  const { viewport } = scene
  ctx.save()
  try {
    ctx.resetTransform()
    ctx.globalAlpha = 1
    ctx.clearRect(0, 0, viewport.width, viewport.height)
    if (scene.background !== null) {
      ctx.fillStyle = scene.background
      ctx.fillRect(0, 0, viewport.width, viewport.height)
    }
    ctx.beginPath()
    ctx.rect(0, 0, viewport.width, viewport.height)
    ctx.clip()

    if (scene.paper && scene.paper !== 'none') {
      paintPaperBackground(ctx, viewport.width, viewport.height, scene.paper, scene.background, scene.seed)
    }

    for (const node of scene.nodes) {
      if (node.type === 'text') {
        drawTextNode2D(ctx, node, viewport.x, viewport.y)
        continue
      }

      const style = node.style
      const feather = style.feather && style.feather > 0 ? style.feather : 0
      const hasArtFill = Boolean(style.gradient || node.shaderRef !== 'flat')

      if (feather > 0) {
        ctx.save()
        ctx.filter = `blur(${feather}px)`
      }

      ctx.beginPath()
      node.points.forEach(([x, y], index) => {
        if (index === 0) ctx.moveTo(x - viewport.x, y - viewport.y)
        else ctx.lineTo(x - viewport.x, y - viewport.y)
      })
      if (node.type !== 'polyline') ctx.closePath()
      ctx.globalAlpha = node.opacity
      ctx.lineJoin = style.lineJoin ?? 'miter'
      ctx.lineCap = style.lineCap ?? 'butt'

      if (style.gradient) {
        const bounds = nodeBounds(node.points, viewport.x, viewport.y)
        const stops = resolveGradientStops(style.gradient, style.fill)
        let grad: CanvasGradient
        if (style.gradient.type === 'radial') {
          const cx = bounds.minX + bounds.width * (style.gradient.cx ?? 0.5)
          const cy = bounds.minY + bounds.height * (style.gradient.cy ?? 0.5)
          const r = Math.max(bounds.width, bounds.height) * (style.gradient.r ?? 0.6)
          grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(1, r))
        } else {
          const [x0, y0, x1, y1] = linearGradientEndpoints(bounds, style.gradient.angle ?? 90)
          grad = ctx.createLinearGradient(x0, y0, x1, y1)
        }
        for (const s of stops) grad.addColorStop(s.offset, s.cssColor)
        ctx.fillStyle = grad
        ctx.fill()
        if (node.shaderRef !== 'flat') {
          ctx.save()
          ctx.clip()
          paintMaterialTexture(ctx, node, bounds, scene.seed)
          ctx.restore()
        }
      } else if (style.fill != null) {
        ctx.fillStyle = style.fill
        ctx.fill()
        if (hasArtFill && node.shaderRef !== 'flat') {
          const bounds = nodeBounds(node.points, viewport.x, viewport.y)
          ctx.save()
          ctx.clip()
          paintMaterialTexture(ctx, node, bounds, scene.seed)
          ctx.restore()
        }
      }

      if (style.stroke != null && (style.strokeWidth ?? 1) > 0) {
        ctx.strokeStyle = style.stroke
        ctx.lineWidth = style.strokeWidth ?? 1
        ctx.stroke()
      }

      if (feather > 0) {
        ctx.restore()
      }
    }
  } finally { ctx.restore() }
}
