import { useState } from 'react'
import type { DrawingIR, Generation } from '@/types/core'
import { InlineNotice } from './Feedback'

export function parseIR(text: string): DrawingIR {
  const value = JSON.parse(text)
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('完整 IR 必须是 JSON 对象。')
  return value as DrawingIR
}

function collectSubtree(ir: DrawingIR, rootId: string) {
  const children = new Map<string | null, typeof ir.nodes>()
  for (const node of ir.nodes) {
    const siblings = children.get(node.parentId) ?? []
    siblings.push(node)
    children.set(node.parentId, siblings)
  }
  const ids = new Set([rootId])
  const queue = [rootId]
  for (const id of queue) {
    for (const node of children.get(id) ?? []) {
      if (ids.has(node.id)) continue
      ids.add(node.id)
      queue.push(node.id)
    }
  }
  return ir.nodes.filter((node) => ids.has(node.id))
}

export default function IRControls({ generation, draft, onDraft, selected, onSelect, disabled, subgraph, onApply, onSubgraph }: {
  generation: Generation
  draft: string
  onDraft: (value: string) => void
  selected: string | null
  onSelect: (id: string) => void
  disabled: boolean
  subgraph: boolean
  onApply: (ir: DrawingIR) => void
  onSubgraph: (edit: { rootNodeId: string; replacementNodes: unknown[]; viewport: DrawingIR['viewport'] }) => void
}) {
  const [error, setError] = useState('')
  const [localNodes, setLocalNodes] = useState<{ rootId: string; text: string }>()
  const [viewport, setViewport] = useState(() => Object.fromEntries(
    Object.entries(generation.ir.viewport).map(([key, value]) => [key, String(value)]),
  ))
  const root = generation.ir.nodes.find((node) => node.id === selected) ?? generation.ir.nodes[0]
  const replacement = localNodes?.rootId === root?.id
    ? localNodes?.text ?? ''
    : JSON.stringify(root ? collectSubtree(generation.ir, root.id) : [], null, 2)
  const ancestors = []
  const seen = new Set([root?.id])
  let parentId = root?.parentId
  while (parentId && !seen.has(parentId)) {
    ancestors.unshift(parentId)
    seen.add(parentId)
    parentId = generation.ir.nodes.find((node) => node.id === parentId)?.parentId ?? null
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
      const fields = ['x', 'y', 'width', 'height'] as const
      for (const field of fields) {
        if (viewport[field].trim() === '' || !Number.isInteger(Number(viewport[field]))) {
          throw new Error(`裁剪字段 ${field} 必须是整数。`)
        }
      }
      onSubgraph({
        rootNodeId: root.id, replacementNodes: nodes,
        viewport: { x: Number(viewport.x), y: Number(viewport.y), width: Number(viewport.width), height: Number(viewport.height) },
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '子树 JSON 无效。')
    }
  }

  return <fieldset disabled={disabled}>
    <legend className="sr-only">编辑 IR</legend>
    <label className="field">完整 IR JSON
      <textarea className="mono p1-json" rows={9} spellCheck={false} value={draft} onChange={(event) => onDraft(event.target.value)} />
    </label>
    <div className="param-actions">
      <button type="button" className="button button--secondary button--small" onClick={() => applyIR(true)}>格式化 JSON</button>
      <button type="button" className="button button--primary button--small" onClick={() => applyIR(false)}>校验并应用 IR</button>
    </div>
    {subgraph && root && <details className="tech-details">
      <summary>编辑子树与裁剪</summary>
      <label className="field">根节点
        <select value={root.id} onChange={(event) => onSelect(event.target.value)}>
          {generation.ir.nodes.map((node) => <option key={node.id} value={node.id}>
            {node.id} · {node.type}{node.parentId ? `（父：${node.parentId}）` : ''}
          </option>)}
        </select>
      </label>
      <p className="rail-lead">只读祖先：{ancestors.join(' → ') || '无'}。保留根 ID 和父节点。</p>
      <label className="field">子树节点 JSON
        <textarea className="mono p1-json" rows={6} spellCheck={false} value={replacement}
          onChange={(event) => setLocalNodes({ rootId: root.id, text: event.target.value })} />
      </label>
      <div className="p1-viewport">
        {Object.entries(viewport).map(([key, value]) => <label className="field" key={key}>{key}
          <input type="number" step={1} value={value} onChange={(event) => setViewport({ ...viewport, [key]: event.target.value })} />
        </label>)}
      </div>
      <button type="button" className="button button--primary button--small" onClick={applySubgraph}>校验并应用子树</button>
    </details>}
    {error && <InlineNotice tone="error" title="编辑未应用">{error}</InlineNotice>}
  </fieldset>
}
