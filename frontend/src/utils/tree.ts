export interface TreeNode<T> {
  item: T
  depth: number
  path: string[]
}

/** Flatten a single-parent tree in name order; orphan and cyclic rows stay reachable. */
export function flattenTree<T extends { id: string; name: string; parentId: string | null }>(items: T[]): TreeNode<T>[] {
  const byParent = new Map<string | null, T[]>()
  for (const item of items) {
    const list = byParent.get(item.parentId) ?? []
    list.push(item)
    byParent.set(item.parentId, list)
  }
  for (const list of byParent.values()) {
    list.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
  }
  const result: TreeNode<T>[] = []
  const visited = new Set<string>()
  function walk(parentId: string | null, depth: number, path: string[]) {
    for (const item of byParent.get(parentId) ?? []) {
      if (visited.has(item.id)) continue
      visited.add(item.id)
      const nextPath = [...path, item.name]
      result.push({ item, depth, path: nextPath })
      walk(item.id, depth + 1, nextPath)
    }
  }
  walk(null, 0, [])
  for (const item of items) {
    if (!visited.has(item.id)) result.push({ item, depth: 0, path: [item.name] })
  }
  return result
}

/** Include the root itself; used to keep a node out of its own parent choices. */
export function descendantIds<T extends { id: string; parentId: string | null }>(items: T[], rootId: string): Set<string> {
  const children = new Map<string, string[]>()
  for (const item of items) {
    if (!item.parentId) continue
    const list = children.get(item.parentId) ?? []
    list.push(item.id)
    children.set(item.parentId, list)
  }
  const result = new Set<string>()
  const stack = [rootId]
  while (stack.length) {
    const id = stack.pop()
    if (!id || result.has(id)) continue
    result.add(id)
    for (const child of children.get(id) ?? []) stack.push(child)
  }
  return result
}

export function treeLabel(path: string[]) {
  return path.join(' / ')
}
