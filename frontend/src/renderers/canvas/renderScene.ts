import type { DrawingIR } from '../../types/core'

export const RENDERER_VERSION = 'codepoet-canvas-1.0.0'
type Matrix = [number, number, number, number, number, number]
const identity: Matrix = [1, 0, 0, 1, 0, 0]
function multiply(a: Matrix, b: Matrix): Matrix {
  return [a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1], a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3], a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5]]
}

// All geometry is transformed before stroking so strokeWidth stays in logical pixels.
// Preview, fixture and exported PNG share this function. Never executes IR as code.
export function renderScene(ctx: CanvasRenderingContext2D, ir: DrawingIR, timeMs = 0) {
  const { viewport } = ir
  if (ir.nodes.length > 200 || viewport.width < 1 || viewport.height < 1 || viewport.width > 800 || viewport.height > 600) {
    throw new Error('IR 超出 core-demo 画布/节点限制')
  }
  const time = Math.max(0, Math.min(timeMs, ir.durationMs))
  const nodes = new Map(ir.nodes.map((node) => [node.id, node]))
  const states = new Map<string, { matrix: Matrix; opacity: number }>()
  const visiting = new Set<string>()
  function state(id: string): { matrix: Matrix; opacity: number } {
    const cached = states.get(id)
    if (cached) return cached
    const node = nodes.get(id)
    if (!node || visiting.has(id) || visiting.size >= 4) throw new Error('IR 父节点不存在、循环或超过4层')
    visiting.add(id)
    const t = { x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1, ...node.transform }
    let opacity = node.style?.opacity ?? 1
    for (const track of ir.animations) {
      if (track.nodeId !== id) continue
      const fraction = Math.max(0, Math.min(1, (time-track.startMs)/(track.endMs-track.startMs)))
      const value = track.from + (track.to-track.from)*fraction
      if (track.property === 'opacity') opacity = value
      else t[track.property] = value
    }
    const angle = t.rotation * Math.PI / 180
    const local: Matrix = [Math.cos(angle)*t.scaleX, Math.sin(angle)*t.scaleX, -Math.sin(angle)*t.scaleY, Math.cos(angle)*t.scaleY, t.x, t.y]
    if (node.parentId && nodes.get(node.parentId)?.type !== 'group') throw new Error('IR parentId 必须指向 group')
    const parent = node.parentId ? state(node.parentId) : { matrix: identity, opacity: 1 }
    const result = { matrix: multiply(parent.matrix, local), opacity: parent.opacity * opacity }
    states.set(id, result)
    visiting.delete(id)
    return result
  }
  ctx.save()
  try {
    ctx.resetTransform()
    ctx.globalAlpha = 1
    ctx.clearRect(0, 0, viewport.width, viewport.height)
    if (ir.canvas.background !== null) {
      ctx.fillStyle = ir.canvas.background
      ctx.fillRect(0, 0, viewport.width, viewport.height)
    }
    ctx.beginPath()
    ctx.rect(0, 0, viewport.width, viewport.height)
    ctx.clip()
    for (const node of ir.nodes) {
      const current = state(node.id)
      if (node.type === 'group') continue
      if (node.material && node.material.shaderRef !== 'flat') throw new Error('仅支持 flat 材质')
      const geometry = node.geometry
      let points = geometry?.points ?? []
      if (node.type === 'circle') {
        if (geometry?.r === undefined) throw new Error('圆缺少半径')
        points = Array.from({ length: 64 }, (_, index) => {
          const angle = index * Math.PI * 2 / 64
          return [(geometry.cx ?? 0) + geometry.r! * Math.cos(angle), (geometry.cy ?? 0) + geometry.r! * Math.sin(angle)]
        })
      }
      if (!points.length) throw new Error('IR 图元缺少顶点')
      const m = current.matrix
      ctx.beginPath()
      points.forEach(([x, y], index) => {
        const px = m[0]*x + m[2]*y + m[4] - viewport.x
        const py = m[1]*x + m[3]*y + m[5] - viewport.y
        if (index === 0) ctx.moveTo(px, py)
        else ctx.lineTo(px, py)
      })
      if (node.type !== 'polyline') ctx.closePath()
      ctx.globalAlpha = current.opacity
      ctx.lineJoin = 'miter'
      ctx.lineCap = 'butt'
      const style = node.style
      if (style?.fill != null) { ctx.fillStyle = style.fill; ctx.fill() }
      if (style?.stroke != null && (style.strokeWidth ?? 1) > 0) {
        ctx.strokeStyle = style.stroke
        ctx.lineWidth = style.strokeWidth ?? 1
        ctx.stroke()
      }
    }
  } finally { ctx.restore() }
}

export function sampleTimes(ir: DrawingIR) { return ir.durationMs === 0 ? [0] : [0, Math.floor(ir.durationMs / 2), ir.durationMs] }
export async function exportFrame(ir: DrawingIR, timeMs: number): Promise<Blob> {
  const canvas = document.createElement('canvas')
  canvas.width = ir.viewport.width
  canvas.height = ir.viewport.height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('浏览器不支持 Canvas2D')
  renderScene(ctx, ir, timeMs)
  return new Promise((resolve, reject) => canvas.toBlob((blob) => {
    if (!blob) reject(new Error('PNG 导出失败，可保留现场后重试'))
    else if (blob.size > 5 * 1024 * 1024) reject(new Error('PNG 超过 5MiB'))
    else resolve(blob)
  }, 'image/png'))
}
