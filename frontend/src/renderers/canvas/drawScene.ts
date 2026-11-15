import type { DrawingIR } from '../../types/core'
import { sampleScene } from '../scene'

export const RENDERER_VERSION = 'codepoet-canvas-1.0.0'

// Keep the synchronous entry point for existing previews and their single clock.
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
    for (const node of scene.nodes) {
      ctx.beginPath()
      node.points.forEach(([x, y], index) => {
        if (index === 0) ctx.moveTo(x - viewport.x, y - viewport.y)
        else ctx.lineTo(x - viewport.x, y - viewport.y)
      })
      if (node.type !== 'polyline') ctx.closePath()
      ctx.globalAlpha = node.opacity
      ctx.lineJoin = 'miter'
      ctx.lineCap = 'butt'
      const style = node.style
      if (style.fill != null) { ctx.fillStyle = style.fill; ctx.fill() }
      if (style.stroke != null && (style.strokeWidth ?? 1) > 0) {
        ctx.strokeStyle = style.stroke
        ctx.lineWidth = style.strokeWidth ?? 1
        ctx.stroke()
      }
    }
  } finally { ctx.restore() }
}
