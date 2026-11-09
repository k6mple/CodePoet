import { useEffect, useRef, useState, type FormEvent } from 'react'
import { errorMessage, request } from '@/api/client'
import type { MappingRule, MappingRuleInput, Page } from '@/types/core'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'

const emptyRule: MappingRuleInput = {
  name: '', enabled: true, priority: 0, explanation: '',
  match: { keywords: [], entityIds: [], conceptIds: [], tagIds: [], operator: 'any' },
  effects: { symbolIds: [], palette: ['#233F32'], composition: 'centered', motion: 'none', caption: '' },
}

export default function MappingRules() {
  const [page, setPage] = useState(1)
  const [list, setList] = useState<Page<MappingRule> | null>(null)
  const [selected, setSelected] = useState<MappingRule | null>(null)
  const [editing, setEditing] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [listError, setListError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [notice, setNotice] = useState('')
  const [saving, setSaving] = useState(false)
  const lock = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    setList(null)
    setListError('')
    request<Page<MappingRule>>(`/rules?page=${page}&pageSize=20`, { signal })
      .then((data) => { if (!signal.aborted) setList(data) })
      .catch((cause) => { if (!signal.aborted) setListError(errorMessage(cause)) })
    return () => controller.abort()
  }, [page, attempt])

  function edit(rule: MappingRule | null) {
    setSelected(rule)
    setSaveError('')
    setNotice('')
    setEditing(true)
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (lock.current) return
    const form = new FormData(event.currentTarget)
    const read = (key: string) => String(form.get(key) ?? '')
    setSaveError('')
    setNotice('')
    lock.current = true
    setSaving(true)
    try {
      const match = JSON.parse(read('match'))
      const effects = JSON.parse(read('effects'))
      if (!match || typeof match !== 'object' || Array.isArray(match)
        || !effects || typeof effects !== 'object' || Array.isArray(effects)) {
        throw new Error('匹配条件和视觉效果必须是 JSON 对象。')
      }
      const name = read('name').trim()
      if (!name) throw new Error('规则名称不能为空。')
      const body = {
        name, priority: Number(read('priority')), enabled: form.has('enabled'),
        match, effects, explanation: read('explanation').trim(),
      }
      const saved = await request<MappingRule>(selected ? `/rules/${encodeURIComponent(selected.id)}` : '/rules', {
        method: selected ? 'PATCH' : 'POST',
        body: JSON.stringify(selected ? { ...body, expectedRevision: selected.revision } : body),
      })
      setSelected(saved)
      setNotice(`规则已保存为第 ${saved.version} 版。`)
      setAttempt((value) => value + 1)
    } catch (cause) {
      setSaveError(errorMessage(cause))
    } finally {
      lock.current = false
      setSaving(false)
    }
  }

  const rule = selected ?? emptyRule
  return <section className="editor-panel" aria-labelledby="mapping-rules-heading">
    <div className="panel-head">
      <h2 id="mapping-rules-heading">语义映射规则</h2>
      <button className="button button--secondary button--small" type="button" disabled={saving || (editing && !selected)}
        onClick={() => edit(null)}>新建规则</button>
    </div>
    <p className="panel-note">规则按优先级匹配知识或关键词。保存新版本后，历史作品继续使用自己的规则快照。</p>
    {listError ? <InlineNotice tone="error" title="规则列表加载失败">
      {listError}
      <button type="button" className="button button--secondary button--small"
        onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
    </InlineNotice> : !list ? <LoadingLines label="正在读取规则" />
      : list.items.length === 0 ? <EmptyState title="当前没有规则" />
        : <ul className="manager-list">
          {list.items.map((item) => <li className="manager-row" key={item.id}>
            <button type="button" className="space-session-main" onClick={() => edit(item)} disabled={saving}>
              <strong>{item.name}</strong>
              <span>{item.enabled ? '启用' : '停用'} · 优先级 {item.priority} · 第 {item.version} 版</span>
            </button>
          </li>)}
        </ul>}
    {list && list.total > list.pageSize && <nav className="pager" aria-label="规则分页">
      <button type="button" className="button button--secondary button--small" disabled={page <= 1}
        onClick={() => setPage(page - 1)}>上一页</button>
      <button type="button" className="button button--secondary button--small" disabled={page * list.pageSize >= list.total}
        onClick={() => setPage(page + 1)}>下一页</button>
    </nav>}
    {editing && <form key={selected ? `${selected.id}:${selected.revision}` : 'new'} onSubmit={(event) => void save(event)}>
      <fieldset disabled={saving}>
        <legend>{selected ? `编辑规则 · rev ${selected.revision}` : '新建规则'}</legend>
        <label className="field">名称 <input name="name" required maxLength={80} defaultValue={rule.name} /></label>
        <label className="field">优先级 <input name="priority" type="number" required min={0} max={1000} step={1} defaultValue={rule.priority} /></label>
        <label className="check"><input type="checkbox" name="enabled" defaultChecked={rule.enabled} /> 启用</label>
        <label className="field">匹配条件 JSON
          <textarea name="match" rows={6} className="mono p1-json" spellCheck={false} defaultValue={JSON.stringify(rule.match, null, 2)} />
        </label>
        <label className="field">视觉效果 JSON
          <textarea name="effects" rows={6} className="mono p1-json" spellCheck={false} defaultValue={JSON.stringify(rule.effects, null, 2)} />
        </label>
        <label className="field">规则说明 <textarea name="explanation" maxLength={500} defaultValue={rule.explanation} /></label>
        <div className="form-actions">
          <button type="submit" className="button button--primary">{saving ? '保存中…' : '保存规则'}</button>
          <button type="button" className="button button--secondary" onClick={() => setEditing(false)}>关闭编辑</button>
        </div>
      </fieldset>
      {notice && <InlineNotice tone="success">{notice}</InlineNotice>}
      {saveError && <InlineNotice tone="error" title="规则未保存">{saveError}</InlineNotice>}
    </form>}
  </section>
}
