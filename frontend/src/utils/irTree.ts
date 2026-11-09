import type { IRNode } from '@/types/core'

export interface IRLayer {
  node: IRNode
  children: IRLayer[]
}

/** Keep sibling draw order. Defensive fallbacks leave malformed rows inspectable. */
export function buildIRTree(nodes: IRNode[]): IRLayer[] {
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const children = new Map<string, IRNode[]>()
  for (const node of nodes) {
    if (node.parentId !== null) {
      const siblings = children.get(node.parentId) ?? []
      siblings.push(node)
      children.set(node.parentId, siblings)
    }
  }
  const visited = new Set<string>()
  function branch(node: IRNode): IRLayer[] {
    if (visited.has(node.id)) return []
    visited.add(node.id)
    return [{ node, children: (children.get(node.id) ?? []).flatMap(branch) }]
  }
  const roots = nodes.filter((node) => node.parentId === null || !byId.has(node.parentId))
  const tree = roots.flatMap(branch)
  for (const node of nodes) tree.push(...branch(node))
  return tree
}
