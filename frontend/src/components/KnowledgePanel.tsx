'use client'

import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { errorMessage, request } from '@/api/client'
import type {
  Concept, EntityStatus, KnowledgeEntity, KnowledgeInput, KnowledgeRelation, KnowledgeType, Page, RelationPredicate, SourceReference, Tag,
} from '@/types/core'
import {
  entityStatusLabel, formatDateTime, KNOWLEDGE_TYPE_OPTIONS, knowledgeTypeLabel,
  RELATION_PREDICATE_OPTIONS, relationPredicateLabel,
} from '@/utils/format'
import { flattenTree, treeLabel } from '@/utils/tree'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'

const PAGE_SIZE = 20
let rowSequence = 0
const nextKey = () => `row-${rowSequence++}`

interface SourceRow { key: string; title: string; url: string; note: string }
interface RelationRow { key: string; predicate: RelationPredicate; targetEntityId: string }
interface Draft {
  id: string | null
  revision: number
  type: KnowledgeType
  name: string
  aliasesText: string
  summary: string
  content: string
  sources: SourceRow[]
  conceptIds: string[]
  tagIds: string[]
  relations: RelationRow[]
  timeTheme: string
  status: EntityStatus
}

function blankDraft(): Draft {
  return {
    id: null, revision: 1, type: 'story', name: '', aliasesText: '', summary: '', content: '',
    sources: [], conceptIds: [], tagIds: [], relations: [], timeTheme: '', status: 'active',
  }
}

function draftFromEntity(item: KnowledgeEntity): Draft {
  return {
    id: item.id,
    revision: item.revision,
    type: item.type,
    name: item.name,
    aliasesText: item.aliases.join('\n'),
    summary: item.summary,
    content: item.content,
    sources: item.sourceReferences.map((source) => ({
      key: nextKey(), title: source.title, url: source.url ?? '', note: source.note,
    })),
    conceptIds: [...item.conceptIds],
    tagIds: [...item.tagIds],
    relations: item.relations.map((relation) => ({
      key: nextKey(), predicate: relation.predicate, targetEntityId: relation.targetEntityId,
    })),
    timeTheme: item.timeTheme ?? '',
    status: item.status,
  }
}

function toggleId(list: string[], id: string) {
  return list.includes(id) ? list.filter((value) => value !== id) : [...list, id]
}

export default function KnowledgePanel({ concepts, tags, entities, onChanged }: {
  concepts: Concept[]
  tags: Tag[]
  entities: KnowledgeEntity[]
  onChanged: () => void
}) {
  const [items, setItems] = useState<KnowledgeEntity[] | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [searchInput, setSearchInput] = useState('')
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState<'' | KnowledgeType>('')
  const [loading, setLoading] = useState(false)
  const [listError, setListError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')

  const conceptNodes = useMemo(() => flattenTree(concepts), [concepts])
  const tagNodes = useMemo(() => flattenTree(tags), [tags])
  const tagNames = useMemo(() => new Map(tags.map((tag) => [tag.id, tag.name])), [tags])
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setListError('')
    setItems(null)
    const params = new URLSearchParams({ scope: 'manage', page: String(page), pageSize: String(PAGE_SIZE) })
    if (query) params.set('q', query)
    if (typeFilter) params.set('type', typeFilter)
    request<Page<KnowledgeEntity>>(`/knowledge?${params.toString()}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return
        setItems(data.items)
        setTotal(data.total)
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setListError(errorMessage(cause))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [page, query, typeFilter, attempt])

  function update(patch: Partial<Draft>) {
    setDraft((old) => old ? { ...old, ...patch } : old)
  }

  function startNew() {
    setDraft(blankDraft())
    setFormError('')
    setNotice('')
  }

  function startEdit(item: KnowledgeEntity) {
    setDraft(draftFromEntity(item))
    setFormError('')
    setNotice('')
  }

  function updateSource(key: string, patch: Partial<SourceRow>) {
    setDraft((old) => old ? { ...old, sources: old.sources.map((source) => source.key === key ? { ...source, ...patch } : source) } : old)
  }

  function updateRelation(key: string, patch: Partial<RelationRow>) {
    setDraft((old) => old ? { ...old, relations: old.relations.map((relation) => relation.key === key ? { ...relation, ...patch } : relation) } : old)
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!draft || saving) return
    const name = draft.name.trim()
    if (!name) {
      setFormError('知识名称不能为空。')
      return
    }
    const aliases = draft.aliasesText.split(/[\n,]/).map((value) => value.trim()).filter(Boolean)
    if (aliases.length > 20) {
      setFormError('别名最多 20 条。')
      return
    }
    const sources: SourceReference[] = []
    for (const row of draft.sources) {
      const title = row.title.trim()
      const url = row.url.trim()
      const note = row.note.trim()
      if (!title && !url && !note) continue
      if (!title) {
        setFormError('来源引用需要填写标题。')
        return
      }
      if (url && !/^https?:\/\//i.test(url)) {
        setFormError(`来源“${title}”的 URL 必须以 http:// 或 https:// 开头。`)
        return
      }
      sources.push({ title, url: url || null, note })
    }
    const relations: KnowledgeRelation[] = []
    for (const row of draft.relations) {
      const target = row.targetEntityId.trim()
      if (!target) continue
      if (draft.id && target === draft.id) {
        setFormError('知识关系不能指向当前知识自身。')
        return
      }
      relations.push({ predicate: row.predicate, targetEntityId: target })
    }
    const payload: KnowledgeInput = {
      type: draft.type,
      name,
      aliases,
      summary: draft.summary,
      content: draft.content,
      sourceReferences: sources,
      conceptIds: draft.conceptIds,
      tagIds: draft.tagIds,
      relations,
      timeTheme: draft.timeTheme.trim() || null,
      status: draft.status,
    }
    setSaving(true)
    setFormError('')
    setNotice('')
    try {
      if (draft.id) {
        await request<KnowledgeEntity>(`/knowledge/${encodeURIComponent(draft.id)}`, {
          method: 'PATCH',
          body: JSON.stringify({ expectedRevision: draft.revision, ...payload }),
        })
        setNotice('知识已保存。')
      } else {
        await request<KnowledgeEntity>('/knowledge', { method: 'POST', body: JSON.stringify(payload) })
        setNotice('知识已新增。')
      }
      setDraft(null)
      setAttempt((value) => value + 1)
      onChanged()
    } catch (cause) {
      setFormError(errorMessage(cause))
    } finally {
      setSaving(false)
    }
  }

  async function deactivate() {
    if (!draft?.id || deleting) return
    if (!window.confirm('停用后保留历史，确认停用这条知识？')) return
    setDeleting(true)
    setFormError('')
    try {
      await request<void>(`/knowledge/${encodeURIComponent(draft.id)}?expectedRevision=${draft.revision}`, { method: 'DELETE' })
      setNotice('知识已停用。')
      setDraft(null)
      setAttempt((value) => value + 1)
      onChanged()
    } catch (cause) {
      setFormError(errorMessage(cause))
    } finally {
      setDeleting(false)
    }
  }

  return <section className="knowledge-panel" aria-labelledby="knowledge-list-heading">
    {notice && <InlineNotice tone="success">{notice}</InlineNotice>}
    <div className="manager-toolbar">
      <form className="manager-search" onSubmit={(event) => {
        event.preventDefault()
        setQuery(searchInput.trim())
        setPage(1)
      }}>
        <label className="field">
          <span>检索知识</span>
          <input value={searchInput} placeholder="名称、摘要或正文" maxLength={120} onChange={(event) => setSearchInput(event.target.value)} />
        </label>
        <button type="submit" className="button button--secondary button--small">搜索</button>
      </form>
      <label className="field manager-filter">
        <span>类型</span>
        <select value={typeFilter} onChange={(event) => {
          setTypeFilter(event.target.value as '' | KnowledgeType)
          setPage(1)
        }}>
          <option value="">全部类型</option>
          {KNOWLEDGE_TYPE_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
        </select>
      </label>
      <div className="manager-toolbar__actions">
        <button type="button" className="button button--primary button--small" onClick={startNew}>新增知识</button>
        <button type="button" className="button button--secondary button--small" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>刷新</button>
      </div>
    </div>

    <div className="knowledge-layout">
      <div className="knowledge-list-panel">
        <div className="panel-head">
          <h2 id="knowledge-list-heading">知识条目</h2>
          <span className="mono">共 {total} 条 · 第 {page} / {totalPages} 页</span>
        </div>
        {listError
          ? <div className="works-error">
            <InlineNotice tone="error" title="知识列表加载失败">
              无法读取知识列表。后端可能尚未实现 `/knowledge` 接口。
              <details className="notice__details">
                <summary>查看技术详情</summary>
                <p className="notice__detail">{listError}</p>
              </details>
            </InlineNotice>
            <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
          </div>
          : loading && !items
            ? <LoadingLines label="正在加载知识列表" />
            : items && items.length === 0
              ? <EmptyState title="没有匹配的知识" action={<button type="button" className="button button--secondary button--small" onClick={startNew}>新增第一条知识</button>}>
                调整搜索词或类型筛选，或创建新的文化知识条目。
              </EmptyState>
              : <ul className="knowledge-list">
                {items?.map((item) => <li className={`knowledge-row${draft?.id === item.id ? ' is-selected' : ''}`} key={item.id}>
                  <div className="knowledge-row__body">
                    <div className="knowledge-row__head">
                      <span className="mono">{knowledgeTypeLabel(item.type)}</span>
                      <span className={`status-chip is-${item.status}`}>{entityStatusLabel(item.status)}</span>
                      <span className="mono">rev {item.revision}</span>
                    </div>
                    <h3>{item.name}</h3>
                    {item.summary && <p>{item.summary}</p>}
                    <div className="knowledge-row__tags">
                      <span className="tag-line"><span className="tag-label">直接</span>{item.tagIds.length
                        ? item.tagIds.map((id) => <span className="tag-chip" key={id}>{tagNames.get(id) ?? id}</span>)
                        : <span className="muted-text">—</span>}</span>
                      <span className="tag-line"><span className="tag-label">继承</span>{item.inheritedTagIds.length
                        ? item.inheritedTagIds.map((id) => <span className="tag-chip" key={id}>{tagNames.get(id) ?? id}</span>)
                        : <span className="muted-text">—</span>}</span>
                    </div>
                  </div>
                  <div className="knowledge-row__side">
                    <span className="mono">{formatDateTime(item.updatedAt)}</span>
                    <button type="button" className="button button--secondary button--small" onClick={() => startEdit(item)}>编辑</button>
                  </div>
                </li>)}
              </ul>}
            {!listError && items && items.length > 0 && <nav className="pager" aria-label="知识分页">
              <button type="button" className="button button--secondary button--small" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
              <span className="mono">第 {page} / {totalPages} 页</span>
              <button type="button" className="button button--secondary button--small" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
            </nav>}
      </div>

      <aside className="knowledge-editor" aria-labelledby="knowledge-editor-heading">
            {!draft
              ? <div className="editor-empty">
                <h2 id="knowledge-editor-heading">编辑区</h2>
                <p>选择一条知识进行编辑，或点击“新增知识”创建条目。</p>
                <p className="field-note">写入会由服务器校验名称、来源 URL、标签冲突、关系与 revision。</p>
              </div>
              : <form onSubmit={(event) => void save(event)}>
                <div className="editor-head">
                  <div>
                    <h2 id="knowledge-editor-heading">{draft.id ? '编辑知识' : '新增知识'}</h2>
                    {draft.id && <p className="mono editor-id">{draft.id} · revision {draft.revision}</p>}
                  </div>
                  <button type="button" className="button button--link button--small" disabled={saving || deleting} onClick={() => { setDraft(null); setFormError('') }}>关闭编辑</button>
                </div>
                <fieldset className="editor-fields" disabled={saving || deleting}>
                  <div className="editor-grid">
                    <label className="field">
                      <span>类型</span>
                      <select value={draft.type} onChange={(event) => update({ type: event.target.value as KnowledgeType })}>
                        {KNOWLEDGE_TYPE_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
                      </select>
                    </label>
                    <label className="field">
                      <span>状态</span>
                      <select value={draft.status} onChange={(event) => update({ status: event.target.value as EntityStatus })}>
                        <option value="active">启用</option>
                        <option value="inactive">停用</option>
                      </select>
                    </label>
                  </div>
                  <label className="field">
                    <span>名称</span>
                    <input required maxLength={80} value={draft.name} onChange={(event) => update({ name: event.target.value })} />
                  </label>
                  <label className="field">
                    <span>别名（每行一个，最多 20 条）</span>
                    <textarea rows={3} value={draft.aliasesText} onChange={(event) => update({ aliasesText: event.target.value })} />
                  </label>
                  <label className="field">
                    <span>摘要（最多 500 字）</span>
                    <textarea rows={3} maxLength={500} value={draft.summary} onChange={(event) => update({ summary: event.target.value })} />
                  </label>
                  <label className="field">
                    <span>正文（最多 20000 字）</span>
                    <textarea rows={8} maxLength={20000} value={draft.content} onChange={(event) => update({ content: event.target.value })} />
                  </label>
                  <label className="field">
                    <span>时间主题（可选，最多 40 字）</span>
                    <input maxLength={40} value={draft.timeTheme} onChange={(event) => update({ timeTheme: event.target.value })} />
                  </label>

                  <fieldset className="editor-fieldset">
                    <legend>来源引用</legend>
                    <div className="repeat-list">
                      {draft.sources.map((source) => <div className="source-row" key={source.key}>
                        <input aria-label="来源标题" placeholder="标题" maxLength={200} value={source.title} onChange={(event) => updateSource(source.key, { title: event.target.value })} />
                        <input aria-label="来源 URL" placeholder="https://…" maxLength={2000} value={source.url} onChange={(event) => updateSource(source.key, { url: event.target.value })} />
                        <input aria-label="来源备注" placeholder="备注（可选）" maxLength={500} value={source.note} onChange={(event) => updateSource(source.key, { note: event.target.value })} />
                        <button type="button" className="button button--link button--small" onClick={() => update({ sources: draft.sources.filter((item) => item.key !== source.key) })}>移除</button>
                      </div>)}
                    </div>
                    <button type="button" className="button button--secondary button--small" onClick={() => update({ sources: [...draft.sources, { key: nextKey(), title: '', url: '', note: '' }] })}>添加来源</button>
                    <p className="field-note">标题必填；URL 仅接受 http/https；备注可选。</p>
                  </fieldset>

                  <fieldset className="editor-fieldset">
                    <legend>关联概念</legend>
                    <div className="checklist">
                      {conceptNodes.map(({ item, depth, path }) => <label className="checklist-item" key={item.id} style={{ paddingLeft: `${depth * 14}px` }}>
                        <input type="checkbox" checked={draft.conceptIds.includes(item.id)} onChange={() => update({ conceptIds: toggleId(draft.conceptIds, item.id) })} />
                        <span>{treeLabel(path)}</span>
                        {item.status === 'inactive' && <small>停用</small>}
                      </label>)}
                    </div>
                    {conceptNodes.length === 0 && <p className="field-note">概念列表尚未载入或为空。</p>}
                  </fieldset>

                  <fieldset className="editor-fieldset">
                    <legend>关联标签</legend>
                    <div className="checklist">
                      {tagNodes.map(({ item, depth, path }) => <label className="checklist-item" key={item.id} style={{ paddingLeft: `${depth * 14}px` }}>
                        <input type="checkbox" checked={draft.tagIds.includes(item.id)} onChange={() => update({ tagIds: toggleId(draft.tagIds, item.id) })} />
                        <span>{treeLabel(path)}</span>
                        {item.status === 'inactive' && <small>停用</small>}
                      </label>)}
                    </div>
                    {tagNodes.length === 0 && <p className="field-note">标签列表尚未载入或为空。</p>}
                  </fieldset>

                  <fieldset className="editor-fieldset">
                    <legend>知识关系</legend>
                    <div className="repeat-list">
                      {draft.relations.map((relation) => <div className="relation-row" key={relation.key}>
                        <select aria-label="关系类型" value={relation.predicate} onChange={(event) => updateRelation(relation.key, { predicate: event.target.value as RelationPredicate })}>
                          {RELATION_PREDICATE_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}（{relationPredicateLabel(option.value)}）</option>)}
                        </select>
                        <input aria-label="目标实体 ID" list="knowledge-entity-options" placeholder="目标实体 ID" maxLength={80} value={relation.targetEntityId} onChange={(event) => updateRelation(relation.key, { targetEntityId: event.target.value })} />
                        <button type="button" className="button button--link button--small" onClick={() => update({ relations: draft.relations.filter((item) => item.key !== relation.key) })}>移除</button>
                      </div>)}
                    </div>
                    <datalist id="knowledge-entity-options">
                      {entities.map((entity) => <option value={entity.id} key={entity.id}>{entity.name} · {knowledgeTypeLabel(entity.type)}</option>)}
                    </datalist>
                    <button type="button" className="button button--secondary button--small" onClick={() => update({ relations: [...draft.relations, { key: nextKey(), predicate: 'associated_with', targetEntityId: '' }] })}>添加关系</button>
                    <p className="field-note">目标实体可从最近 100 条知识中选择，也可直接输入 ID；不能指向当前条目自身。</p>
                  </fieldset>
                </fieldset>

                {formError && <InlineNotice tone="error" title="保存失败">{formError}</InlineNotice>}
                <div className="form-actions">
                  <button type="submit" className="button button--primary" disabled={saving || deleting}>{saving ? '保存中…' : draft.id ? '保存修改' : '创建知识'}</button>
                  <button type="button" className="button button--secondary button--small" disabled={saving || deleting} onClick={() => { setDraft(null); setFormError('') }}>取消</button>
                  {draft.id && <button type="button" className="button button--secondary button--small" disabled={saving || deleting} onClick={() => void deactivate()}>{deleting ? '停用中…' : '停用'}</button>}
                </div>
              </form>}
      </aside>
    </div>
  </section>
}
