'use client'

import { useMemo, useState, type FormEvent } from 'react'
import { errorMessage, request } from '@/api/client'
import type { EntityStatus, Tag, TagInput } from '@/types/core'
import { entityStatusLabel, formatDateTime } from '@/utils/format'
import { descendantIds, flattenTree, treeLabel } from '@/utils/tree'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'

interface Draft {
  id: string | null
  revision: number
  name: string
  description: string
  parentId: string
  status: EntityStatus
  conflictsWith: string[]
}

function blankDraft(): Draft {
  return { id: null, revision: 1, name: '', description: '', parentId: '', status: 'active', conflictsWith: [] }
}

function draftFromTag(item: Tag): Draft {
  return {
    id: item.id,
    revision: item.revision,
    name: item.name,
    description: item.description,
    parentId: item.parentId ?? '',
    status: item.status,
    conflictsWith: [...item.conflictsWith],
  }
}

function toggleId(list: string[], id: string) {
  return list.includes(id) ? list.filter((value) => value !== id) : [...list, id]
}

export default function TagPanel({ tags, loading, loadError, onChanged }: {
  tags: Tag[]
  loading: boolean
  loadError: string
  onChanged: () => void
}) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')

  const nodes = useMemo(() => flattenTree(tags), [tags])
  const tagNames = useMemo(() => new Map(tags.map((tag) => [tag.id, tag.name])), [tags])
  const excluded = draft?.id ? descendantIds(tags, draft.id) : new Set<string>()
  const parentOptions = nodes.filter(({ item }) => !excluded.has(item.id))
  const conflictOptions = nodes.filter(({ item }) => item.id !== draft?.id)

  function update(patch: Partial<Draft>) {
    setDraft((old) => old ? { ...old, ...patch } : old)
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!draft || saving) return
    const name = draft.name.trim()
    if (!name) {
      setFormError('标签名称不能为空。')
      return
    }
    const payload: TagInput = {
      name,
      description: draft.description,
      parentId: draft.parentId || null,
      status: draft.status,
      conflictsWith: draft.conflictsWith,
    }
    setSaving(true)
    setFormError('')
    setNotice('')
    try {
      if (draft.id) {
        await request<Tag>(`/tags/${encodeURIComponent(draft.id)}`, {
          method: 'PATCH',
          body: JSON.stringify({ expectedRevision: draft.revision, ...payload }),
        })
        setNotice('标签已保存。')
      } else {
        await request<Tag>('/tags', { method: 'POST', body: JSON.stringify(payload) })
        setNotice('标签已创建。')
      }
      setDraft(null)
      onChanged()
    } catch (cause) {
      setFormError(errorMessage(cause))
    } finally {
      setSaving(false)
    }
  }

  async function deactivate() {
    if (!draft?.id || deleting) return
    if (!window.confirm('停用后保留历史，确认停用这个标签？')) return
    setDeleting(true)
    setFormError('')
    try {
      await request<void>(`/tags/${encodeURIComponent(draft.id)}?expectedRevision=${draft.revision}`, { method: 'DELETE' })
      setNotice('标签已停用。')
      setDraft(null)
      onChanged()
    } catch (cause) {
      setFormError(errorMessage(cause))
    } finally {
      setDeleting(false)
    }
  }

  return <section className="taxonomy-panel" aria-labelledby="tag-list-heading">
    {notice && <InlineNotice tone="success">{notice}</InlineNotice>}
    <div className="manager-toolbar">
      <div className="manager-toolbar__actions">
        <button type="button" className="button button--primary button--small" onClick={() => { setDraft(blankDraft()); setFormError(''); setNotice('') }}>新增标签</button>
        <button type="button" className="button button--secondary button--small" onClick={onChanged}>刷新列表</button>
      </div>
    </div>

    <div className="taxonomy-layout">
      <div className="taxonomy-list-panel">
        <div className="panel-head">
          <h2 id="tag-list-heading">标签树</h2>
          <span className="mono">{tags.length} 个</span>
        </div>
        {loading && tags.length === 0
          ? <LoadingLines label="正在加载标签" />
          : loadError && tags.length === 0
            ? <InlineNotice tone="error" title="标签列表加载失败">
              请先重新加载参考数据。
              <div className="notice__actions">
                <button type="button" className="button button--secondary button--small" onClick={onChanged}>重新加载</button>
              </div>
            </InlineNotice>
            : nodes.length === 0
              ? <EmptyState title="还没有标签" action={<button type="button" className="button button--secondary button--small" onClick={() => setDraft(blankDraft())}>创建第一个标签</button>}>
                标签支持单父树、继承和互斥；互斥对由服务端对称维护。
              </EmptyState>
              : <ul className="tree-list">
            {nodes.map(({ item, depth, path }) => <li className={`tree-row${draft?.id === item.id ? ' is-selected' : ''}`} key={item.id}>
              <div className="tree-row__main" style={{ paddingLeft: `${depth * 18}px` }}>
                <div>
                  <strong>{item.name}</strong>
                  {item.description && <p>{item.description}</p>}
                  <div className="tag-line">
                    <span className="tag-label">继承</span>
                    {item.inheritedTagIds.length
                      ? item.inheritedTagIds.map((id) => <span className="tag-chip" key={id}>{tagNames.get(id) ?? id}</span>)
                      : <span className="muted-text">—</span>}
                  </div>
                  <div className="tag-line">
                    <span className="tag-label">冲突</span>
                    {item.conflictsWith.length
                      ? item.conflictsWith.map((id) => <span className="tag-chip" key={id}>{tagNames.get(id) ?? id}</span>)
                      : <span className="muted-text">—</span>}
                  </div>
                  {depth > 0 && <span className="tree-path mono">{treeLabel(path)}</span>}
                </div>
              </div>
              <div className="tree-row__meta">
                <span className={`status-chip is-${item.status}`}>{entityStatusLabel(item.status)}</span>
                <span className="mono">rev {item.revision}</span>
                <span className="mono">{formatDateTime(item.updatedAt)}</span>
                <button type="button" className="button button--secondary button--small" onClick={() => { setDraft(draftFromTag(item)); setFormError(''); setNotice('') }}>编辑</button>
              </div>
            </li>)}
          </ul>}
      </div>

      <aside className="taxonomy-editor" aria-labelledby="tag-editor-heading">
        {!draft
          ? <div className="editor-empty">
            <h2 id="tag-editor-heading">编辑区</h2>
            <p>选择树中的一个标签进行编辑，或点击“新增标签”。</p>
            <p className="field-note">继承标签由父标签自动计算；冲突对由服务端对称维护。</p>
          </div>
          : <form onSubmit={(event) => void save(event)}>
            <div className="editor-head">
              <div>
                <h2 id="tag-editor-heading">{draft.id ? '编辑标签' : '新增标签'}</h2>
                {draft.id && <p className="mono editor-id">{draft.id} · revision {draft.revision}</p>}
              </div>
              <button type="button" className="button button--link button--small" disabled={saving || deleting} onClick={() => { setDraft(null); setFormError('') }}>关闭编辑</button>
            </div>
            <fieldset className="editor-fields" disabled={saving || deleting}>
              <label className="field">
                <span>名称</span>
                <input required maxLength={80} value={draft.name} onChange={(event) => update({ name: event.target.value })} />
              </label>
              <label className="field">
                <span>说明（最多 500 字）</span>
                <textarea rows={4} maxLength={500} value={draft.description} onChange={(event) => update({ description: event.target.value })} />
              </label>
              <label className="field">
                <span>父节点</span>
                <select value={draft.parentId} onChange={(event) => update({ parentId: event.target.value })}>
                  <option value="">无父节点（根）</option>
                  {parentOptions.map(({ item, path }) => <option value={item.id} key={item.id}>{treeLabel(path)}</option>)}
                </select>
              </label>
              <label className="field">
                <span>状态</span>
                <select value={draft.status} onChange={(event) => update({ status: event.target.value as EntityStatus })}>
                  <option value="active">启用</option>
                  <option value="inactive">停用</option>
                </select>
              </label>
              <fieldset className="editor-fieldset">
                <legend>互斥标签</legend>
                <div className="checklist">
                  {conflictOptions.map(({ item, depth, path }) => <label className="checklist-item" key={item.id} style={{ paddingLeft: `${depth * 14}px` }}>
                    <input type="checkbox" checked={draft.conflictsWith.includes(item.id)} onChange={() => update({ conflictsWith: toggleId(draft.conflictsWith, item.id) })} />
                    <span>{treeLabel(path)}</span>
                  </label>)}
                </div>
                {conflictOptions.length === 0 && <p className="field-note">
                  {loading ? '正在加载标签列表…' : loadError ? '标签列表加载失败，暂时无法选择互斥项。' : '还没有其他标签可作为互斥项。'}
                </p>}
                <p className="field-note">互斥关系由服务端对称维护；知识引用冲突标签时会被拒绝。</p>
              </fieldset>
            </fieldset>
            {formError && <InlineNotice tone="error" title="保存失败">{formError}</InlineNotice>}
            <div className="form-actions">
              <button type="submit" className="button button--primary" disabled={saving || deleting}>{saving ? '保存中…' : draft.id ? '保存修改' : '创建标签'}</button>
              <button type="button" className="button button--secondary button--small" disabled={saving || deleting} onClick={() => { setDraft(null); setFormError('') }}>取消</button>
              {draft.id && <button type="button" className="button button--secondary button--small" disabled={saving || deleting} onClick={() => void deactivate()}>{deleting ? '停用中…' : '停用'}</button>}
            </div>
          </form>}
      </aside>
    </div>
  </section>
}
