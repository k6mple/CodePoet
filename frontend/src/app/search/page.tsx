import SearchView from '@/components/SearchView'
import type { SearchQuery } from '@/types/core'

type RawParams = Record<string, string | string[] | undefined>

const first = (value: string | string[] | undefined) => Array.isArray(value) ? value[0] : value
const all = (value: string | string[] | undefined) => value === undefined ? [] : Array.isArray(value) ? value : [value]

export default async function SearchPage({ searchParams }: { searchParams: Promise<RawParams> }) {
  const raw = await searchParams
  const backend = first(raw.backend)
  const initial: SearchQuery = {
    view: first(raw.view) === 'knowledge' ? 'knowledge' : 'templates',
    q: first(raw.q) ?? '',
    entityIds: all(raw.entityIds),
    tagIds: all(raw.tagIds),
    symbols: all(raw.symbols),
    backend: backend === undefined ? 'canvas2d' : backend,
    timeTheme: first(raw.timeTheme) ?? '',
    page: Math.max(1, Number(first(raw.page)) || 1),
  }
  return <main id="main-content" className="page"><SearchView key={JSON.stringify(initial)} initial={initial} /></main>
}
