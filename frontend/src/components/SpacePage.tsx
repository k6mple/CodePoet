'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import type { CreationSession, Page, Snapshot, SnapshotSaved, Work } from '@/types/core'
import { formatDateTime, roleLabel, shortId } from '@/utils/format'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'

type Tab = 'works' | 'favorites' | 'sessions' | 'profile'

const PAGE_SIZE = 20

function WorkPanel({ scope }: { scope: 'mine' | 'favorites' }) {
  const [works, setWorks] = useState<Work[] | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [busyId, setBusyId] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setWorks(null)
    request<Page<Work>>(`/works?scope=${scope}&page=${page}&pageSize=${PAGE_SIZE}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return
        setWorks(data.items)
        setTotal(data.total)
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [scope, page, attempt])

  async function toggleFavorite(work: Work) {
    if (busyId) return
    const next = !work.isFavorite
    setBusyId(work.id)
    setActionError('')
    try {
      await request<{ workId: string; isFavorite: boolean }>(`/works/${encodeURIComponent(work.id)}/favorite`, {
        method: next ? 'PUT' : 'DELETE',
      })
      setWorks((old) => {
        if (!old) return old
        if (scope === 'favorites' && !next) return old.filter((item) => item.id !== work.id)
        return old.map((item) => item.id === work.id ? { ...item, isFavorite: next } : item)
      })
      if (scope === 'favorites' && !next) setTotal((value) => Math.max(0, value - 1))
    } catch (cause) {
      setActionError(errorMessage(cause))
    } finally {
      setBusyId('')
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const title = scope === 'favorites' ? '收藏的作品' : '我的作品'
  const emptyTitle = scope === 'favorites' ? '还没有收藏作品' : '还没有保存的作品'

  return <section className="editor-panel" aria-labelledby={`space-${scope}-heading`}>
    <div className="panel-head">
      <h2 id={`space-${scope}-heading`}>{title}</h2>
      <span className="mono">{error || !works ? '共 — 件 · 第 — / — 页' : `共 ${total} 件 · 第 ${page} / ${totalPages} 页`}</span>
    </div>
    {actionError && <InlineNotice tone="error" title="收藏操作失败">{actionError}</InlineNotice>}
    {error
      ? <div className="works-error">
        <InlineNotice tone="error" title="作品列表加载失败">
          无法读取{title}。请查看技术详情；若后端已启动，可能是作品接口尚未实现。
          <details className="notice__details">
            <summary>查看技术详情</summary>
            <p className="notice__detail">{error}</p>
          </details>
        </InlineNotice>
        <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
      </div>
      : loading && !works
        ? <LoadingLines label={`正在加载${title}`} />
        : !works || works.length === 0
          ? <EmptyState title={emptyTitle} action={scope === 'mine' ? <Link href="/" className="button button--secondary button--small">前往创作台</Link> : undefined}>
            {scope === 'mine' ? '保存作品后会出现在个人空间。' : '在作品列表或详情中收藏作品后会出现在这里。'}
          </EmptyState>
          : <ul className="manager-list">
            {works.map((work) => <li className="manager-row" key={work.id}>
              <div className="manager-row__body">
                <h3><Link href={`/works/versions/${encodeURIComponent(work.displayVersionId)}`}>{work.title || '未命名作品'}</Link></h3>
                <p>{work.description || '暂无说明'}</p>
                <div className="tag-line">
                  <span className="mono">{formatDateTime(work.createdAt)}</span>
                  {work.isFavorite && <span className="tag-chip">已收藏</span>}
                </div>
              </div>
              <div className="manager-row__side">
                <button type="button" className="button button--secondary button--small" disabled={busyId === work.id} onClick={() => void toggleFavorite(work)}>
                  {busyId === work.id ? '处理中…' : work.isFavorite ? '取消收藏' : '收藏'}
                </button>
                <Link href={`/works/versions/${encodeURIComponent(work.displayVersionId)}`} className="button button--secondary button--small">打开作品</Link>
              </div>
            </li>)}
          </ul>}
    {works && works.length > 0 && <nav className="pager" aria-label={`${title}分页`}>
      <button type="button" className="button button--secondary button--small" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
      <span className="mono">第 {page} / {totalPages} 页</span>
      <button type="button" className="button button--secondary button--small" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
    </nav>}
  </section>
}

function SessionsPanel() {
  const [sessions, setSessions] = useState<CreationSession[] | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [selected, setSelected] = useState<CreationSession | null>(null)
  const [snapshots, setSnapshots] = useState<Snapshot[] | null>(null)
  const [snapshotsLoading, setSnapshotsLoading] = useState(false)
  const [snapshotsError, setSnapshotsError] = useState('')
  const [snapshotsAttempt, setSnapshotsAttempt] = useState(0)
  const [restoreBusy, setRestoreBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [restoreError, setRestoreError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setSessions(null)
    request<Page<CreationSession>>(`/sessions?page=${page}&pageSize=${PAGE_SIZE}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return
        setSessions(data.items)
        setTotal(data.total)
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [page, attempt])

  useEffect(() => {
    if (!selected) {
      setSnapshots(null)
      setSnapshotsError('')
      return
    }
    const controller = new AbortController()
    setSnapshotsLoading(true)
    setSnapshotsError('')
    request<Page<Snapshot>>(`/sessions/${encodeURIComponent(selected.id)}/snapshots?page=1&pageSize=100`, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setSnapshots(data.items)
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setSnapshotsError(errorMessage(cause))
      })
      .finally(() => {
        if (!controller.signal.aborted) setSnapshotsLoading(false)
      })
    return () => controller.abort()
  }, [selected?.id, snapshotsAttempt])

  async function restore(snapshot: Snapshot) {
    if (!selected || restoreBusy) return
    if (!window.confirm(`恢复到快照 ${shortId(snapshot.id)}？恢复会追加新快照，不覆盖历史。`)) return
    setRestoreBusy(snapshot.id)
    setRestoreError('')
    setNotice('')
    try {
      const result = await request<SnapshotSaved>(`/sessions/${encodeURIComponent(selected.id)}/restore`, {
        method: 'POST',
        body: JSON.stringify({ expectedRevision: selected.revision, snapshotId: snapshot.id }),
      })
      setSelected(result.session)
      setNotice(`已恢复到快照 ${shortId(snapshot.id)}；恢复前后快照都会保留。`)
      setSnapshotsAttempt((value) => value + 1)
      setAttempt((value) => value + 1)
    } catch (cause) {
      setRestoreError(errorMessage(cause))
    } finally {
      setRestoreBusy('')
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return <section className="editor-panel" aria-labelledby="space-sessions-heading">
    <div className="panel-head">
      <h2 id="space-sessions-heading">创作会话</h2>
      <span className="mono">{error || !sessions ? '共 — 个 · 第 — / — 页' : `共 ${total} 个 · 第 ${page} / ${totalPages} 页`}</span>
    </div>
    {notice && <InlineNotice tone="success">{notice}</InlineNotice>}
    {error
      ? <div className="works-error">
        <InlineNotice tone="error" title="会话列表加载失败">
          无法读取创作会话。请查看技术详情；若后端已启动，可能是 `/sessions` 接口尚未实现。
          <details className="notice__details">
            <summary>查看技术详情</summary>
            <p className="notice__detail">{error}</p>
          </details>
        </InlineNotice>
        <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
      </div>
      : loading && !sessions
        ? <LoadingLines label="正在加载创作会话" />
        : !sessions || sessions.length === 0
          ? <EmptyState title="还没有创作会话">
            在创作台点击“保存进度”会创建会话；每次保存由服务器追加快照。
          </EmptyState>
          : <div className="space-layout">
            <div className="space-session-list">
              <ul className="manager-list">
                {sessions.map((session) => <li className={`manager-row${selected?.id === session.id ? ' is-selected' : ''}`} key={session.id}>
                  <button type="button" className="space-session-main" disabled={Boolean(restoreBusy)}
                    onClick={() => { setSelected(session); setRestoreError(''); setNotice('') }}>
                    <strong>{session.name || '未命名创作现场'}</strong>
                    <span className="mono">更新 {formatDateTime(session.updatedAt)} · rev {session.revision}</span>
                    <span className="mono">最新快照 {shortId(session.latestSnapshotId)}</span>
                  </button>
                  <div className="manager-row__side">
                    <span className="muted-text">会话 {shortId(session.id)}</span>
                  </div>
                </li>)}
              </ul>
              <nav className="pager" aria-label="会话分页">
                <button type="button" className="button button--secondary button--small" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
                <span className="mono">第 {page} / {totalPages} 页</span>
                <button type="button" className="button button--secondary button--small" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
              </nav>
            </div>
            <aside className="space-detail" aria-labelledby="space-snapshots-heading">
              <div className="panel-head">
                <h3 id="space-snapshots-heading">{selected ? selected.name || '会话快照' : '选择会话'}</h3>
                {selected && <span className="mono">rev {selected.revision}</span>}
              </div>
              {!selected
                ? <p className="panel-note">从左侧选择一个创作会话，查看历史快照并恢复。</p>
                : <>
                  <div className="form-actions">
                    <Link href={`/?session=${encodeURIComponent(selected.id)}`} className="button button--primary button--small">在创作台继续</Link>
                  </div>
                  <p className="panel-note">恢复会复制旧现场为新当前态，并保留恢复前后的历史快照。</p>
                  {restoreError && <InlineNotice tone="error" title="恢复失败">{restoreError}</InlineNotice>}
                  {snapshotsLoading
                    ? <LoadingLines label="正在读取会话快照" />
                    : snapshotsError
                      ? <InlineNotice tone="error" title="快照加载失败">{snapshotsError}</InlineNotice>
                      : !snapshots || snapshots.length === 0
                        ? <p className="panel-note">该会话还没有快照。</p>
                        : <ul className="snapshot-list">
                          {snapshots.map((snapshot) => <li className="snapshot-row" key={snapshot.id}>
                            <div className="snapshot-row__head">
                              <strong>{formatDateTime(snapshot.createdAt)}</strong>
                              <span className="mono">rev {snapshot.sessionRevision} · {shortId(snapshot.id)}</span>
                            </div>
                            <p>{snapshot.name || snapshot.note || '无说明'}</p>
                            {snapshot.restoredFromSnapshotId && <p className="panel-note">恢复自 {shortId(snapshot.restoredFromSnapshotId)}</p>}
                            <details className="tech-details">
                              <summary>查看快照状态</summary>
                              <pre>{JSON.stringify(snapshot.state, null, 2)}</pre>
                            </details>
                            <div className="form-actions">
                              <button type="button" className="button button--secondary button--small" disabled={Boolean(restoreBusy)} onClick={() => void restore(snapshot)}>
                                {restoreBusy === snapshot.id ? '恢复中…' : '恢复到此快照'}
                              </button>
                            </div>
                          </li>)}
                        </ul>}
                </>}
            </aside>
          </div>}
  </section>
}

export default function SpacePage() {
  const { user, loading, openLogin } = useSession()
  const [tab, setTab] = useState<Tab>('works')

  useEffect(() => {
    const value = new URLSearchParams(window.location.search).get('tab')
    if (value === 'works' || value === 'favorites' || value === 'sessions' || value === 'profile') setTab(value)
  }, [])

  function changeTab(next: Tab) {
    setTab(next)
    if (typeof window !== 'undefined') window.history.replaceState(null, '', `/space?tab=${next}`)
  }

  return <section className="space-page" aria-labelledby="space-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="space-heading">个人空间</h1>
        <p>作品、收藏、创作会话与历史快照。保存进度与保存作品是两种不同操作。</p>
      </div>
    </header>

    {loading
      ? <LoadingLines label="正在恢复登录" />
      : !user
        ? <EmptyState
          title="登录后进入个人空间"
          action={<button type="button" className="button button--primary" onClick={() => openLogin('登录后即可查看作品、收藏和创作会话。')}>登录</button>}
        >
          个人空间中的数据保存在服务器上，不依赖当前浏览器。
        </EmptyState>
        : <>
          <div className="manager-tabs" role="tablist" aria-label="个人空间分类">
            {([['works', '作品'], ['favorites', '收藏'], ['sessions', '创作会话'], ['profile', '个人资料']] as const).map(([key, label]) => <button
              type="button"
              role="tab"
              key={key}
              className={`manager-tab${tab === key ? ' is-active' : ''}`}
              aria-selected={tab === key}
              onClick={() => changeTab(key)}
            >{label}</button>)}
          </div>
          {tab === 'works' && <WorkPanel scope="mine" />}
          {tab === 'favorites' && <WorkPanel scope="favorites" />}
          {tab === 'sessions' && <SessionsPanel />}
          {tab === 'profile' && <section className="editor-panel" aria-labelledby="space-profile-heading">
            <div className="panel-head">
              <h2 id="space-profile-heading">个人资料</h2>
              <span className="mono">{roleLabel(user.role)}</span>
            </div>
            <p className="panel-note">{user.nickname || user.username} · @{user.username}</p>
            <p className="panel-note">{user.bio || '还没有填写简介。'}</p>
            <div className="form-actions">
              <Link href="/account" className="button button--primary button--small">打开账号与资料</Link>
            </div>
          </section>}
        </>}
  </section>
}
