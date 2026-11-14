'use client'

import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { errorMessage, request } from '@/api/client'
import type { DrawingIR, Page, Parameter, Template, TemplateVersion, VersionCreateInput } from '@/types/core'
import { formatDateTime, parameterTypeLabel, parameterValueLabel, symbolLabel } from '@/utils/format'
import {
  diffTemplateVersions, draftToMetadata, isEditableVersion, metadataToDraft, prettyJson,
  STARTER_BINDINGS, STARTER_IR, STARTER_PARAMETERS, validateParameterSchema, versionStatusLabel,
  type MetadataDraft,
} from '@/utils/template'
import CanvasPreview from './CanvasPreview'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'
import TemplateMetadataForm from './TemplateMetadataForm'
import { useCapabilities } from '@/hooks/useCapabilities'
import { hasBrowserRenderer } from '@/renderers/registry'
import { backendLabel, preferredBrowserBackend } from '@/renderers/backend'

const PAGE_SIZE = 20
const canManageTemplates = (permissions: string[]) => permissions.some((permission) =>
  permission === 'templates.create' || permission === 'templates.editOwn' || permission === 'templates.editAny')

function blankMetadata(): MetadataDraft {
  return { name: '', description: '', symbols: [], timeTheme: '', entityIdsText: '', tagIdsText: '' }
}

function displayMetadata(template: Template) {
  return template.workingMetadata ?? template.metadata
}

function MetadataSummary({ metadata }: { metadata: ReturnType<typeof displayMetadata> }) {
  return <div className="tag-line">
    <span className="tag-label">符号</span>
    {metadata.symbols?.length
      ? metadata.symbols.map((symbol) => <span className="tag-chip" key={symbol}>{symbolLabel(symbol)}</span>)
      : <span className="muted-text">—</span>}
    {metadata.timeTheme && <span className="tag-chip">{metadata.timeTheme}</span>}
  </div>
}

export function TemplateManager() {
  const { user, loading, openLogin } = useSession()
  const router = useRouter()
  const [templates, setTemplates] = useState<Template[] | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [searchInput, setSearchInput] = useState('')
  const [query, setQuery] = useState('')
  const [fetching, setFetching] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [creating, setCreating] = useState(false)
  const [createDraft, setCreateDraft] = useState<MetadataDraft>(blankMetadata())
  const [createBusy, setCreateBusy] = useState(false)
  const [createError, setCreateError] = useState('')

  const canManage = Boolean(user && canManageTemplates(user.permissions))

  useEffect(() => {
    if (!user || !canManage) return
    const controller = new AbortController()
    setFetching(true)
    setError('')
    setTemplates(null)
    const params = new URLSearchParams({ scope: 'mine', page: String(page), pageSize: String(PAGE_SIZE) })
    if (query) params.set('q', query)
    request<Page<Template>>(`/templates?${params.toString()}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return
        setTemplates(data.items)
        setTotal(data.total)
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause))
      })
      .finally(() => {
        if (!controller.signal.aborted) setFetching(false)
      })
    return () => controller.abort()
  }, [user, canManage, page, query, attempt])

  async function createTemplate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (createBusy) return
    if (!createDraft.name.trim()) {
      setCreateError('模板名称不能为空。')
      return
    }
    setCreateBusy(true)
    setCreateError('')
    try {
      const created = await request<Template>('/templates', {
        method: 'POST',
        body: JSON.stringify({ metadata: draftToMetadata(createDraft) }),
      })
      router.push(`/manage/templates/${encodeURIComponent(created.id)}`)
    } catch (cause) {
      setCreateError(errorMessage(cause))
    } finally {
      setCreateBusy(false)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return <section className="template-manager" aria-labelledby="template-manager-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="template-manager-heading">模板管理</h1>
        <p>维护模板元数据、IR 模板和参数版本。草稿通过审核并发布后才会进入模板库。</p>
      </div>
    </header>

    {loading
      ? <LoadingLines label="正在恢复登录" />
      : !user
        ? <EmptyState
          title="登录后管理模板"
          action={<button type="button" className="button button--primary" onClick={() => openLogin('登录具有模板维护权限的账号后，即可创建和编辑模板。')}>登录</button>}
        >
          模板创建和编辑需要服务器授予的模板权限。
        </EmptyState>
        : !canManage
          ? <EmptyState title="无权访问" action={<Link href="/" className="button button--secondary">返回创作台</Link>}>
            当前账号没有创建或编辑模板的权限。
          </EmptyState>
          : <>
            <div className="manager-toolbar">
              <form className="manager-search" onSubmit={(event) => {
                event.preventDefault()
                setQuery(searchInput.trim())
                setPage(1)
              }}>
                <label className="field">
                  <span>检索我的模板</span>
                  <input value={searchInput} placeholder="名称或说明" maxLength={120} onChange={(event) => setSearchInput(event.target.value)} />
                </label>
                <button type="submit" className="button button--secondary button--small">搜索</button>
              </form>
              <div className="manager-toolbar__actions">
                <button type="button" className="button button--primary button--small" onClick={() => { setCreating(true); setCreateDraft(blankMetadata()); setCreateError('') }}>新建模板</button>
                <button type="button" className="button button--secondary button--small" disabled={fetching} onClick={() => setAttempt((value) => value + 1)}>刷新</button>
              </div>
            </div>

            {creating && <section className="editor-panel" aria-labelledby="create-template-heading">
              <div className="editor-head">
                <div>
                  <h2 id="create-template-heading">新建模板</h2>
                  <p className="editor-id">先创建模板容器，再在模板详情中创建第一个版本。</p>
                </div>
                <button type="button" className="button button--link button--small" disabled={createBusy} onClick={() => { setCreating(false); setCreateError('') }}>关闭</button>
              </div>
              <form className="editor-fields" onSubmit={(event) => void createTemplate(event)}>
                <fieldset disabled={createBusy}>
                  <TemplateMetadataForm value={createDraft} onChange={setCreateDraft} disabled={createBusy} />
                </fieldset>
                {createError && <InlineNotice tone="error" title="创建失败">{createError}</InlineNotice>}
                <div className="form-actions">
                  <button type="submit" className="button button--primary" disabled={createBusy}>{createBusy ? '创建中…' : '创建模板容器'}</button>
                  <button type="button" className="button button--secondary button--small" disabled={createBusy} onClick={() => { setCreating(false); setCreateError('') }}>取消</button>
                </div>
              </form>
            </section>}

            {error
              ? <div className="works-error">
                <InlineNotice tone="error" title="模板列表加载失败">
                  无法读取我的模板。请查看技术详情；若后端已启动，可能是模板接口尚未实现。
                  <details className="notice__details">
                    <summary>查看技术详情</summary>
                    <p className="notice__detail">{error}</p>
                  </details>
                </InlineNotice>
                <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
              </div>
              : !templates
                ? <LoadingLines label="正在加载我的模板" />
                : templates.length === 0
                  ? <EmptyState title="还没有模板" action={<button type="button" className="button button--secondary button--small" onClick={() => { setCreating(true); setCreateDraft(blankMetadata()) }}>新建第一个模板</button>}>
                    先创建模板元数据，再添加 IR 模板和参数版本。
                  </EmptyState>
                  : <div className="manager-list-panel">
                    <div className="panel-head">
                      <h2>我的模板</h2>
                      <span className="mono">共 {total} 个 · 第 {page} / {totalPages} 页</span>
                    </div>
                    <ul className="manager-list">
                      {templates.map((template) => {
                        const metadata = displayMetadata(template)
                        return <li className="manager-row" key={template.id}>
                          <div className="manager-row__body">
                            <h3>{metadata.name}</h3>
                            <p>{metadata.description || '暂无说明'}</p>
                            <MetadataSummary metadata={metadata} />
                          </div>
                          <div className="manager-row__side">
                            <span className="mono">{template.publishedVersionId ? '已发布' : '无公开版本'}</span>
                            <span className="mono">{template.latestVersionId ? '有版本' : '无版本'}</span>
                            <Link href={`/manage/templates/${encodeURIComponent(template.id)}`} className="button button--secondary button--small">管理模板</Link>
                          </div>
                        </li>
                      })}
                    </ul>
                    <nav className="pager" aria-label="模板分页">
                      <button type="button" className="button button--secondary button--small" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
                      <span className="mono">第 {page} / {totalPages} 页</span>
                      <button type="button" className="button button--secondary button--small" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
                    </nav>
                  </div>}
          </>}
  </section>
}

interface VersionDraft {
  supportedBackends: string[]
  changeNote: string
  irText: string
  paramsText: string
  bindingsText: string
  constraintsText: string
}

interface VersionEditorState {
  mode: 'new' | 'edit'
  version: TemplateVersion | null
  draft: VersionDraft
}

function newVersionState(): VersionEditorState {
  return {
    mode: 'new',
    version: null,
    draft: {
      supportedBackends: ['canvas2d'],
      changeNote: '',
      irText: prettyJson(STARTER_IR),
      paramsText: prettyJson(STARTER_PARAMETERS),
      bindingsText: prettyJson(STARTER_BINDINGS),
      constraintsText: '[]',
    },
  }
}

function editVersionState(version: TemplateVersion): VersionEditorState {
  return {
    mode: 'edit',
    version,
    draft: {
      supportedBackends: [...version.supportedBackends],
      changeNote: version.changeNote,
      irText: prettyJson(version.irTemplate),
      paramsText: prettyJson(version.parameterSchema),
      bindingsText: prettyJson(version.bindings),
      constraintsText: prettyJson(version.constraints),
    },
  }
}

export function TemplateEditor({ id }: { id: string }) {
  const capabilities = useCapabilities()
  const { user, loading, openLogin } = useSession()
  const [template, setTemplate] = useState<Template | null>(null)
  const [versions, setVersions] = useState<TemplateVersion[]>([])
  const [fetching, setFetching] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [metadataDraft, setMetadataDraft] = useState<MetadataDraft>(blankMetadata())
  const [metadataSaving, setMetadataSaving] = useState(false)
  const [metadataMessage, setMetadataMessage] = useState('')
  const [metadataError, setMetadataError] = useState('')
  const [editor, setEditor] = useState<VersionEditorState | null>(null)
  const [editorError, setEditorError] = useState('')
  const [savingVersion, setSavingVersion] = useState(false)
  const [versionView, setVersionView] = useState<TemplateVersion | null>(null)
  const [actionBusy, setActionBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')
  const [compareLeftId, setCompareLeftId] = useState('')
  const [compareRightId, setCompareRightId] = useState('')
  const metadataLoadedId = useRef('')

  const canManage = Boolean(user && canManageTemplates(user.permissions))
  const reload = () => setAttempt((value) => value + 1)

  useEffect(() => {
    if (!user || !canManage) return
    const controller = new AbortController()
    setFetching(true)
    setError('')
    async function load() {
      try {
        const detail = await request<Template>(`/templates/${encodeURIComponent(id)}`, { signal: controller.signal })
        if (controller.signal.aborted) return
        setTemplate(detail)
        if (metadataLoadedId.current !== detail.id) {
          metadataLoadedId.current = detail.id
          setMetadataDraft(metadataToDraft(detail.workingMetadata ?? detail.metadata))
        }
        const versionPage = await request<Page<TemplateVersion>>(`/templates/${encodeURIComponent(id)}/versions?pageSize=100`, { signal: controller.signal })
        if (!controller.signal.aborted) setVersions(versionPage.items)
      } catch (cause) {
        if (!controller.signal.aborted) setError(errorMessage(cause))
      } finally {
        if (!controller.signal.aborted) setFetching(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [id, user, canManage, attempt])

  useEffect(() => {
    if (versions.length === 0) {
      setCompareLeftId('')
      setCompareRightId('')
      return
    }
    setCompareLeftId((old) => versions.some((version) => version.id === old) ? old : versions[0].id)
    setCompareRightId((old) => versions.some((version) => version.id === old) ? old : versions[1]?.id ?? versions[0].id)
  }, [versions])

  const parsedIr = useMemo(() => {
    if (!editor) return null
    try {
      const value = JSON.parse(editor.draft.irText) as DrawingIR
      return value && typeof value === 'object' && Array.isArray(value.nodes) ? value : null
    } catch {
      return null
    }
  }, [editor])
  const parsedParams = useMemo(() => {
    if (!editor) return null
    try {
      return JSON.parse(editor.draft.paramsText) as Parameter[]
    } catch {
      return null
    }
  }, [editor])
  const parameterErrors = useMemo(() => parsedParams ? validateParameterSchema(parsedParams) : ['参数 schema JSON 语法错误或不是数组。'], [parsedParams])

  async function saveMetadata(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!template || metadataSaving) return
    if (!metadataDraft.name.trim()) {
      setMetadataError('模板名称不能为空。')
      return
    }
    setMetadataSaving(true)
    setMetadataError('')
    setMetadataMessage('')
    try {
      const updated = await request<Template>(`/templates/${encodeURIComponent(template.id)}`, {
        method: 'PATCH',
        body: JSON.stringify({ expectedRevision: template.revision, metadata: draftToMetadata(metadataDraft) }),
      })
      setTemplate(updated)
      setMetadataDraft(metadataToDraft(updated.workingMetadata ?? updated.metadata))
      setMetadataMessage('模板元数据已保存。')
      reload()
    } catch (cause) {
      setMetadataError(errorMessage(cause))
    } finally {
      setMetadataSaving(false)
    }
  }

  function resetMetadata() {
    if (!template) return
    setMetadataDraft(metadataToDraft(template.workingMetadata ?? template.metadata))
    setMetadataError('')
    setMetadataMessage('已恢复为服务器元数据。')
  }

  function startNewVersion() {
    const next = newVersionState()
    const available = capabilities?.backends.find(item => item.available && hasBrowserRenderer(item.backend))
    if (available) next.draft.supportedBackends = [available.backend]
    setEditor(next)
    setVersionView(null)
    setEditorError('')
    setNotice('')
    setActionError('')
  }

  function startEditVersion(version: TemplateVersion) {
    setEditor(editVersionState(version))
    setVersionView(null)
    setEditorError('')
    setNotice('')
    setActionError('')
  }

  function startViewVersion(version: TemplateVersion) {
    setVersionView(version)
    setEditor(null)
    setEditorError('')
    setNotice('')
    setActionError('')
  }

  function formatJson(field: keyof VersionDraft) {
    if (!editor || field === 'changeNote' || field === 'supportedBackends') return
    try {
      const parsed = JSON.parse(editor.draft[field])
      setEditor({ ...editor, draft: { ...editor.draft, [field]: prettyJson(parsed) } })
      setEditorError('')
    } catch (cause) {
      setEditorError(`JSON 语法错误：${cause instanceof Error ? cause.message : '无法解析'}`)
    }
  }

  async function saveVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!editor || savingVersion || !template) return
    if (!editor.draft.supportedBackends.length || editor.draft.supportedBackends.some(backend =>
      !editor.version?.supportedBackends.includes(backend) && !capabilities?.backends.some(item =>
        item.backend === backend && item.available && hasBrowserRenderer(backend)))) {
      setEditorError('请选择至少一个当前已启用且有浏览器适配器的后端。')
      return
    }
    let ir: DrawingIR
    let params: Parameter[]
    let bindings: unknown[]
    let constraints: unknown[]
    try {
      ir = JSON.parse(editor.draft.irText) as DrawingIR
      if (!ir || typeof ir !== 'object' || !Array.isArray(ir.nodes)) throw new Error('IR 必须包含 nodes 数组。')
      params = JSON.parse(editor.draft.paramsText) as Parameter[]
      bindings = JSON.parse(editor.draft.bindingsText) as unknown[]
      constraints = JSON.parse(editor.draft.constraintsText) as unknown[]
    } catch (cause) {
      setEditorError(`JSON 解析失败：${cause instanceof Error ? cause.message : '无法解析'}`)
      return
    }
    if (!Array.isArray(params)) {
      setEditorError('参数 schema 必须是数组。')
      return
    }
    const parameterProblems = validateParameterSchema(params)
    if (parameterProblems.length > 0) {
      setEditorError(parameterProblems.join('\n'))
      return
    }
    if (!Array.isArray(bindings) || !Array.isArray(constraints)) {
      setEditorError('绑定和约束必须是 JSON 数组。')
      return
    }
    const payload: VersionCreateInput = {
      metadata: draftToMetadata(metadataDraft),
      irTemplate: ir,
      parameterSchema: params,
      bindings,
      constraints,
      supportedBackends: editor.draft.supportedBackends,
      changeNote: editor.draft.changeNote.trim(),
    }
    setSavingVersion(true)
    setEditorError('')
    setNotice('')
    try {
      if (editor.mode === 'edit' && editor.version) {
        await request<TemplateVersion>(`/template-versions/${encodeURIComponent(editor.version.id)}`, {
          method: 'PATCH',
          body: JSON.stringify({ expectedRevision: editor.version.revision, ...payload }),
        })
        setNotice(`版本 v${editor.version.versionNumber} 已保存。`)
      } else {
        await request<TemplateVersion>(`/templates/${encodeURIComponent(id)}/versions`, {
          method: 'POST',
          body: JSON.stringify(payload),
        })
        setNotice('新版本已创建。')
      }
      setEditor(null)
      reload()
    } catch (cause) {
      setEditorError(errorMessage(cause))
    } finally {
      setSavingVersion(false)
    }
  }

  async function submitVersion(version: TemplateVersion) {
    if (!window.confirm(`提交 v${version.versionNumber} 进入审核？`)) return
    setActionBusy(`submit-${version.id}`)
    setActionError('')
    setNotice('')
    try {
      await request<TemplateVersion>(`/template-versions/${encodeURIComponent(version.id)}/submit`, {
        method: 'POST',
        body: JSON.stringify({ expectedRevision: version.revision }),
      })
      setNotice(`v${version.versionNumber} 已提交审核。`)
      reload()
    } catch (cause) {
      setActionError(errorMessage(cause))
    } finally {
      setActionBusy('')
    }
  }

  async function cloneVersion(version: TemplateVersion) {
    if (!window.confirm(`从 v${version.versionNumber} 克隆一个新草稿？`)) return
    setActionBusy(`clone-${version.id}`)
    setActionError('')
    setNotice('')
    try {
      await request<TemplateVersion>(`/templates/${encodeURIComponent(id)}/versions`, {
        method: 'POST',
        body: JSON.stringify({ fromVersionId: version.id, changeNote: `从 v${version.versionNumber} 克隆` }),
      })
      setNotice('已克隆为新的草稿版本。')
      reload()
    } catch (cause) {
      setActionError(errorMessage(cause))
    } finally {
      setActionBusy('')
    }
  }

  async function archiveVersion(version: TemplateVersion) {
    const reason = window.prompt(`归档 v${version.versionNumber} 的理由：`)
    if (reason === null) return
    if (!reason.trim()) {
      setActionError('归档需要填写理由。')
      return
    }
    setActionBusy(`archive-${version.id}`)
    setActionError('')
    setNotice('')
    try {
      await request<TemplateVersion>(`/template-versions/${encodeURIComponent(version.id)}/archive`, {
        method: 'POST',
        body: JSON.stringify({ expectedRevision: version.revision, reason: reason.trim() }),
      })
      setNotice(`v${version.versionNumber} 已归档。`)
      reload()
    } catch (cause) {
      setActionError(errorMessage(cause))
    } finally {
      setActionBusy('')
    }
  }

  const left = versions.find((version) => version.id === compareLeftId)
  const right = versions.find((version) => version.id === compareRightId)
  const changedFields = left && right ? diffTemplateVersions(left, right) : []

  return <section className="template-editor-page" aria-labelledby="template-editor-heading">
    <nav className="back-link" aria-label="返回">
      <Link href="/manage/templates">返回模板管理</Link>
    </nav>

    {loading
      ? <LoadingLines label="正在恢复登录" />
      : !user
        ? <EmptyState title="登录后管理模板" action={<button type="button" className="button button--primary" onClick={() => openLogin('登录具有模板维护权限的账号后，即可管理模板。')}>登录</button>}>
          模板管理需要登录。
        </EmptyState>
        : !canManage
          ? <EmptyState title="无权访问" action={<Link href="/" className="button button--secondary">返回创作台</Link>}>
            当前账号没有模板维护权限。
          </EmptyState>
          : error
            ? <>
              <header className="work-header"><h1 id="template-editor-heading">模板加载失败</h1></header>
              <div className="viewer-error">
                <InlineNotice tone="error" title="无法读取模板">
                  模板可能不存在、无权访问，或后端尚未实现模板接口。
                  <details className="notice__details">
                    <summary>查看技术详情</summary>
                    <p className="notice__detail">{error}</p>
                  </details>
                </InlineNotice>
                <button type="button" className="button button--secondary" onClick={reload}>重新加载</button>
              </div>
            </>
            : fetching || !template
              ? <><header className="work-header"><h1 id="template-editor-heading">正在读取模板</h1></header><LoadingLines label="正在读取模板" /></>
              : <>
                <header className="work-header">
                  <h1 id="template-editor-heading">{displayMetadata(template).name}</h1>
                  <div className="work-header__meta">
                    <span className={`status-chip ${template.publishedVersionId ? 'is-published' : 'is-private'}`}>{template.publishedVersionId ? '已发布' : '无公开版本'}</span>
                    <span className="mono">revision {template.revision}</span>
                    <span className="mono">{versions.length} 个版本</span>
                  </div>
                </header>

                {notice && <InlineNotice tone="success">{notice}</InlineNotice>}
                {actionError && <InlineNotice tone="error" title="版本操作失败">{actionError}</InlineNotice>}

                <section className="editor-panel" aria-labelledby="metadata-editor-heading">
                  <div className="editor-head">
                    <div>
                      <h2 id="metadata-editor-heading">模板元数据</h2>
                      <p className="editor-id">元数据用于模板库展示与检索；版本内容单独维护。</p>
                    </div>
                    <span className="mono">rev {template.revision}</span>
                  </div>
                  <form className="editor-fields" onSubmit={(event) => void saveMetadata(event)}>
                    <fieldset disabled={metadataSaving}>
                      <TemplateMetadataForm value={metadataDraft} onChange={setMetadataDraft} disabled={metadataSaving} />
                    </fieldset>
                    {metadataMessage && <InlineNotice tone="success">{metadataMessage}</InlineNotice>}
                    {metadataError && <InlineNotice tone="error" title="元数据保存失败">{metadataError}</InlineNotice>}
                    <div className="form-actions">
                      <button type="submit" className="button button--primary" disabled={metadataSaving}>{metadataSaving ? '保存中…' : '保存元数据'}</button>
                      <button type="button" className="button button--secondary button--small" disabled={metadataSaving} onClick={resetMetadata}>重置为服务器值</button>
                    </div>
                  </form>
                </section>

                <section className="editor-panel" aria-labelledby="versions-heading">
                  <div className="editor-head">
                    <div>
                      <h2 id="versions-heading">版本</h2>
                      <p className="editor-id">草稿/已退回可编辑或送审；已发布可归档；已提交/归档只读。</p>
                    </div>
                    <button type="button" className="button button--primary button--small" onClick={startNewVersion}>新建版本</button>
                  </div>
                  {versions.length === 0
                    ? <EmptyState title="还没有版本" action={<button type="button" className="button button--secondary button--small" onClick={startNewVersion}>创建第一个版本</button>}>
                      新版本需要合法 IR 模板、参数 schema 和明确的后端支持声明。
                    </EmptyState>
                    : <ul className="manager-list version-list">
                      {versions.map((version) => <li className={`manager-row version-row${editor?.version?.id === version.id || versionView?.id === version.id ? ' is-selected' : ''}`} key={version.id}>
                        <div className="manager-row__body">
                          <div className="version-row__head">
                            <strong>v{version.versionNumber}</strong>
                            <span className={`status-chip is-${version.status}`}>{versionStatusLabel(version.status)}</span>
                            <span className="mono">rev {version.revision}</span>
                          </div>
                          <p>{version.changeNote || '无变更说明'}</p>
                          <div className="tag-line">
                            <span className="mono">创建 {formatDateTime(version.createdAt)}</span>
                            {version.submittedAt && <span className="mono">提交 {formatDateTime(version.submittedAt)}</span>}
                            {version.publishedAt && <span className="mono">发布 {formatDateTime(version.publishedAt)}</span>}
                          </div>
                          {version.lastReview?.decision === 'reject' && <p className="rejection-note">退回理由：{version.lastReview.reason || '未填写'}</p>}
                        </div>
                        <div className="manager-row__side version-actions">
                          <button type="button" className="button button--secondary button--small" onClick={() => startViewVersion(version)}>查看</button>
                          {isEditableVersion(version.status) && <button type="button" className="button button--secondary button--small" onClick={() => startEditVersion(version)}>编辑</button>}
                          {isEditableVersion(version.status) && <button type="button" className="button button--secondary button--small" disabled={actionBusy === `submit-${version.id}`} onClick={() => void submitVersion(version)}>送审</button>}
                          <button type="button" className="button button--secondary button--small" disabled={actionBusy === `clone-${version.id}`} onClick={() => void cloneVersion(version)}>克隆</button>
                          {version.status === 'published' && <button type="button" className="button button--secondary button--small" disabled={actionBusy === `archive-${version.id}`} onClick={() => void archiveVersion(version)}>归档</button>}
                        </div>
                      </li>)}
                    </ul>}
                </section>

                {versionView && <section className="editor-panel version-view" aria-labelledby="version-view-heading">
                  <div className="editor-head">
                    <div>
                      <h2 id="version-view-heading">查看 v{versionView.versionNumber}</h2>
                      <p className="editor-id">{versionStatusLabel(versionView.status)} · revision {versionView.revision}</p>
                    </div>
                    <button type="button" className="button button--link button--small" onClick={() => setVersionView(null)}>关闭查看</button>
                  </div>
                  {versionView.lastReview && <InlineNotice tone={versionView.lastReview.decision === 'reject' ? 'error' : 'success'}>
                    {versionView.lastReview.decision === 'approve' ? '审核通过' : '审核退回'}：{versionView.lastReview.reason || '未填写理由'}
                  </InlineNotice>}
                  <div className="version-view-grid">
                    <div className="version-preview"><CanvasPreview ir={versionView.irTemplate} backend={preferredBrowserBackend(versionView.supportedBackends)} timeMs={versionView.irTemplate.durationMs} /></div>
                    <div>
                      <dl className="label-list">
                        <div><dt>变更说明</dt><dd>{versionView.changeNote || '—'}</dd></div>
                        <div><dt>参数</dt><dd>{versionView.parameterSchema.length} 项</dd></div>
                        <div><dt>绑定</dt><dd>{versionView.bindings.length} 项</dd></div>
                        <div><dt>约束</dt><dd>{versionView.constraints.length} 项</dd></div>
                      </dl>
                      <p className="plate-caption">模板原始 IR，未应用参数与绑定。</p>
                      {isEditableVersion(versionView.status) && <Link
                        className="button button--secondary button--small"
                        href={`/?draft=${encodeURIComponent(versionView.id)}&backend=${encodeURIComponent(preferredBrowserBackend(versionView.supportedBackends))}`}
                      >在创作台私有预览默认参数</Link>}
                    </div>
                  </div>
                  <details className="tech-details">
                    <summary>版本 JSON</summary>
                    <pre>{prettyJson(versionView)}</pre>
                  </details>
                </section>}

                {editor && <section className="editor-panel version-editor" aria-labelledby="version-editor-heading">
                  <div className="editor-head">
                    <div>
                      <h2 id="version-editor-heading">{editor.mode === 'edit' ? `编辑 v${editor.version?.versionNumber}` : '新建版本'}</h2>
                      <p className="editor-id">{editor.mode === 'edit' ? `revision ${editor.version?.revision}` : '草稿将使用当前元数据；保存后再送审。'}</p>
                    </div>
                    <button type="button" className="button button--link button--small" disabled={savingVersion} onClick={() => { setEditor(null); setEditorError('') }}>关闭编辑</button>
                  </div>
                  <form className="editor-fields" onSubmit={(event) => void saveVersion(event)}>
                    <label className="field">
                      <span>变更说明（最多 500 字）</span>
                      <input maxLength={500} value={editor.draft.changeNote} disabled={savingVersion} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, changeNote: event.target.value } })} />
                    </label>
                    <fieldset disabled={savingVersion}>
                      <legend>版本支持的渲染后端</legend>
                      {(['canvas2d', 'css', 'webgl', 'turtle'] as const).map(backend => <label className="check" key={backend}>
                        <input type="checkbox" checked={editor.draft.supportedBackends.includes(backend)}
                          disabled={!capabilities?.backends.some(item => item.backend === backend && item.available && hasBrowserRenderer(backend))}
                          onChange={event => setEditor({ ...editor, draft: { ...editor.draft, supportedBackends: event.target.checked
                            ? [...editor.draft.supportedBackends, backend] : editor.draft.supportedBackends.filter(item => item !== backend) } })} />
                        {backendLabel(backend)}
                      </label>)}
                    </fieldset>
                    <div className="json-grid">
                      <label className="field json-field">
                        <span className="json-label">IR 模板 JSON <button type="button" className="button button--link button--small" disabled={savingVersion} onClick={() => formatJson('irText')}>格式化</button></span>
                        <textarea className="json-editor" rows={12} value={editor.draft.irText} disabled={savingVersion} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, irText: event.target.value } })} />
                      </label>
                      <label className="field json-field">
                        <span className="json-label">参数 schema JSON <button type="button" className="button button--link button--small" disabled={savingVersion} onClick={() => formatJson('paramsText')}>格式化</button></span>
                        <textarea className="json-editor" rows={12} value={editor.draft.paramsText} disabled={savingVersion} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, paramsText: event.target.value } })} />
                      </label>
                    </div>
                    <div className="json-grid">
                      <label className="field json-field">
                        <span className="json-label">参数绑定 JSON <button type="button" className="button button--link button--small" disabled={savingVersion} onClick={() => formatJson('bindingsText')}>格式化</button></span>
                        <textarea className="json-editor" rows={7} value={editor.draft.bindingsText} disabled={savingVersion} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, bindingsText: event.target.value } })} />
                      </label>
                      <label className="field json-field">
                        <span className="json-label">参数约束 JSON <button type="button" className="button button--link button--small" disabled={savingVersion} onClick={() => formatJson('constraintsText')}>格式化</button></span>
                        <textarea className="json-editor" rows={7} value={editor.draft.constraintsText} disabled={savingVersion} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, constraintsText: event.target.value } })} />
                      </label>
                    </div>
                    {parsedIr
                      ? <div className="version-preview"><CanvasPreview ir={parsedIr} backend={preferredBrowserBackend(editor.draft.supportedBackends)} timeMs={parsedIr.durationMs} /><p className="plate-caption">模板原始 IR 预览 · {backendLabel(preferredBrowserBackend(editor.draft.supportedBackends))} · 未应用参数与绑定</p></div>
                      : <p className="field-note">IR JSON 尚未解析为带 nodes 数组的对象，暂时无法预览。</p>}
                    {parameterErrors.length > 0
                      ? <InlineNotice tone="error" title="参数 schema 校验未通过">{parameterErrors.join('\n')}</InlineNotice>
                      : parsedParams && <div className="parameter-preview">
                        <p className="field-note">参数 schema 解析通过：{parsedParams.length} 项。</p>
                        <ul className="parameter-list">
                          {parsedParams.map((parameter) => <li className="parameter-row" key={parameter.name}>
                            <div className="parameter-row__head"><strong>{parameter.label}</strong><span className="mono">{parameter.name} · {parameterTypeLabel(parameter.type)}</span></div>
                            <div className="parameter-row__meta"><span>默认 {parameterValueLabel(parameter.default)}</span></div>
                          </li>)}
                        </ul>
                      </div>}
                    {editorError && <InlineNotice tone="error" title="版本保存失败">{editorError}</InlineNotice>}
                    <div className="form-actions">
                      <button type="submit" className="button button--primary" disabled={savingVersion}>{savingVersion ? '保存中…' : editor.mode === 'edit' ? '保存版本' : '创建版本'}</button>
                      <button type="button" className="button button--secondary button--small" disabled={savingVersion} onClick={() => { setEditor(null); setEditorError('') }}>取消</button>
                    </div>
                  </form>
                </section>}

                {versions.length > 1 && <section className="editor-panel compare-panel" aria-labelledby="compare-heading">
                  <div className="editor-head">
                    <div>
                      <h2 id="compare-heading">版本对比</h2>
                      <p className="editor-id">比较元数据、IR、参数、绑定、约束和变更说明。</p>
                    </div>
                  </div>
                  <div className="compare-selects">
                    <label className="field"><span>左侧版本</span><select value={compareLeftId} onChange={(event) => setCompareLeftId(event.target.value)}>{versions.map((version) => <option value={version.id} key={version.id}>v{version.versionNumber} · {versionStatusLabel(version.status)}</option>)}</select></label>
                    <label className="field"><span>右侧版本</span><select value={compareRightId} onChange={(event) => setCompareRightId(event.target.value)}>{versions.map((version) => <option value={version.id} key={version.id}>v{version.versionNumber} · {versionStatusLabel(version.status)}</option>)}</select></label>
                  </div>
                  {left && right && <div className="compare-body">
                    <p className="compare-changed">{changedFields.length > 0 ? `变更字段：${changedFields.join('、')}` : '两个版本的内容字段一致。'}</p>
                    <div className="compare-grid">
                      <div><p className="plate-caption">v{left.versionNumber}</p><pre>{prettyJson({ metadata: left.metadata, irTemplate: left.irTemplate, parameterSchema: left.parameterSchema, bindings: left.bindings, constraints: left.constraints, supportedBackends: left.supportedBackends, changeNote: left.changeNote })}</pre></div>
                      <div><p className="plate-caption">v{right.versionNumber}</p><pre>{prettyJson({ metadata: right.metadata, irTemplate: right.irTemplate, parameterSchema: right.parameterSchema, bindings: right.bindings, constraints: right.constraints, supportedBackends: right.supportedBackends, changeNote: right.changeNote })}</pre></div>
                    </div>
                  </div>}
                </section>}
              </>}
  </section>
}
