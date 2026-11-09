'use client'

import { useId, useMemo } from 'react'
import type { IRNode } from '@/types/core'
import { buildIRTree, type IRLayer } from '@/utils/irTree'

function LayerBranch({ layers, selected, name, onSelect }: {
  layers: IRLayer[]
  selected: string | null
  name: string
  onSelect: (id: string) => void
}) {
  return <ul className="ir-node-list">
    {layers.map(({ node, children }) => <li key={node.id}>
      <label className={`ir-node-choice${selected === node.id ? ' is-selected' : ''}`}>
        <input type="radio" name={name} value={node.id} checked={selected === node.id}
          aria-label={`${node.id} · ${node.type}`} onChange={() => onSelect(node.id)} />
        <span><strong>{node.id}</strong> <small>{node.type}</small></span>
      </label>
      {children.length > 0 && <LayerBranch layers={children} selected={selected} name={name} onSelect={onSelect} />}
    </li>)}
  </ul>
}

export default function IRLayerTree({ nodes, selected, onSelect }: {
  nodes: IRNode[]
  selected: string | null
  onSelect: (id: string) => void
}) {
  const name = useId()
  const tree = useMemo(() => buildIRTree(nodes), [nodes])
  return <fieldset className="ir-layer-tree">
    <legend>图层与子树根节点</legend>
    <p className="rail-lead">按父子关系显示，保留同级绘制顺序。用方向键切换节点。</p>
    {tree.length > 0
      ? <div className="ir-layer-scroll"><LayerBranch layers={tree} selected={selected} name={name} onSelect={onSelect} /></div>
      : <p className="rail-lead">当前 IR 没有节点。</p>}
  </fieldset>
}
