'use client'

import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import type {
  AuditLog, BackupPackage, ImportApplied, ImportApply, ImportValidation, Issue, Page,
} from '@/types/core'
import { formatDateTime, shortId } from '@/utils/format'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'

type Tab = 'export' | 'import' | 'audit'

const EXPORT_TYPES = [
  { value: 'entities', label: '知识实体' },
  { value: 'concepts', label: '本体概念' },
  { value: 'tags', label: '标签' },
  { value: 'rules', label: '映射规则' },
  { value: 'templates', label: '模板' },
  { value: 'workSnapshots', label: '作品 IR 快照' },
]

const emptyFilters = { actorId: '', action: '', targetType: '', requestId: '', fromTime: '', toTime: '' }

function issueList(errors: Issue[], tone: 'error' | 'success') {
  if (errors.length === 0) return null
  return <InlineNotice tone={tone} title={tone === 'error' ? '校验问题' : '导入警告'}>
    <ul className="issue-list">
      {errors.map((issue, index) => <li key={`${issue.code}-${index}`}>
        <span className="mono">{issue.code}</span> {issue.message}
        {issue.path && <span className="mono"> · {issue.path}</span>}
      </li>)}
    </ul>
  </InlineNotice>
}

function toIso(value: string) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toISOString()
}

export default function AdminOps() {
  const { user, loading, openLogin } = useSession()
  const canExport = Boolean(user?.permissions.includes('exports.create'))
  const canImport = Boolean(user?.permissions.includes('imports.apply'))
  const canAudit = Boolean(user?.permissions.includes('audit.read'))
  const availableTabs = useMemo(() => [
    ...(canExport ? [{ key: 'export' as const, label: '导出' }] : []),
    ...(canImport ? [{ key: 'import' as const, label: '导入' }] : []),
    ...(canAudit ? [{ key: 'audit' as const, label: '审计日志' }] : []),
  ], [canExport, canImport, canAudit])
  const canAccess = availableTabs.length > 0
  const [tab, setTab] = useState<Tab>('export')
  const activeTab = availableTabs.some((item) => item.key === tab) ? tab : availableTabs[0]?.key

  const [exportTypes, setExportTypes] = useState<string[]>(EXPORT_TYPES.map((item) => item.value))
  const [exportPackage, setExportPackage] = useState<BackupPackage | null>(null)
  const [exportBusy, setExportBusy] = useState(false)
  const [exportError, setExportError] = useState('')
  const [exportNotice, setExportNotice] = useState('')

  const [packageText, setPackageText] = useState('')
  const [fileName, setFileName] = useState('')
  const [parseError, setParseError] = useState('')
  const [validation, setValidation] = useState<ImportValidation | null>(null)
  const [applied, setApplied] = useState<ImportApplied | null>(null)
  const [importBusy, setImportBusy] = useState(false)
  const [importError, setImportError] = useState('')
  const [importNotice, setImportNotice] = useState('')

  const [auditDraft, setAuditDraft] = useState(emptyFilters)
  const [auditFilters, setAuditFilters] = useState(emptyFilters)
  const [auditPage, setAuditPage] = useState(1)
  const [auditLogs, setAuditLogs] = useState<AuditLog[] | null>(null)
  const [auditTotal, setAuditTotal] = useState(0)
  const [auditLoading, setAuditLoading] = useState(false)
  const [auditError, setAuditError] = useState('')
  const [auditAttempt, setAuditAttempt] = useState(0)

  useEffect(() => {
    if (!canAccess) return
    if (!availableTabs.some((item) => item.key === tab)) setTab(availableTabs[0].key)
  }, [canAccess, availableTabs, tab])

  useEffect(() => {
    if (!user || !canAudit || activeTab !== 'audit') return
    const controller = new AbortController()
    setAuditLoading(true)
    setAuditError('')
    setAuditLogs(null)
    const params = new URLSearchParams({ page: String(auditPage), pageSize: '20' })
    for (const [key, value] of Object.entries(auditFilters)) {
      if (!value) continue
      const mapped = key === 'fromTime' || key === 'toTime' ? toIso(value) : value
      if (mapped) params.set(key, mapped)
    }
    request<Page<AuditLog>>(`/audit-logs?${params.toString()}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return
        setAuditLogs(data.items)
        setAuditTotal(data.total)
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setAuditError(errorMessage(cause))
      })
      .finally(() => {
        if (!controller.signal.aborted) setAuditLoading(false)
      })
    return () => controller.abort()
  }, [user, canAudit, activeTab, auditPage, auditFilters, auditAttempt])

  async function runExport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (exportBusy) return
    if (exportTypes.length === 0) {
      setExportError('至少选择一种导出类型。')
      return
    }
    setExportBusy(true)
    setExportError('')
    setExportNotice('')
    setExportPackage(null)
    try {
      const data = await request<BackupPackage>('/admin/exports', {
        method: 'POST',
        body: JSON.stringify({ types: exportTypes }),
      })
      setExportPackage(data)
      setExportNotice('导出包已生成；可以下载 JSON。')
    } catch (cause) {
      setExportError(errorMessage(cause))
    } finally {
      setExportBusy(false)
    }
  }

  function downloadExport() {
    if (!exportPackage) return
    const blob = new Blob([JSON.stringify(exportPackage, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `codepoet-backup-${new Date().toISOString().replace(/[:.]/g, '-')}.json`
    anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  async function readFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    if (file.size > 5 * 1024 * 1024) {
      setParseError('导入文件超过 5MiB。')
      return
    }
    const text = await file.text()
    setPackageText(text)
    setFileName(file.name)
    setValidation(null)
    setApplied(null)
    setImportError('')
    setImportNotice('')
    setParseError('')
  }

  async function validateImport() {
    if (importBusy) return
    if (!packageText.trim()) {
      setParseError('请选择文件或粘贴导入 JSON。')
      return
    }
    let parsed: unknown
    try {
      parsed = JSON.parse(packageText)
    } catch (cause) {
      setParseError(`JSON 语法错误：${cause instanceof Error ? cause.message : '无法解析'}`)
      return
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setParseError('导入包必须是 JSON 对象。')
      return
    }
    setParseError('')
    setImportBusy(true)
    setImportError('')
    setImportNotice('')
    setValidation(null)
    setApplied(null)
    try {
      const result = await request<ImportValidation>('/admin/imports/validate', {
        method: 'POST',
        body: JSON.stringify(parsed),
      })
      setValidation(result)
      setImportNotice(result.valid ? '导入包校验通过；确认后会事务执行。' : '导入包校验未通过，不能执行。')
    } catch (cause) {
      setImportError(errorMessage(cause))
    } finally {
      setImportBusy(false)
    }
  }

  async function applyImport() {
    if (!validation?.valid || applied || importBusy) return
    const payload: ImportApply = {
      validationId: validation.validationId,
      expectedDataRevision: validation.expectedDataRevision,
    }
    setImportBusy(true)
    setImportError('')
    setImportNotice('')
    try {
      const result = await request<ImportApplied>('/admin/imports/apply', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setApplied(result)
      setImportNotice('导入已事务执行；请重新校验下一份包。')
    } catch (cause) {
      setImportError(errorMessage(cause))
    } finally {
      setImportBusy(false)
    }
  }

  const auditTotalPages = Math.max(1, Math.ceil(auditTotal / 20))

  return <section className="admin-ops-page" aria-labelledby="admin-ops-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="admin-ops-heading">运维与审计</h1>
        <p>导出知识/模板/规则备份，预校验并事务导入 JSON，查看关键写操作审计日志。</p>
      </div>
    </header>

    {loading
      ? <LoadingLines label="正在恢复登录" />
      : !user
        ? <EmptyState title="登录后进入运维页面" action={<button type="button" className="button button--primary" onClick={() => openLogin('登录管理员或专家账号后，即可使用导出/审计功能。')}>登录</button>}>
          运维操作需要服务器授予的导出、导入或审计权限。
        </EmptyState>
        : !canAccess
          ? <EmptyState title="无权访问" action={<Link href="/" className="button button--secondary">返回创作台</Link>}>
            当前账号没有导出、导入或审计权限。
          </EmptyState>
          : <>
            <div className="manager-tabs" role="tablist" aria-label="运维分类">
              {availableTabs.map((item) => <button
                type="button"
                role="tab"
                key={item.key}
                aria-selected={activeTab === item.key}
                className={`manager-tab${activeTab === item.key ? ' is-active' : ''}`}
                onClick={() => setTab(item.key)}
              >{item.label}</button>)}
            </div>

            {activeTab === 'export' && <section className="editor-panel" aria-labelledby="export-heading">
              <div className="panel-head">
                <h2 id="export-heading">导出备份</h2>
                <span className="mono">当前不按 ID 选择</span>
              </div>
              <form onSubmit={(event) => void runExport(event)}>
                <fieldset disabled={exportBusy}>
                  <legend className="field-label">导出类型</legend>
                  <div className="checklist checklist--inline">
                    {EXPORT_TYPES.map((item) => <label className="checklist-item" key={item.value}>
                      <input
                        type="checkbox"
                        checked={exportTypes.includes(item.value)}
                        onChange={() => setExportTypes((old) => old.includes(item.value)
                          ? old.filter((value) => value !== item.value)
                          : [...old, item.value])}
                      />
                      <span>{item.label}</span>
                    </label>)}
                  </div>
                </fieldset>
                <p className="field-note">导出包包含知识、概念、标签、规则、模板与作品 IR 快照；不包含 PNG，也不恢复作品公开状态。</p>
                <div className="form-actions">
                  <button type="submit" className="button button--primary" disabled={exportBusy}>{exportBusy ? '生成中…' : '生成导出包'}</button>
                </div>
              </form>
              {exportNotice && <InlineNotice tone="success">{exportNotice}</InlineNotice>}
              {exportError && <InlineNotice tone="error" title="导出失败">{exportError}</InlineNotice>}
              {exportPackage && <div className="ops-result">
                <div className="panel-head">
                  <h3>导出包摘要</h3>
                  <button type="button" className="button button--secondary button--small" onClick={downloadExport}>下载 JSON</button>
                </div>
                <dl className="label-list">
                  <div><dt>schemaVersion</dt><dd className="mono">{exportPackage.schemaVersion}</dd></div>
                  <div><dt>导出时间</dt><dd className="mono">{formatDateTime(exportPackage.exportedAt)}</dd></div>
                  <div><dt>包含图片</dt><dd>{exportPackage.manifest.includesArtifacts ? '是' : '否'}</dd></div>
                </dl>
                <div className="count-grid">
                  {Object.entries(exportPackage.manifest.counts).map(([key, value]) => <div className="count-item" key={key}>
                    <span className="mono">{key}</span><strong>{value}</strong>
                  </div>)}
                </div>
                <details className="tech-details">
                  <summary>查看导出包 JSON 摘要</summary>
                  <pre>{JSON.stringify({ schemaVersion: exportPackage.schemaVersion, exportedAt: exportPackage.exportedAt, manifest: exportPackage.manifest }, null, 2)}</pre>
                </details>
              </div>}
            </section>}

            {activeTab === 'import' && <section className="editor-panel" aria-labelledby="import-heading">
              <div className="panel-head">
                <h2 id="import-heading">导入备份</h2>
                <span className="mono">先校验，后事务执行</span>
              </div>
              <label className="field">
                <span>导入 JSON 文件（≤5MiB）</span>
                <input type="file" accept=".json,application/json" disabled={importBusy} onChange={(event) => void readFile(event)} />
              </label>
              {fileName && <p className="field-note">当前文件：{fileName}</p>}
              <label className="field">
                <span>或粘贴/编辑导入包 JSON</span>
                <textarea className="json-editor" rows={12} value={packageText} disabled={importBusy} onChange={(event) => { setPackageText(event.target.value); setValidation(null); setApplied(null); setParseError('') }} />
              </label>
              <p className="field-note">每类最多 100 条；workSnapshots 必须为空；当前导入不恢复 PNG 或公开作品。错误包不会部分写入。</p>
              {parseError && <InlineNotice tone="error" title="导入包格式错误">{parseError}</InlineNotice>}
              {importError && <InlineNotice tone="error" title="导入操作失败">{importError}</InlineNotice>}
              {importNotice && <InlineNotice tone={validation?.valid ? 'success' : 'info'}>{importNotice}</InlineNotice>}
              <div className="form-actions">
                <button type="button" className="button button--secondary" disabled={importBusy || !packageText.trim()} onClick={() => void validateImport()}>{importBusy ? '处理中…' : '校验导入包'}</button>
                <button type="button" className="button button--primary" disabled={importBusy || !validation?.valid || Boolean(applied)} onClick={() => void applyImport()}>{applied ? '已执行' : '执行导入'}</button>
              </div>
              {validation && <div className="ops-result">
                <dl className="label-list">
                  <div><dt>校验结果</dt><dd>{validation.valid ? '通过' : '未通过'}</dd></div>
                  <div><dt>validationId</dt><dd className="mono">{validation.validationId}</dd></div>
                  <div><dt>数据版本</dt><dd className="mono">{validation.expectedDataRevision}</dd></div>
                  <div><dt>过期时间</dt><dd className="mono">{formatDateTime(validation.expiresAt)}</dd></div>
                </dl>
                <div className="count-grid">
                  {Object.entries(validation.counts).map(([key, value]) => <div className="count-item" key={key}>
                    <span className="mono">{key}</span><strong>{value}</strong>
                  </div>)}
                </div>
                {issueList(validation.errors, 'error')}
              </div>}
              {applied && <div className="ops-result">
                <dl className="label-list">
                  <div><dt>importId</dt><dd className="mono">{applied.importId}</dd></div>
                  <div><dt>数据版本</dt><dd className="mono">{applied.dataRevision}</dd></div>
                </dl>
                <div className="count-grid">
                  {Object.entries(applied.counts).map(([key, value]) => <div className="count-item" key={key}>
                    <span className="mono">{key}</span><strong>{value}</strong>
                  </div>)}
                </div>
                {issueList(applied.warnings, 'success')}
              </div>}
            </section>}

            {activeTab === 'audit' && <section className="editor-panel" aria-labelledby="audit-heading">
              <div className="panel-head">
                <h2 id="audit-heading">审计日志</h2>
                <span className="mono">{auditError || !auditLogs ? '共 — 条 · 第 — / — 页' : `共 ${auditTotal} 条 · 第 ${auditPage} / ${auditTotalPages} 页`}</span>
              </div>
              <form className="search-filters" onSubmit={(event) => { event.preventDefault(); setAuditFilters(auditDraft); setAuditPage(1) }}>
                <div className="search-filters__grid">
                  <label className="field"><span>actorId</span><input value={auditDraft.actorId} onChange={(event) => setAuditDraft({ ...auditDraft, actorId: event.target.value })} /></label>
                  <label className="field"><span>action</span><input value={auditDraft.action} onChange={(event) => setAuditDraft({ ...auditDraft, action: event.target.value })} /></label>
                  <label className="field"><span>targetType</span><input value={auditDraft.targetType} onChange={(event) => setAuditDraft({ ...auditDraft, targetType: event.target.value })} /></label>
                  <label className="field"><span>requestId</span><input value={auditDraft.requestId} onChange={(event) => setAuditDraft({ ...auditDraft, requestId: event.target.value })} /></label>
                  <label className="field"><span>开始时间</span><input type="datetime-local" value={auditDraft.fromTime} onChange={(event) => setAuditDraft({ ...auditDraft, fromTime: event.target.value })} /></label>
                  <label className="field"><span>结束时间</span><input type="datetime-local" value={auditDraft.toTime} onChange={(event) => setAuditDraft({ ...auditDraft, toTime: event.target.value })} /></label>
                </div>
                <div className="form-actions">
                  <button type="submit" className="button button--primary button--small">查询日志</button>
                  <button type="button" className="button button--secondary button--small" onClick={() => { const cleared = { ...emptyFilters }; setAuditDraft(cleared); setAuditFilters(cleared); setAuditPage(1) }}>清除条件</button>
                  <button type="button" className="button button--secondary button--small" disabled={auditLoading} onClick={() => setAuditAttempt((value) => value + 1)}>刷新</button>
                </div>
              </form>
              {auditError
                ? <div className="works-error">
                  <InlineNotice tone="error" title="审计日志加载失败">
                    无法读取审计日志。请查看技术详情；若后端已启动，可能是 `/audit-logs` 接口尚未实现。
                    <details className="notice__details">
                      <summary>查看技术详情</summary>
                      <p className="notice__detail">{auditError}</p>
                    </details>
                  </InlineNotice>
                  <button type="button" className="button button--secondary" onClick={() => setAuditAttempt((value) => value + 1)}>重新加载</button>
                </div>
                : auditLoading && !auditLogs
                  ? <LoadingLines label="正在加载审计日志" />
                  : !auditLogs || auditLogs.length === 0
                    ? <EmptyState title="没有匹配的审计日志">调整 actor/action/targetType/requestId 或时间范围后重试。</EmptyState>
                    : <ul className="manager-list audit-list">
                      {auditLogs.map((log) => <li className="manager-row audit-row" key={log.id}>
                        <div className="manager-row__body">
                          <div className="audit-row__head">
                            <span className={`audit-result audit-result--${log.result}`}>{log.result === 'success' ? '成功' : '失败'}</span>
                            <strong>{log.action}</strong>
                            <span className="mono">{formatDateTime(log.createdAt)}</span>
                          </div>
                          <p className="mono">actor {log.actorId ? shortId(log.actorId) : '—'} · {log.actorRole ?? '—'} · target {log.targetType}{log.targetId ? `/${shortId(log.targetId)}` : ''}</p>
                          <p className="mono">requestId {log.requestId}{log.errorCode ? ` · error ${log.errorCode}` : ''}</p>
                          <details className="tech-details">
                            <summary>查看输入与变更摘要</summary>
                            <pre>{JSON.stringify({ inputSummary: log.inputSummary, before: log.before, after: log.after }, null, 2)}</pre>
                          </details>
                        </div>
                      </li>)}
                    </ul>}
              {auditLogs && auditLogs.length > 0 && <nav className="pager" aria-label="审计日志分页">
                <button type="button" className="button button--secondary button--small" disabled={auditPage <= 1} onClick={() => setAuditPage((value) => value - 1)}>上一页</button>
                <span className="mono">第 {auditPage} / {auditTotalPages} 页</span>
                <button type="button" className="button button--secondary button--small" disabled={auditPage >= auditTotalPages} onClick={() => setAuditPage((value) => value + 1)}>下一页</button>
              </nav>}
            </section>}
          </>}
  </section>
}
