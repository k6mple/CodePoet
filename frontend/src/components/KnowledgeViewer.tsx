'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import type { Concept, KnowledgeEntity, Page, Tag } from '@/types/core'
import { entityStatusLabel, formatDateTime, knowledgeTypeLabel, relationPredicateLabel, shortId } from '@/utils/format'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'

export default function KnowledgeViewer({ id }: { id: string }) {
  const [entity, setEntity] = useState<KnowledgeEntity | null>(null)
  const [concepts, setConcepts] = useState<Concept[]>([])
  const [tags, setTags] = useState<Tag[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setEntity(null)
    async function load() {
      try {
        const detail = await request<KnowledgeEntity>(`/knowledge/${encodeURIComponent(id)}`, { signal: controller.signal })
        if (controller.signal.aborted) return
        setEntity(detail)
        const [conceptPage, tagPage] = await Promise.allSettled([
          request<Page<Concept>>('/ontology/concepts?pageSize=100', { signal: controller.signal }),
          request<Page<Tag>>('/tags?pageSize=100', { signal: controller.signal }),
        ])
        if (controller.signal.aborted) return
        if (conceptPage.status === 'fulfilled') setConcepts(conceptPage.value.items)
        if (tagPage.status === 'fulfilled') setTags(tagPage.value.items)
      } catch (cause) {
        if (!controller.signal.aborted) setError(errorMessage(cause))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [id, attempt])

  const conceptNames = new Map(concepts.map((concept) => [concept.id, concept.name]))
  const tagNames = new Map(tags.map((tag) => [tag.id, tag.name]))
  const tagList = (ids: string[]) => ids.length > 0
    ? ids.map((tagId) => <span className="tag-chip" key={tagId}>{tagNames.get(tagId) ?? tagId}</span>)
    : <span className="muted-text">—</span>

  return <section className="knowledge-viewer" aria-labelledby="knowledge-heading">
    <nav className="back-link" aria-label="返回">
      <Link href="/search?view=knowledge">返回检索</Link>
    </nav>

    {loading
      ? <><header className="work-header"><h1 id="knowledge-heading">正在读取知识</h1></header><LoadingLines label="正在读取知识" /></>
      : error
        ? <>
          <header className="work-header"><h1 id="knowledge-heading">知识加载失败</h1></header>
          <div className="viewer-error">
            <InlineNotice tone="error" title="无法读取知识">
              知识条目可能不存在、已停用，或后端尚未实现知识接口。
              <details className="notice__details">
                <summary>查看技术详情</summary>
                <p className="notice__detail">{error}</p>
              </details>
            </InlineNotice>
            <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
          </div>
        </>
        : !entity
          ? <EmptyState title="知识条目不可用">请返回检索页重新选择。</EmptyState>
          : <>
            <header className="work-header">
              <h1 id="knowledge-heading">{entity.name}</h1>
              <div className="work-header__meta">
                <span className="mono">{knowledgeTypeLabel(entity.type)}</span>
                <span className={`status-chip is-${entity.status}`}>{entityStatusLabel(entity.status)}</span>
                {entity.timeTheme && <span className="tag-chip">{entity.timeTheme}</span>}
                <span className="mono">{formatDateTime(entity.updatedAt)}</span>
              </div>
              {entity.status === 'active' && <Link
                href={`/?entity=${encodeURIComponent(entity.id)}`}
                className="button button--secondary button--small"
              >基于此知识创作</Link>}
            </header>

            <div className="knowledge-detail-layout">
              <article className="knowledge-article">
                {entity.summary && <p className="knowledge-summary">{entity.summary}</p>}
                <div className="knowledge-content">{entity.content || '暂无正文。'}</div>

                <section className="knowledge-section" aria-labelledby="knowledge-sources-heading">
                  <h2 id="knowledge-sources-heading">来源引用</h2>
                  {entity.sourceReferences.length === 0
                    ? <p className="panel-note">没有登记来源。</p>
                    : <ul className="source-list">
                      {entity.sourceReferences.map((source, index) => <li className="source-reference" key={`${source.title}-${index}`}>
                        <strong>{source.title}</strong>
                        {source.url && <a href={source.url} target="_blank" rel="noreferrer noopener">{source.url}</a>}
                        {source.note && <p>{source.note}</p>}
                      </li>)}
                    </ul>}
                </section>

                <section className="knowledge-section" aria-labelledby="knowledge-relations-heading">
                  <h2 id="knowledge-relations-heading">知识关系</h2>
                  {entity.relations.length === 0
                    ? <p className="panel-note">没有登记关系。</p>
                    : <ul className="relation-list">
                      {entity.relations.map((relation, index) => <li className="relation-line" key={`${relation.predicate}-${relation.targetEntityId}-${index}`}>
                        <span>{relationPredicateLabel(relation.predicate)}</span>
                        <span className="mono">{relation.targetEntityId}</span>
                      </li>)}
                    </ul>}
                </section>
              </article>

              <aside className="knowledge-meta" aria-label="知识信息">
                <h2>条目信息</h2>
                <dl className="label-list">
                  <div><dt>类型</dt><dd>{knowledgeTypeLabel(entity.type)}</dd></div>
                  <div><dt>状态</dt><dd>{entityStatusLabel(entity.status)}</dd></div>
                  <div><dt>时间主题</dt><dd>{entity.timeTheme || '—'}</dd></div>
                  <div><dt>更新时间</dt><dd className="mono">{formatDateTime(entity.updatedAt)}</dd></div>
                  <div><dt>条目编号</dt><dd className="mono">{shortId(entity.id)}</dd></div>
                </dl>
                <div className="knowledge-meta__group">
                  <h3>关联概念</h3>
                  <div className="tag-line">{entity.conceptIds.length > 0
                    ? entity.conceptIds.map((conceptId) => <span className="tag-chip" key={conceptId}>{conceptNames.get(conceptId) ?? conceptId}</span>)
                    : <span className="muted-text">—</span>}</div>
                </div>
                <div className="knowledge-meta__group">
                  <h3>直接标签</h3>
                  <div className="tag-line">{tagList(entity.tagIds)}</div>
                </div>
                <div className="knowledge-meta__group">
                  <h3>继承标签</h3>
                  <div className="tag-line">{tagList(entity.inheritedTagIds)}</div>
                </div>
                <div className="knowledge-meta__group">
                  <h3>别名</h3>
                  <div className="tag-line">{entity.aliases.length > 0
                    ? entity.aliases.map((alias) => <span className="tag-chip" key={alias}>{alias}</span>)
                    : <span className="muted-text">—</span>}</div>
                </div>
                <p className="panel-note">本页只呈现服务器返回的知识内容，不执行 HTML 或用户脚本。</p>
              </aside>
            </div>
          </>}
  </section>
}
