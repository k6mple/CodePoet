'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { errorMessage, request } from '@/api/client'
import type { KnowledgeEntity, Page, SearchQuery, Template } from '@/types/core'
import { entityStatusLabel, knowledgeTypeLabel, symbolLabel } from '@/utils/format'
import { parseIdList, SYMBOL_OPTIONS } from '@/utils/template'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'

export default function SearchView({ initial }: { initial: SearchQuery }) {
  const router = useRouter()
  const [q, setQ] = useState(initial.q)
  const [entityIdsText, setEntityIdsText] = useState(initial.entityIds.join(', '))
  const [tagIdsText, setTagIdsText] = useState(initial.tagIds.join(', '))
  const [symbols, setSymbols] = useState(initial.symbols)
  const [backend, setBackend] = useState(initial.backend || 'canvas2d')
  const [timeTheme, setTimeTheme] = useState(initial.timeTheme)
  const [templates, setTemplates] = useState<Template[] | null>(null)
  const [knowledge, setKnowledge] = useState<KnowledgeEntity[] | null>(null)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  const activeView = initial.view
  const pageSize = activeView === 'templates' ? 12 : 20

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setTemplates(null)
    setKnowledge(null)
    setTotal(0)
    async function load() {
      try {
        if (initial.view === 'templates') {
          const params = new URLSearchParams({ page: String(initial.page), pageSize: '12' })
          if (initial.q) params.set('q', initial.q)
          if (initial.timeTheme) params.set('timeTheme', initial.timeTheme)
          if (initial.backend) params.set('backend', initial.backend)
          for (const id of initial.entityIds) params.append('entityIds', id)
          for (const id of initial.tagIds) params.append('tagIds', id)
          for (const symbol of initial.symbols) params.append('symbols', symbol)
          const data = await request<Page<Template>>(`/templates?${params.toString()}`, { signal: controller.signal })
          if (controller.signal.aborted) return
          setTemplates(data.items.filter((item) => item.publishedVersionId && item.supportedBackends.includes('canvas2d')))
          setTotal(data.total)
        } else {
          const params = new URLSearchParams({ page: String(initial.page), pageSize: '20' })
          if (initial.q) params.set('q', initial.q)
          if (initial.timeTheme) params.set('timeTheme', initial.timeTheme)
          for (const id of initial.entityIds) params.append('entityIds', id)
          for (const id of initial.tagIds) params.append('tagIds', id)
          const data = await request<Page<KnowledgeEntity>>(`/knowledge?${params.toString()}`, { signal: controller.signal })
          if (controller.signal.aborted) return
          setKnowledge(data.items)
          setTotal(data.total)
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(errorMessage(cause))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [initial.view, initial.q, initial.timeTheme, initial.entityIds, initial.tagIds, initial.symbols, initial.backend, initial.page, attempt])

  function go(nextView: 'templates' | 'knowledge', nextPage = 1) {
    const params = new URLSearchParams()
    params.set('view', nextView)
    if (q.trim()) params.set('q', q.trim())
    if (timeTheme.trim()) params.set('timeTheme', timeTheme.trim())
    for (const id of parseIdList(entityIdsText)) params.append('entityIds', id)
    for (const id of parseIdList(tagIdsText)) params.append('tagIds', id)
    for (const symbol of symbols) params.append('symbols', symbol)
    if (backend) params.set('backend', backend)
    if (nextPage > 1) params.set('page', String(nextPage))
    router.push(`/search?${params.toString()}`)
  }

  function clearFilters() {
    setQ('')
    setEntityIdsText('')
    setTagIdsText('')
    setSymbols([])
    setBackend('canvas2d')
    setTimeTheme('')
    router.push(`/search?view=${activeView}`)
  }

  function toggleSymbol(symbol: string) {
    setSymbols((old) => old.includes(symbol) ? old.filter((item) => item !== symbol) : [...old, symbol])
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return <section className="search-page" aria-labelledby="search-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="search-heading">组合检索</h1>
        <p>按文化实体、标签、视觉符号、渲染后端和时间主题叠加条件，检索已发布模板与知识条目。</p>
      </div>
    </header>

    <div className="manager-tabs" role="tablist" aria-label="检索结果类型">
      <button type="button" role="tab" aria-selected={activeView === 'templates'} className={`manager-tab${activeView === 'templates' ? ' is-active' : ''}`} onClick={() => go('templates')}>模板</button>
      <button type="button" role="tab" aria-selected={activeView === 'knowledge'} className={`manager-tab${activeView === 'knowledge' ? ' is-active' : ''}`} onClick={() => go('knowledge')}>知识</button>
    </div>

    <form className="search-filters" onSubmit={(event) => { event.preventDefault(); go(activeView) }}>
      <div className="search-filters__grid">
        <label className="field">
          <span>关键词</span>
          <input value={q} placeholder="名称、说明或正文" maxLength={120} onChange={(event) => setQ(event.target.value)} />
        </label>
        <label className="field">
          <span>时间主题</span>
          <input value={timeTheme} placeholder="可选" maxLength={40} onChange={(event) => setTimeTheme(event.target.value)} />
        </label>
        <label className="field">
          <span>实体 ID（逗号或空格分隔）</span>
          <input value={entityIdsText} placeholder="可留空" onChange={(event) => setEntityIdsText(event.target.value)} />
        </label>
        <label className="field">
          <span>标签 ID（逗号或空格分隔）</span>
          <input value={tagIdsText} placeholder="可留空" onChange={(event) => setTagIdsText(event.target.value)} />
        </label>
        <label className="field">
          <span>渲染后端</span>
          <select value={backend} onChange={(event) => setBackend(event.target.value)}>
            <option value="canvas2d">Canvas 2D（当前可用）</option>
            <option value="">全部后端</option>
          </select>
        </label>
      </div>
      <div className="filter-row" aria-label="按文化符号筛选">
        <span className="filter-row__label">文化符号</span>
        {SYMBOL_OPTIONS.map((option) => <button
          type="button"
          key={option.value}
          className={`filter-chip${symbols.includes(option.value) ? ' is-active' : ''}`}
          aria-pressed={symbols.includes(option.value)}
          onClick={() => toggleSymbol(option.value)}
        >{option.label}</button>)}
      </div>
      <div className="form-actions">
        <button type="submit" className="button button--primary button--small">检索</button>
        <button type="button" className="button button--secondary button--small" onClick={clearFilters}>清除条件</button>
      </div>
      <p className="field-note">同一维度内多个值按 OR 组合，跨维度按 AND 组合。符号和后端只作用于模板；知识检索使用关键词、实体、标签和时间主题。当前只启用 Canvas 2D。</p>
    </form>

    {error
      ? <div className="works-error">
        <InlineNotice tone="error" title="检索失败">
          无法读取检索结果。请查看技术详情；若后端已启动，可能是对应检索接口尚未实现。
          <details className="notice__details">
            <summary>查看技术详情</summary>
            <p className="notice__detail">{error}</p>
          </details>
        </InlineNotice>
        <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
      </div>
      : loading
        ? <LoadingLines label="正在检索" />
        : activeView === 'templates'
          ? !templates || templates.length === 0
            ? <EmptyState title="没有匹配的模板">
              调整关键词、实体/标签、符号、后端或时间主题；草稿和未发布版本不会出现在检索结果。
            </EmptyState>
            : <div className="search-results-panel">
              <div className="panel-head">
                <h2>模板结果</h2>
                <span className="mono">共 {total} 个 · 第 {initial.page} / {totalPages} 页</span>
              </div>
              <ul className="manager-list">
                {templates.map((template) => <li className="manager-row" key={template.id}>
                  <div className="manager-row__body">
                    <h3><Link href={`/templates/${encodeURIComponent(template.id)}`}>{template.metadata.name}</Link></h3>
                    <p>{template.metadata.description || '暂无说明'}</p>
                    <div className="tag-line">
                      {template.metadata.symbols?.length
                        ? template.metadata.symbols.map((symbol) => <span className="tag-chip" key={symbol}>{symbolLabel(symbol)}</span>)
                        : <span className="muted-text">未标注符号</span>}
                      {template.metadata.timeTheme && <span className="tag-chip">{template.metadata.timeTheme}</span>}
                      <span className="mono">Canvas 2D</span>
                    </div>
                  </div>
                  <div className="manager-row__side">
                    <Link href={`/templates/${encodeURIComponent(template.id)}`} className="button button--secondary button--small">查看模板</Link>
                  </div>
                </li>)}
              </ul>
              <nav className="pager" aria-label="模板结果分页">
                <button type="button" className="button button--secondary button--small" disabled={initial.page <= 1} onClick={() => go('templates', initial.page - 1)}>上一页</button>
                <span className="mono">第 {initial.page} / {totalPages} 页</span>
                <button type="button" className="button button--secondary button--small" disabled={initial.page >= totalPages} onClick={() => go('templates', initial.page + 1)}>下一页</button>
              </nav>
            </div>
          : !knowledge || knowledge.length === 0
            ? <EmptyState title="没有匹配的知识">
              调整关键词、实体/标签或时间主题；检索结果只包含当前可见的知识条目。
            </EmptyState>
            : <div className="search-results-panel">
              <div className="panel-head">
                <h2>知识结果</h2>
                <span className="mono">共 {total} 条 · 第 {initial.page} / {totalPages} 页</span>
              </div>
              <ul className="manager-list">
                {knowledge.map((item) => <li className="manager-row" key={item.id}>
                  <div className="manager-row__body">
                    <div className="knowledge-row__head">
                      <span className="mono">{knowledgeTypeLabel(item.type)}</span>
                      <span className={`status-chip is-${item.status}`}>{entityStatusLabel(item.status)}</span>
                      {item.timeTheme && <span className="tag-chip">{item.timeTheme}</span>}
                    </div>
                    <h3><Link href={`/knowledge/${encodeURIComponent(item.id)}`}>{item.name}</Link></h3>
                    <p>{item.summary || item.content.slice(0, 120) || '暂无摘要'}</p>
                    <div className="tag-line">
                      <span className="mono">直接标签 {item.tagIds.length} · 继承标签 {item.inheritedTagIds.length}</span>
                    </div>
                  </div>
                  <div className="manager-row__side">
                    <Link href={`/knowledge/${encodeURIComponent(item.id)}`} className="button button--secondary button--small">查看知识</Link>
                  </div>
                </li>)}
              </ul>
              <nav className="pager" aria-label="知识结果分页">
                <button type="button" className="button button--secondary button--small" disabled={initial.page <= 1} onClick={() => go('knowledge', initial.page - 1)}>上一页</button>
                <span className="mono">第 {initial.page} / {totalPages} 页</span>
                <button type="button" className="button button--secondary button--small" disabled={initial.page >= totalPages} onClick={() => go('knowledge', initial.page + 1)}>下一页</button>
              </nav>
            </div>}
  </section>
}
