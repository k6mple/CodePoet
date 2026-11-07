'use client'

import { useCallback, useEffect, useMemo, useState, type KeyboardEvent } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import { fetchAllPages } from '@/api/pages'
import type { Concept, KnowledgeEntity, Page, Tag } from '@/types/core'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'
import ConceptPanel from './ConceptPanel'
import KnowledgePanel from './KnowledgePanel'
import TagPanel from './TagPanel'

type TabKey = 'knowledge' | 'concepts' | 'tags'

export default function KnowledgeManager() {
  const { user, loading, openLogin } = useSession()
  const [tab, setTab] = useState<TabKey>('knowledge')
  const [concepts, setConcepts] = useState<Concept[]>([])
  const [tags, setTags] = useState<Tag[]>([])
  const [entities, setEntities] = useState<KnowledgeEntity[]>([])
  const [referenceLoading, setReferenceLoading] = useState(false)
  const [conceptError, setConceptError] = useState('')
  const [tagError, setTagError] = useState('')
  const [entityError, setEntityError] = useState('')
  const [referenceAttempt, setReferenceAttempt] = useState(0)
  const referenceError = conceptError || tagError || entityError

  const canWriteKnowledge = Boolean(user?.permissions.includes('knowledge.write'))
  const canWriteOntology = Boolean(user?.permissions.includes('ontology.write'))
  const canWriteTags = Boolean(user?.permissions.includes('tags.write'))
  const availableTabs = useMemo(() => [
    ...(canWriteKnowledge ? [{ key: 'knowledge' as const, label: '知识' }] : []),
    ...(canWriteOntology ? [{ key: 'concepts' as const, label: '概念' }] : []),
    ...(canWriteTags ? [{ key: 'tags' as const, label: '标签' }] : []),
  ], [canWriteKnowledge, canWriteOntology, canWriteTags])
  const canManage = availableTabs.length > 0
  const activeTab = availableTabs.some((item) => item.key === tab) ? tab : availableTabs[0]?.key

  useEffect(() => {
    if (!canManage) return
    if (!availableTabs.some((item) => item.key === tab)) setTab(availableTabs[0].key)
  }, [canManage, availableTabs, tab])

  useEffect(() => {
    if (!user || !canManage) return
    const controller = new AbortController()
    setReferenceLoading(true)
    setConceptError('')
    setTagError('')
    setEntityError('')
    Promise.allSettled([
      fetchAllPages<Concept>('/ontology/concepts?scope=manage', controller.signal),
      fetchAllPages<Tag>('/tags?scope=manage', controller.signal),
      request<Page<KnowledgeEntity>>('/knowledge?scope=manage&page=1&pageSize=100', { signal: controller.signal })
        .then((data) => data.items),
    ]).then((results) => {
      if (controller.signal.aborted) return
      if (results[0].status === 'fulfilled') setConcepts(results[0].value)
      else setConceptError(errorMessage(results[0].reason))
      if (results[1].status === 'fulfilled') setTags(results[1].value)
      else setTagError(errorMessage(results[1].reason))
      if (results[2].status === 'fulfilled') setEntities(results[2].value)
      else setEntityError(errorMessage(results[2].reason))
    }).finally(() => {
      if (!controller.signal.aborted) setReferenceLoading(false)
    })
    return () => controller.abort()
  }, [user, canManage, referenceAttempt])

  const reloadReference = useCallback(() => setReferenceAttempt((value) => value + 1), [])

  function handleTabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    let next = index
    if (event.key === 'ArrowLeft') next = (index - 1 + availableTabs.length) % availableTabs.length
    if (event.key === 'ArrowRight') next = (index + 1) % availableTabs.length
    if (event.key === 'Home') next = 0
    if (event.key === 'End') next = availableTabs.length - 1
    const item = availableTabs[next]
    setTab(item.key)
    document.getElementById(`knowledge-tab-${item.key}`)?.focus()
  }

  return <section className="manager-page" aria-labelledby="manager-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="manager-heading">知识管理</h1>
        <p>维护廉洁文化知识、概念与标签。所有写入由服务器校验，页面不执行用户脚本。</p>
      </div>
    </header>

    {loading
      ? <LoadingLines label="正在恢复登录" />
      : !user
        ? <EmptyState
          title="登录后管理知识"
          action={<button type="button" className="button button--primary" onClick={() => openLogin('登录具有知识维护权限的账号后，即可进入知识管理。')}>登录</button>}
        >
          知识、概念和标签的写入需要服务器授予的 `knowledge.write`、`ontology.write` 或 `tags.write` 权限。
        </EmptyState>
        : !canManage
          ? <EmptyState
            title="无权访问"
            action={<Link href="/" className="button button--secondary">返回创作台</Link>}
          >
            当前账号没有知识维护权限。普通创作者仍可在创作台使用已发布模板。
          </EmptyState>
          : <>
            <div className="manager-tabs" role="tablist" aria-label="知识管理分类">
              {availableTabs.map((item, index) => <button
                type="button"
                role="tab"
                id={`knowledge-tab-${item.key}`}
                key={item.key}
                className={`manager-tab${activeTab === item.key ? ' is-active' : ''}`}
                aria-selected={activeTab === item.key}
                aria-controls={`knowledge-panel-${item.key}`}
                tabIndex={activeTab === item.key ? 0 : -1}
                onClick={() => setTab(item.key)}
                onKeyDown={(event) => handleTabKey(event, index)}
              >{item.label}</button>)}
            </div>

            {referenceError && <InlineNotice tone="error" title="参考数据加载失败">
              概念、标签或关系目标未能全部载入，表单中的部分选项可能为空。
              <details className="notice__details">
                <summary>查看技术详情</summary>
                <p className="notice__detail">{referenceError}</p>
              </details>
              <div className="notice__actions">
                <button type="button" className="button button--secondary button--small" disabled={referenceLoading} onClick={reloadReference}>
                  {referenceLoading ? '重新加载中…' : '重新加载参考数据'}
                </button>
              </div>
            </InlineNotice>}
            {referenceLoading && <p className="manager-status" role="status">正在载入概念、标签与实体选项…</p>}

            {canWriteKnowledge && <div role="tabpanel" id="knowledge-panel-knowledge" aria-labelledby="knowledge-tab-knowledge" hidden={activeTab !== 'knowledge'}>
              <KnowledgePanel concepts={concepts} tags={tags} entities={entities} onChanged={reloadReference} />
            </div>}
            {canWriteOntology && <div role="tabpanel" id="knowledge-panel-concepts" aria-labelledby="knowledge-tab-concepts" hidden={activeTab !== 'concepts'}>
              <ConceptPanel concepts={concepts} loading={referenceLoading} loadError={conceptError} onChanged={reloadReference} />
            </div>}
            {canWriteTags && <div role="tabpanel" id="knowledge-panel-tags" aria-labelledby="knowledge-tab-tags" hidden={activeTab !== 'tags'}>
              <TagPanel tags={tags} loading={referenceLoading} loadError={tagError} onChanged={reloadReference} />
            </div>}
          </>}
  </section>
}
