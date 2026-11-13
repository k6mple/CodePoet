'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import type { Page, ReviewCreateInput, ReviewQueueItem, TemplateVersion, WorkDetail } from '@/types/core'
import { formatDateTime, parameterTypeLabel, parameterValueLabel, shortId } from '@/utils/format'
import { reviewDecisionLabel, reviewQueueStatusLabel, reviewTargetLabel } from '@/utils/template'
import CanvasPreview from './CanvasPreview'
import { historicalBackend } from '@/renderers/backend'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'

function isPending(item: ReviewQueueItem) {
  return item.status === 'submitted' || item.status === 'pending_review'
}

export default function ReviewDesk() {
  const { user, loading, openLogin } = useSession()
  const [items, setItems] = useState<ReviewQueueItem[] | null>(null)
  const [total, setTotal] = useState(0)
  const [fetching, setFetching] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [filter, setFilter] = useState<'pending' | 'processed'>('pending')
  const [selected, setSelected] = useState<ReviewQueueItem | null>(null)
  const [target, setTarget] = useState<TemplateVersion | WorkDetail | null>(null)
  const [targetLoading, setTargetLoading] = useState(false)
  const [targetError, setTargetError] = useState('')
  const [frameUrl, setFrameUrl] = useState('')
  const [frameError, setFrameError] = useState('')
  const [reason, setReason] = useState('')
  const [deciding, setDeciding] = useState(false)
  const [decisionError, setDecisionError] = useState('')
  const [notice, setNotice] = useState('')

  const canReview = Boolean(user?.permissions.includes('reviews.decide'))
  const pendingItems = useMemo(() => items?.filter(isPending) ?? [], [items])
  const processedItems = useMemo(() => items?.filter((item) => !isPending(item)) ?? [], [items])
  const visibleItems = filter === 'pending' ? pendingItems : processedItems
  const templateTarget = target && 'templateId' in target ? target as TemplateVersion : null
  const workTarget = target && 'workVersion' in target ? target as WorkDetail : null
  const canDecide = Boolean(selected && isPending(selected) && selected.canReview && !targetLoading && (templateTarget || workTarget))

  useEffect(() => {
    if (!user || !canReview) return
    const controller = new AbortController()
    setFetching(true)
    setError('')
    setItems(null)
    request<Page<ReviewQueueItem>>('/reviews?page=1&pageSize=100', { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return
        setItems(data.items)
        setTotal(data.total)
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause))
      })
      .finally(() => {
        if (!controller.signal.aborted) setFetching(false)
      })
    return () => controller.abort()
  }, [user, canReview, attempt])

  useEffect(() => {
    setSelected(null)
    setReason('')
    setDecisionError('')
  }, [filter])

  useEffect(() => {
    if (!selected) {
      setTarget(null)
      setTargetError('')
      setTargetLoading(false)
      setFrameError('')
      setFrameUrl('')
      return
    }
    const controller = new AbortController()
    let createdUrl = ''
    setTarget(null)
    setTargetError('')
    setTargetLoading(true)
    setFrameError('')
    setFrameUrl('')
    setDecisionError('')
    setNotice('')
    async function load() {
      try {
        if (selected!.targetType === 'template_version') {
          const version = await request<TemplateVersion>(`/template-versions/${encodeURIComponent(selected!.targetId)}`, { signal: controller.signal })
          if (!controller.signal.aborted) setTarget(version)
        } else {
          const detail = await request<WorkDetail>(`/work-versions/${encodeURIComponent(selected!.targetId)}`, { signal: controller.signal })
          if (controller.signal.aborted) return
          setTarget(detail)
          const frames = detail.renderJob.manifest?.frames ?? []
          const last = frames[frames.length - 1]
          if (!last) {
            setFrameError('该作品没有成功保存的 PNG 帧。')
            return
          }
          try {
            const blob = await request<Blob>(`/artifacts/${encodeURIComponent(last.artifactId)}`, {
              signal: controller.signal, headers: { Accept: 'image/png' },
            })
            if (controller.signal.aborted) return
            createdUrl = URL.createObjectURL(blob)
            setFrameUrl(createdUrl)
          } catch (cause) {
            if (!controller.signal.aborted) setFrameError(errorMessage(cause))
          }
        }
      } catch (cause) {
        if (!controller.signal.aborted) setTargetError(errorMessage(cause))
      } finally {
        if (!controller.signal.aborted) setTargetLoading(false)
      }
    }
    void load()
    return () => {
      controller.abort()
      if (createdUrl) URL.revokeObjectURL(createdUrl)
    }
  }, [selected?.targetId, selected?.targetType])

  async function decide(decision: 'approve' | 'reject') {
    if (!selected || deciding || !selected.canReview || !isPending(selected)) return
    const trimmed = reason.trim()
    if (decision === 'reject' && !trimmed) {
      setDecisionError('退回必须填写理由。')
      return
    }
    const payload: ReviewCreateInput = {
      targetType: selected.targetType,
      targetId: selected.targetId,
      expectedRevision: selected.targetRevision,
      decision,
      reason: trimmed || '审核通过',
    }
    setDeciding(true)
    setDecisionError('')
    setNotice('')
    try {
      await request('/reviews', { method: 'POST', body: JSON.stringify(payload) })
      setNotice(decision === 'approve' ? '审核通过，目标版本已进入发布流程。' : '已退回，作者可以修订后重新提交。')
      setSelected(null)
      setReason('')
      setAttempt((value) => value + 1)
    } catch (cause) {
      setDecisionError(errorMessage(cause))
    } finally {
      setDeciding(false)
    }
  }

  return <section className="review-page" aria-labelledby="review-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="review-heading">审核台</h1>
        <p>审核模板版本和作品版本。不能审核自己的提交；退回时必须说明原因。</p>
      </div>
    </header>

    {loading
      ? <LoadingLines label="正在恢复登录" />
      : !user
        ? <EmptyState
          title="登录后进入审核台"
          action={<button type="button" className="button button--primary" onClick={() => openLogin('登录领域专家或管理员账号后，即可审核提交。')}>登录</button>}
        >
          审核台只对具有 `reviews.decide` 权限的账号开放。
        </EmptyState>
        : !canReview
          ? <EmptyState title="无权访问" action={<Link href="/" className="button button--secondary">返回创作台</Link>}>
            当前账号没有审核权限。请使用领域专家或管理员账号。
          </EmptyState>
          : <>
            {notice && <InlineNotice tone="success">{notice}</InlineNotice>}
            <div className="manager-toolbar">
              <div className="manager-tabs manager-tabs--inline" role="group" aria-label="审核记录筛选">
                <button type="button" className={`manager-tab${filter === 'pending' ? ' is-active' : ''}`} aria-pressed={filter === 'pending'} onClick={() => setFilter('pending')}>待审核 {items ? pendingItems.length : '—'}</button>
                <button type="button" className={`manager-tab${filter === 'processed' ? ' is-active' : ''}`} aria-pressed={filter === 'processed'} onClick={() => setFilter('processed')}>已处理 {items ? processedItems.length : '—'}</button>
              </div>
              <div className="manager-toolbar__actions">
                <span className="mono review-count">共 {items ? total : '—'} 条</span>
                <button type="button" className="button button--secondary button--small" disabled={fetching} onClick={() => setAttempt((value) => value + 1)}>刷新</button>
              </div>
            </div>

            {error
              ? <div className="works-error">
                <InlineNotice tone="error" title="审核队列加载失败">
                  无法读取审核队列。请查看技术详情；若后端已启动，可能是 `/reviews` 接口尚未实现。
                  <details className="notice__details">
                    <summary>查看技术详情</summary>
                    <p className="notice__detail">{error}</p>
                  </details>
                </InlineNotice>
                <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
              </div>
              : !items
                ? <LoadingLines label="正在加载审核队列" />
                : <div className="review-layout">
                  <section className="review-queue" aria-labelledby="review-queue-heading">
                    <div className="panel-head">
                      <h2 id="review-queue-heading">{filter === 'pending' ? '待审核队列' : '已处理记录'}</h2>
                      <span className="mono">{visibleItems.length} 条</span>
                    </div>
                    {visibleItems.length === 0
                      ? <EmptyState title={filter === 'pending' ? '没有待审核的提交' : '还没有已处理记录'}>
                        {filter === 'pending' ? '新的模板或作品送审后会出现在这里。' : '完成一次审核后，记录会出现在这里。'}
                      </EmptyState>
                      : <ul className="review-list">
                        {visibleItems.map((item) => <li className={`review-row${selected?.targetType === item.targetType && selected?.targetId === item.targetId ? ' is-selected' : ''}`} key={`${item.targetType}-${item.targetId}`}>
                          <button type="button" className="review-row__main" onClick={() => { setSelected(item); setReason(''); setDecisionError('') }}>
                            <span className="mono review-row__type">{reviewTargetLabel(item.targetType)}</span>
                            <strong>{item.title || '未命名提交'}</strong>
                            <span className="mono review-row__meta">提交者 {shortId(item.submittedBy)} · {formatDateTime(item.submittedAt)}</span>
                          </button>
                          <div className="review-row__side">
                            <span className={`status-chip is-${item.status}`}>{reviewQueueStatusLabel(item.status)}</span>
                            {isPending(item) && !item.canReview && <span className="muted-text">不能自审</span>}
                          </div>
                        </li>)}
                      </ul>}
                    {total > 100 && <p className="panel-note">当前仅加载前 100 条；更多记录需要后端分页支持。</p>}
                  </section>

                  <aside className="review-detail" aria-labelledby="review-detail-heading">
                    <div className="panel-head">
                      <h2 id="review-detail-heading">审核详情</h2>
                      {selected && <span className="mono">{reviewTargetLabel(selected.targetType)} · rev {selected.targetRevision}</span>}
                    </div>
                    {!selected
                      ? <p className="panel-note">从左侧队列选择一条提交，查看目标内容后作出决定。</p>
                      : <>
                        <div className="review-target-head">
                          <h3>{selected.title || '未命名提交'}</h3>
                          <span className={`status-chip is-${selected.status}`}>{reviewQueueStatusLabel(selected.status)}</span>
                        </div>
                        <p className="panel-note">提交者 {shortId(selected.submittedBy)} · 提交于 {formatDateTime(selected.submittedAt)}</p>
                        {targetLoading
                          ? <LoadingLines label="正在读取审核目标" />
                          : targetError
                            ? <InlineNotice tone="error" title="目标详情加载失败">{targetError}</InlineNotice>
                            : templateTarget
                              ? <div className="review-target">
                                <p className="review-target__name">{templateTarget.metadata.name} · v{templateTarget.versionNumber}</p>
                                <div className="version-preview"><CanvasPreview ir={templateTarget.irTemplate} backend={templateTarget.supportedBackends[0]} timeMs={templateTarget.irTemplate.durationMs} /></div>
                                <p className="plate-caption">模板原始 IR，未应用参数与绑定。</p>
                                <dl className="label-list">
                                  <div><dt>变更说明</dt><dd>{templateTarget.changeNote || '—'}</dd></div>
                                  <div><dt>参数</dt><dd>{templateTarget.parameterSchema.length} 项</dd></div>
                                  <div><dt>绑定</dt><dd>{templateTarget.bindings.length} 项</dd></div>
                                </dl>
                                {templateTarget.parameterSchema.length > 0 && <ul className="parameter-list">
                                  {templateTarget.parameterSchema.map((parameter) => <li className="parameter-row" key={parameter.name}>
                                    <div className="parameter-row__head"><strong>{parameter.label}</strong><span className="mono">{parameter.name} · {parameterTypeLabel(parameter.type)}</span></div>
                                    <div className="parameter-row__meta"><span>默认 {parameterValueLabel(parameter.default)}</span></div>
                                  </li>)}
                                </ul>}
                              </div>
                              : workTarget
                                ? <div className="review-target">
                                  <p className="review-target__name">{workTarget.workVersion.title} · v{workTarget.workVersion.versionNumber}</p>
                                  {frameUrl
                                    ? <div className="review-frame"><img src={frameUrl} alt="作品审核帧" /></div>
                                    : <p className="panel-note">{frameError || '正在读取作品帧…'}</p>}
                                  <div className="version-preview"><CanvasPreview ir={workTarget.generation.ir} backend={historicalBackend(workTarget)} timeMs={workTarget.generation.ir.durationMs} /></div>
                                  <p className="plate-caption">上：服务器保存的 PNG；下：按保存 IR 重绘。</p>
                                  <dl className="label-list">
                                    <div><dt>变更说明</dt><dd>{workTarget.workVersion.changeNote || '—'}</dd></div>
                                    <div><dt>作品状态</dt><dd>{reviewQueueStatusLabel(workTarget.workVersion.status)}</dd></div>
                                    <div><dt>生成编号</dt><dd className="mono">{shortId(workTarget.generation.id)}</dd></div>
                                  </dl>
                                </div>
                                : null}
                        {selected.latestReview && <InlineNotice tone={selected.latestReview.decision === 'reject' ? 'error' : 'success'}>
                          {reviewDecisionLabel(selected.latestReview.decision)}：{selected.latestReview.reason || '未填写理由'}
                          <span className="mono"> · {formatDateTime(selected.latestReview.createdAt)}</span>
                        </InlineNotice>}
                        {isPending(selected) && !selected.canReview
                          ? <InlineNotice tone="info" title="不能审核自己的提交">服务端会拒绝自审；请等待其他审核人处理。</InlineNotice>
                          : isPending(selected)
                            ? <div className="review-decision">
                              <label className="field">
                                <span>审核理由</span>
                                <textarea rows={4} maxLength={500} value={reason} disabled={deciding} onChange={(event) => setReason(event.target.value)} />
                              </label>
                              <p className="field-note">退回必须填写理由；通过时可留空，将记录为“审核通过”。</p>
                              {decisionError && <InlineNotice tone="error" title="审核失败">{decisionError}</InlineNotice>}
                              <div className="decision-actions">
                                <button type="button" className="button button--primary" disabled={deciding || !canDecide} onClick={() => void decide('approve')}>{deciding ? '提交中…' : '通过'}</button>
                                <button type="button" className="button button--secondary" disabled={deciding || !canDecide} onClick={() => void decide('reject')}>退回</button>
                              </div>
                              {!canDecide && !targetLoading && <p className="panel-note">请在目标内容加载成功后再作决定。</p>}
                            </div>
                            : <p className="panel-note">该提交已处理，当前只读。</p>}
                      </>}
                  </aside>
                </div>}
          </>}
  </section>
}
