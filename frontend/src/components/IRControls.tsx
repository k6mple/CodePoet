import { useState } from 'react'
import type { DrawingIR } from '@/types/core'
import { descendantIds } from '@/utils/tree'
import { InlineNotice } from './Feedback'
import IRLayerTree from './IRLayerTree'

export function parseIR(text: string): DrawingIR {
  const value = JSON.parse(text)
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('完整 IR 必须是 JSON 对象。')
  return value as DrawingIR
}

function collectSubtree(ir: DrawingIR, rootId: string) {
  const ids = descendantIds(ir.nodes, rootId)
  return ir.nodes.filter((node) => ids.has(node.id))
}

export default function IRControls({ ir, draft, onDraft, selected, onSelect, disabled, subgraph, onApply, onSubgraph,
  applyLabel = '校验并应用 IR', subgraphLabel = '校验并应用子树' }: {
  ir: DrawingIR
  draft: string
  onDraft: (value: string) => void
  selected: string | null
  onSelect: (id: string) => void
  disabled: boolean
  subgraph: boolean
  onApply: (ir: DrawingIR) => void
  onSubgraph: (edit: { rootNodeId: string; replacementNodes: unknown[]; viewport: DrawingIR['viewport'] }) => void
  applyLabel?: string
  subgraphLabel?: string
}) {
  const [error, setError] = useState('')
  const [nodeDrafts, setNodeDrafts] = useState(() => new Map<string, string>())
  const [viewport, setViewport] = useState(() => Object.fromEntries(
    Object.entries(ir.viewport).map(([key, value]) => [key, String(value)]),
  ))
  const root = ir.nodes.find((node) => node.id === selected) ?? ir.nodes[0]
  const replacement = (root && nodeDrafts.get(root.id)) ?? JSON.stringify(root ? collectSubtree(ir, root.id) : [], null, 2)
  const ancestors = []
  const seen = new Set([root?.id])
  let parentId = root?.parentId
  while (parentId && !seen.has(parentId)) {
    ancestors.unshift(parentId)
    seen.add(parentId)
    parentId = ir.nodes.find((node) => node.id === parentId)?.parentId ?? null
  }

  function applyIR(format: boolean) {
    setError('')
    try {
      const ir = parseIR(draft)
      if (format) onDraft(JSON.stringify(ir, null, 2))
      else onApply(ir)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'IR JSON 无效。')
    }
  }

  function applySubgraph() {
    if (!root) return
    setError('')
    try {
      const nodes: unknown = JSON.parse(replacement)
      if (!Array.isArray(nodes) || nodes.length === 0) throw new Error('子树必须是非空 JSON 数组。')
      const rootNode = nodes.find((node) => node && typeof node === 'object' && node.id === root.id)
      if (!rootNode || rootNode.parentId !== root.parentId) throw new Error('必须保留根节点 ID 和原 parentId；祖先节点保持只读。')
      const fields = ['x', 'y', 'width', 'height'] as const
      for (const field of fields) {
        if (viewport[field].trim() === '' || !Number.isInteger(Number(viewport[field]))) {
          throw new Error(`裁剪字段 ${field} 必须是整数。`)
        }
      }
      const crop = { x: Number(viewport.x), y: Number(viewport.y), width: Number(viewport.width), height: Number(viewport.height) }
      if (crop.x < 0 || crop.y < 0 || crop.width <= 0 || crop.height <= 0
        || crop.x + crop.width > ir.canvas.width || crop.y + crop.height > ir.canvas.height) {
        throw new Error('裁剪必须位于画布内，宽度和高度必须大于 0。')
      }
      onSubgraph({
        rootNodeId: root.id, replacementNodes: nodes,
        viewport: crop,
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '子树 JSON 无效。')
    }
  }

  return <fieldset disabled={disabled}>
    <legend className="sr-only">编辑 IR</legend>
    <IRLayerTree nodes={ir.nodes} selected={root?.id ?? null} onSelect={(id) => { setError(''); onSelect(id) }} />
    <label className="field">完整 IR JSON
      <textarea className="mono p1-json" rows={9} spellCheck={false} value={draft} onChange={(event) => onDraft(event.target.value)} />
    </label>
    <div className="param-actions">
      <button type="button" className="button button--secondary button--small" onClick={() => applyIR(true)}>格式化 JSON</button>
      <button type="button" className="button button--primary button--small" onClick={() => applyIR(false)}>{applyLabel}</button>
    </div>
    {subgraph && root && <details className="tech-details">
      <summary>编辑子树与裁剪</summary>
      <p className="rail-lead">子树根节点：<strong>{root.id}</strong>。在上方图层列表切换。</p>
      <p className="rail-lead">只读祖先：{ancestors.join(' → ') || '无'}。保留根 ID 和父节点。</p>
      <label className="field">子树节点 JSON
        <textarea className="mono p1-json" rows={6} spellCheck={false} value={replacement}
          onChange={(event) => setNodeDrafts((current) => new Map(current).set(root.id, event.target.value))} />
      </label>
      <div className="p1-viewport">
        {Object.entries(viewport).map(([key, value]) => <label className="field" key={key}>{key}
          <input type="number" step={1} value={value} onChange={(event) => setViewport({ ...viewport, [key]: event.target.value })} />
        </label>)}
      </div>
      <button type="button" className="button button--primary button--small" onClick={applySubgraph}>{subgraphLabel}</button>
    </details>}
    {!subgraph && <p className="rail-lead">当前后端未启用子树编辑，可查看图层或编辑完整 IR。</p>}
    {error && <InlineNotice tone="error" title="编辑未应用">{error}</InlineNotice>}
  </fieldset>
}
