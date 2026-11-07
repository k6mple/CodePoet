import type { Page } from '@/types/core'
import { request } from './client'

/** Fetch every page of a collection that is small enough to need a tree or option list. */
export async function fetchAllPages<T>(path: string, signal: AbortSignal): Promise<T[]> {
  const items: T[] = []
  let page = 1
  let total = Number.POSITIVE_INFINITY
  while (items.length < total) {
    const separator = path.includes('?') ? '&' : '?'
    const data = await request<Page<T>>(`${path}${separator}page=${page}&pageSize=100`, { signal })
    items.push(...data.items)
    total = data.total
    if (data.items.length === 0) break
    page += 1
  }
  return items
}
