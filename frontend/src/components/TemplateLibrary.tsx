'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import type { Page, Parameter, Template, TemplateVersion } from '@/types/core'
import { formatDateTime, parameterTypeLabel, parameterValueLabel, symbolLabel } from '@/utils/format'
import { SYMBOL_OPTIONS } from '@/utils/template'
import CanvasPreview from './CanvasPreview'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'
import { useCapabilities } from '@/hooks/useCapabilities'
import { backendLabel, preferredBrowserBackend } from '@/renderers/backend'
import { hasBrowserRenderer } from '@/renderers/registry'

const PAGE_SIZE = 12

function TemplatePlate({ template }: { template: Template }) {
  const [preview, setPreview] = useState('')
  const [previewFailed, setPreviewFailed] = useState(false)

  useEffect(() => {
    if (!template.thumbnailArtifactId) return
    const controller = new AbortController()
    let url = ''
    request<Blob>(`/artifacts/${encodeURIComponent(template.thumbnailArtifactId)}`, {
      signal: controller.signal, headers: { Accept: 'image/png' },
    }).then((blob) => {
      if (controller.signal.aborted) return
      url = URL.createObjectURL(blob)
      setPreview(url)
    }).catch(() => {
      if (!controller.signal.aborted) setPreviewFailed(true)
    })
    return () => {
      controller.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [template.thumbnailArtifactId])

  if (preview) return <img src={preview} alt={`${template.metadata.name} 模板预览`} loading="lazy" />
  const mark = template.metadata.symbols?.length ? symbolLabel(template.metadata.symbols[0]) : template.metadata.name.slice(0, 1)
  return <div className="template-card__placeholder">
    <span aria-hidden="true">{mark || '模'}</span>
    <small>{previewFailed ? '缩略图不可用' : '模板符号'}</small>
  </div>
}

export function TemplateLibrary() {
  const capabilities = useCapabilities()
  const [backend, setBackend] = useState('')
  const [templates, setTemplates] = useState<Template[] | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [searchInput, setSearchInput] = useState('')
  const [query, setQuery] = useState('')
  const [timeThemeInput, setTimeThemeInput] = useState('')
  const [timeTheme, setTimeTheme] = useState('')
  const [symbols, setSymbols] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const symbolKey = symbols.join(',')

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setTemplates(null)
    const params = new URLSearchParams({ page: String(page), pageSize: String(PAGE_SIZE) })
    if (backend) params.set('backend', backend)
    if (query) params.set('q', query)
    if (timeTheme) params.set('timeTheme', timeTheme)
    for (const symbol of symbols) params.append('symbols', symbol)
    request<Page<Template>>(`/templates?${params.toString()}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return
        setTemplates(data.items.filter((item) => item.publishedVersionId && (!backend || item.supportedBackends.includes(backend))))
        setTotal(data.total)
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [page, query, timeTheme, symbolKey, attempt, backend])

  function toggleSymbol(symbol: string) {
    setSymbols((old) => old.includes(symbol) ? old.filter((item) => item !== symbol) : [...old, symbol])
    setPage(1)
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return <section className="template-library" aria-labelledby="templates-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="templates-heading">模板库</h1>
        <p>从已发布的文化模板开始创作。模板不是固定图片：参数、IR 模板和来源会随作品保存。</p>
      </div>
    </header>

    <div className="manager-toolbar">
      <form className="manager-search" onSubmit={(event) => {
        event.preventDefault()
        setQuery(searchInput.trim())
        setTimeTheme(timeThemeInput.trim())
        setPage(1)
      }}>
        <label className="field">
          <span>检索模板</span>
          <input value={searchInput} placeholder="名称或说明" maxLength={120} onChange={(event) => setSearchInput(event.target.value)} />
        </label>
        <label className="field">
          <span>时间主题</span>
          <input value={timeThemeInput} placeholder="可选" maxLength={40} onChange={(event) => setTimeThemeInput(event.target.value)} />
        </label>
        <label className="field">渲染后端<select value={backend} onChange={event => { setBackend(event.target.value); setPage(1) }}>
          <option value="">全部后端</option>
          {capabilities?.backends.filter(item => item.available && hasBrowserRenderer(item.backend)).map(item =>
            <option key={item.backend} value={item.backend}>{backendLabel(item.backend)}</option>)}
        </select></label>
        <button type="submit" className="button button--secondary button--small">搜索</button>
      </form>
      <button type="button" className="button button--secondary button--small" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>刷新</button>
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
      {symbols.length > 0 && <button type="button" className="button button--link button--small" onClick={() => { setSymbols([]); setPage(1) }}>清除筛选</button>}
    </div>

    {error
      ? <div className="works-error">
        <InlineNotice tone="error" title="模板库加载失败">
          无法读取已发布模板。请查看技术详情；若后端已启动，可能是模板接口尚未实现。
          <details className="notice__details">
            <summary>查看技术详情</summary>
            <p className="notice__detail">{error}</p>
          </details>
        </InlineNotice>
        <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
      </div>
      : loading && !templates
        ? <LoadingLines label="正在加载模板库" />
        : !templates || templates.length === 0
          ? <EmptyState title="没有匹配的已发布模板">
            调整检索词或文化符号筛选；也可以稍后重试。草稿和未发布版本不会出现在模板库。
          </EmptyState>
          : <>
            <div className="works-toolbar">
              <span className="mono">共 {total} 个模板 · 当前显示 {templates.length} 个</span>
              <span className="mono">第 {page} / {totalPages} 页</span>
            </div>
            <ul className="template-grid">
              {templates.map((template) => <li className="template-card" key={template.id}>
                <Link href={`/templates/${encodeURIComponent(template.id)}`} className="template-card__link">
                  <div className="template-card__plate"><TemplatePlate template={template} /></div>
                  <div className="template-card__body">
                    <div className="template-card__meta">
                      <span>{template.metadata.symbols?.map(symbolLabel).join(' · ') || '文化模板'}</span>
                      <span className="mono">已发布</span>
                    </div>
                    <h2>{template.metadata.name}</h2>
                    <p>{template.metadata.description || '暂无说明'}</p>
                    <div className="template-card__foot">
                      <span className="mono">{template.metadata.timeTheme || '未标注时间主题'}</span>
                      <span className="template-card__cta">查看模板</span>
                    </div>
                  </div>
                </Link>
              </li>)}
            </ul>
            <nav className="pager" aria-label="模板分页">
              <button type="button" className="button button--secondary button--small" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
              <span className="mono">第 {page} / {totalPages} 页</span>
              <button type="button" className="button button--secondary button--small" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
            </nav>
          </>}
  </section>
}

function parameterSummary(parameter: Parameter) {
  if (parameter.type === 'number' || parameter.type === 'integer') {
    return `${parameter.min} — ${parameter.max} · 步长 ${parameter.step}`
  }
  if (parameter.type === 'enum') return parameter.options.join(' / ')
  if (parameter.type === 'color') return '颜色值'
  return parameter.type === 'boolean' ? '开关' : ''
}

export function TemplateViewer({ id }: { id: string }) {
  const [previewBackend, setPreviewBackend] = useState('')
  const { user } = useSession()
  const [template, setTemplate] = useState<Template | null>(null)
  const [version, setVersion] = useState<TemplateVersion | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const canManage = Boolean(user?.permissions.includes('templates.create')
    || user?.permissions.includes('templates.editOwn')
    || user?.permissions.includes('templates.editAny'))

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setTemplate(null)
    setVersion(null)
    setPreviewBackend('')
    async function load() {
      try {
        const detail = await request<Template>(`/templates/${encodeURIComponent(id)}`, { signal: controller.signal })
        if (controller.signal.aborted) return
        setTemplate(detail)
        if (detail.publishedVersionId) {
          const published = await request<TemplateVersion>(`/template-versions/${encodeURIComponent(detail.publishedVersionId)}`, { signal: controller.signal })
          if (!controller.signal.aborted) setVersion(published)
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(errorMessage(cause))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [id, attempt])

  const publishedMetadata = version?.metadata ?? template?.metadata

  return <section className="template-viewer" aria-labelledby="template-heading">
    <nav className="back-link" aria-label="返回">
      <Link href="/templates">返回模板库</Link>
    </nav>
    {error
      ? <>
        <header className="work-header">
          <h1 id="template-heading">模板加载失败</h1>
        </header>
        <div className="viewer-error">
          <InlineNotice tone="error" title="无法读取模板">
            模板可能不存在、没有公开版本，或后端尚未实现模板接口。
            <details className="notice__details">
              <summary>查看技术详情</summary>
              <p className="notice__detail">{error}</p>
            </details>
          </InlineNotice>
          <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
        </div>
      </>
      : loading || !template
        ? <><header className="work-header"><h1 id="template-heading">正在读取模板</h1></header><LoadingLines label="正在读取模板" /></>
        : <>
          <header className="work-header">
            <h1 id="template-heading">{publishedMetadata?.name ?? '未命名模板'}</h1>
            <div className="work-header__meta">
              {version
                ? <span className="status-chip is-published">已发布 · v{version.versionNumber}</span>
                : <span className="status-chip is-private">无公开版本</span>}
              <span className="mono">{publishedMetadata?.symbols?.map(symbolLabel).join(' · ') || '未标注符号'}</span>
            </div>
          </header>

          {!version
            ? <EmptyState
              title="该模板暂无公开版本"
              action={canManage ? <Link href={`/manage/templates/${encodeURIComponent(id)}`} className="button button--secondary">进入模板管理</Link> : undefined}
            >
              只有通过审核并发布的模板版本会出现在模板库。草稿仍可在模板管理中查看。
            </EmptyState>
            : <div className="template-detail-layout">
              <div className="template-stage">
                <div className="template-preview">
                  <CanvasPreview ir={version.irTemplate} backend={previewBackend || preferredBrowserBackend(version.supportedBackends)} timeMs={version.irTemplate.durationMs} />
                </div>
                <p className="plate-caption">模板原始 IR 预览 · 未应用参数与绑定 · {version.irTemplate.viewport.width} × {version.irTemplate.viewport.height}</p>
              </div>
              <aside className="template-label" aria-label="模板信息">
                <h2>模板信息</h2>
                <p className="template-description">{publishedMetadata?.description || '暂无说明'}</p>
                <dl className="label-list">
                  <div><dt>版本</dt><dd className="mono">v{version.versionNumber} · rev {version.revision}</dd></div>
                  <div><dt>发布时间</dt><dd className="mono">{formatDateTime(version.publishedAt)}</dd></div>
                  <div><dt>参数</dt><dd>{version.parameterSchema.length} 项</dd></div>
                  <div><dt>符号</dt><dd>{publishedMetadata?.symbols?.map(symbolLabel).join(' · ') || '—'}</dd></div>
                  <div><dt>时间主题</dt><dd>{publishedMetadata?.timeTheme || '—'}</dd></div>
                </dl>
                <label className="field">模板预览后端<select value={previewBackend || preferredBrowserBackend(version.supportedBackends)} onChange={event => setPreviewBackend(event.target.value)}>
                  {version.supportedBackends.map(backend => <option key={backend} value={backend} disabled={!hasBrowserRenderer(backend)}>{backendLabel(backend)}</option>)}
                </select></label>
                <Link className="button button--primary template-use" href={`/?template=${encodeURIComponent(template.id)}&backend=${encodeURIComponent(previewBackend || preferredBrowserBackend(version.supportedBackends))}`}>使用此模板创作</Link>
                {canManage && <Link className="button button--secondary button--small" href={`/manage/templates/${encodeURIComponent(template.id)}`}>进入模板管理</Link>}
              </aside>
            </div>}

          {version && <section className="template-parameters" aria-labelledby="template-parameters-heading">
            <h2 id="template-parameters-heading">参数</h2>
            {version.parameterSchema.length === 0
              ? <p className="panel-note">该模板没有可调参数，使用默认 IR 生成。</p>
              : <div className="parameter-list">
                {version.parameterSchema.map((parameter) => <div className="parameter-row" key={parameter.name}>
                  <div className="parameter-row__head">
                    <strong>{parameter.label}</strong>
                    <span className="mono">{parameter.name} · {parameterTypeLabel(parameter.type)}</span>
                  </div>
                  <div className="parameter-row__meta">
                    <span>默认 {parameterValueLabel(parameter.default)}</span>
                    <span className="mono">{parameterSummary(parameter)}</span>
                  </div>
                  {parameter.description && <p>{parameter.description}</p>}
                </div>)}
              </div>}
          </section>}

          {version && <section className="technical" aria-labelledby="template-technical-heading">
            <h2 id="template-technical-heading">技术档案</h2>
            <details className="tech-details">
              <summary>IR 模板 JSON</summary>
              <pre>{JSON.stringify(version.irTemplate, null, 2)}</pre>
            </details>
            <details className="tech-details">
              <summary>参数、绑定与约束</summary>
              <pre>{JSON.stringify({
                templateId: version.templateId,
                versionId: version.id,
                generatorRef: version.generatorRef,
                parameterSchema: version.parameterSchema,
                bindings: version.bindings,
                constraints: version.constraints,
              }, null, 2)}</pre>
            </details>
          </section>}
        </>}
  </section>
}
