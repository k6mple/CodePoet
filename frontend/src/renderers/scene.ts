import type { DrawingIR, IRNode } from '../types/core'

export interface SampledNode {
  id: string
  type: Exclude<IRNode['type'], 'group'>
  points: [number, number][]
  // Includes ancestor opacity; renderers must not multiply style.opacity again.
  opacity: number
  style: NonNullable<IRNode['style']>
}

export interface SampledScene {
  timeMs: number
  viewport: DrawingIR['viewport']
  background: string | null
  nodes: SampledNode[]
}

type Matrix = [number, number, number, number, number, number]
const identity: Matrix = [1, 0, 0, 1, 0, 0]
function multiply(a: Matrix, b: Matrix): Matrix {
  return [a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1], a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3], a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5]]
}

// World geometry is sampled without DOM or Canvas access; preserve the original leaf draw order.
export function sampleScene(ir: DrawingIR, timeMs = 0): SampledScene {
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
  const sampled: SampledNode[] = []
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
    sampled.push({
      id: node.id, type: node.type, opacity: current.opacity, style: { ...node.style },
      points: points.map(([x, y]) => [m[0]*x + m[2]*y + m[4], m[1]*x + m[3]*y + m[5]]),
    })
  }
  return { timeMs: time, viewport: { ...viewport }, background: ir.canvas.background, nodes: sampled }
}

export function sampleTimes(ir: DrawingIR) { return ir.durationMs === 0 ? [0] : [0, Math.floor(ir.durationMs / 2), ir.durationMs] }
