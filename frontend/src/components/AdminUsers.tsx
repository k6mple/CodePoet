'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import type { Page, RoleDecisionInput, User } from '@/types/core'
import { accountStatusLabel, applicationStatusLabel, formatDateTime, roleLabel } from '@/utils/format'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'

const PAGE_SIZE = 20

export default function AdminUsers() {
  const { user, loading, openLogin } = useSession()
  const [users, setUsers] = useState<User[] | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [applicationFilter, setApplicationFilter] = useState<'pending' | 'all'>('pending')
  const [searchInput, setSearchInput] = useState('')
  const [query, setQuery] = useState('')
  const [fetching, setFetching] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [selected, setSelected] = useState<User | null>(null)
  const [reason, setReason] = useState('')
  const [deciding, setDeciding] = useState(false)
  const [decisionError, setDecisionError] = useState('')
  const [notice, setNotice] = useState('')

  const canApprove = Boolean(user?.permissions.includes('users.approveRole'))

  useEffect(() => {
    if (!user || !canApprove) return
    const controller = new AbortController()
    setFetching(true)
    setError('')
    setUsers(null)
    const params = new URLSearchParams({ page: String(page), pageSize: String(PAGE_SIZE) })
    if (query) params.set('q', query)
    if (applicationFilter === 'pending') params.set('applicationStatus', 'pending')
    request<Page<User>>(`/admin/users?${params.toString()}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return
        setUsers(data.items)
        setTotal(data.total)
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause))
      })
      .finally(() => {
        if (!controller.signal.aborted) setFetching(false)
      })
    return () => controller.abort()
  }, [user, canApprove, page, query, applicationFilter, attempt])

  function selectUser(item: User) {
    setSelected(item)
    setReason('')
    setDecisionError('')
    setNotice('')
  }

  async function decide(approved: boolean) {
    if (!selected || !selected.roleApplication || deciding || !user) return
    if (selected.id === user.id) {
      setDecisionError('不能审批自己的角色申请。')
      return
    }
    const trimmed = reason.trim()
    if (!trimmed) {
      setDecisionError('请填写审批理由。')
      return
    }
    setDeciding(true)
    setDecisionError('')
    setNotice('')
    const payload: RoleDecisionInput = {
      expectedRevision: selected.revision,
      approved,
      role: selected.roleApplication.requestedRole,
      reason: trimmed,
    }
    try {
      await request<User>(`/admin/users/${encodeURIComponent(selected.id)}/role-decision`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setNotice(approved
        ? `已批准 ${selected.username} 的${roleLabel(payload.role)}申请。`
        : `已拒绝 ${selected.username} 的角色申请。`)
      setSelected(null)
      setReason('')
      setAttempt((value) => value + 1)
    } catch (cause) {
      setDecisionError(errorMessage(cause))
    } finally {
      setDeciding(false)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return <section className="admin-page" aria-labelledby="admin-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="admin-heading">用户与角色审批</h1>
        <p>查看角色申请并作出批准或拒绝决定。管理员不能审批自己的申请。</p>
      </div>
    </header>

    {loading
      ? <LoadingLines label="正在恢复登录" />
      : !user
        ? <EmptyState
          title="登录后查看用户管理"
          action={<button type="button" className="button button--primary" onClick={() => openLogin('登录管理员账号后即可审批角色申请。')}>登录</button>}
        >
          用户与角色审批仅对具有 `users.approveRole` 权限的账号开放。
        </EmptyState>
        : !canApprove
          ? <EmptyState
            title="无权访问"
            action={<Link href="/" className="button button--secondary">返回创作台</Link>}
          >
            当前账号没有审批角色申请的权限。请使用管理员账号，或由管理员授予权限。
          </EmptyState>
          : <>
            {notice && <InlineNotice tone="success">{notice}</InlineNotice>}
            <div className="manager-toolbar">
              <form className="manager-search" onSubmit={(event) => {
                event.preventDefault()
                setQuery(searchInput.trim())
                setPage(1)
              }}>
                <label className="field">
                  <span>搜索用户</span>
                  <input
                    value={searchInput}
                    placeholder="用户名或昵称"
                    maxLength={80}
                    onChange={(event) => setSearchInput(event.target.value)}
                  />
                </label>
                <button type="submit" className="button button--secondary button--small">搜索</button>
              </form>
              <label className="field manager-filter">
                <span>显示范围</span>
                <select value={applicationFilter} onChange={(event) => {
                  setApplicationFilter(event.target.value as 'pending' | 'all')
                  setPage(1)
                }}>
                  <option value="pending">仅待审批</option>
                  <option value="all">全部用户</option>
                </select>
              </label>
              <button type="button" className="button button--secondary button--small" disabled={fetching} onClick={() => setAttempt((value) => value + 1)}>刷新</button>
            </div>

            {error
              ? <div className="works-error">
                <InlineNotice tone="error" title="用户列表加载失败">
                  无法读取用户与角色申请。后端可能尚未实现 `/admin/users`。
                  <details className="notice__details">
                    <summary>查看技术详情</summary>
                    <p className="notice__detail">{error}</p>
                  </details>
                </InlineNotice>
                <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
              </div>
              : !users
                ? <UserSkeleton />
                : users.length === 0
                  ? <EmptyState title={applicationFilter === 'pending' ? '没有待审批的角色申请' : '没有匹配的用户'}>
                    {applicationFilter === 'pending'
                      ? '当前没有需要处理的角色申请。可以切换到“全部用户”查看账号目录。'
                      : '尝试清空搜索词或切换显示范围。'}
                  </EmptyState>
                  : <div className="admin-layout">
                    <section className="user-panel" aria-labelledby="user-list-heading">
                      <div className="panel-head">
                        <h2 id="user-list-heading">用户</h2>
                        <span className="mono">共 {total} 个 · 第 {page} / {totalPages} 页</span>
                      </div>
                      <ul className="user-list">
                        {users.map((item) => {
                          const pending = item.roleApplication?.status === 'pending'
                          return <li className={`user-row${pending ? ' is-pending' : ''}`} key={item.id}>
                            <div className="user-row__identity">
                              <strong>{item.nickname || item.username}</strong>
                              <span className="mono">@{item.username}</span>
                            </div>
                            <div className="user-row__role">
                              <span className={`role-tag role-${item.role}`}>{roleLabel(item.role)}</span>
                              <span className="mono">{accountStatusLabel(item.status)}</span>
                            </div>
                            <div className="user-row__application">
                              {item.roleApplication
                                ? <>
                                  <span>{roleLabel(item.roleApplication.requestedRole)} · {applicationStatusLabel(item.roleApplication.status)}</span>
                                  {item.roleApplication.reason && <small>{item.roleApplication.reason}</small>}
                                </>
                                : <span className="muted-text">无角色申请</span>}
                            </div>
                            <div className="user-row__meta mono">
                              <span>rev {item.revision}</span>
                              <span>{formatDateTime(item.updatedAt)}</span>
                            </div>
                            <div className="user-row__actions">
                              {pending
                                ? item.id === user.id
                                  ? <span className="muted-text">不能审批自己</span>
                                  : <button type="button" className="button button--secondary button--small" onClick={() => selectUser(item)}>审批</button>
                                : null}
                            </div>
                          </li>
                        })}
                      </ul>
                      <nav className="pager" aria-label="用户分页">
                        <button type="button" className="button button--secondary button--small" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
                        <span className="mono">第 {page} / {totalPages} 页</span>
                        <button type="button" className="button button--secondary button--small" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
                      </nav>
                    </section>

                    <aside className="decision-panel" aria-labelledby="decision-heading">
                      <div className="panel-head">
                        <h2 id="decision-heading">审批申请</h2>
                        {selected && <button type="button" className="button button--link button--small" onClick={() => setSelected(null)}>取消选择</button>}
                      </div>
                      {!selected
                        ? <p className="panel-note">从左侧列表选择一条待审批申请，填写理由后批准或拒绝。</p>
                        : selected.roleApplication
                          ? <>
                            <dl className="label-list">
                              <div><dt>用户</dt><dd>{selected.nickname || selected.username} <span className="mono">@{selected.username}</span></dd></div>
                              <div><dt>当前角色</dt><dd>{roleLabel(selected.role)}</dd></div>
                              <div><dt>申请角色</dt><dd>{roleLabel(selected.roleApplication.requestedRole)}</dd></div>
                              <div><dt>数据版本</dt><dd className="mono">revision {selected.revision}</dd></div>
                            </dl>
                            <label className="field">
                              <span>审批理由</span>
                              <textarea
                                rows={4}
                                maxLength={500}
                                required
                                value={reason}
                                disabled={deciding}
                                onChange={(event) => setReason(event.target.value)}
                              />
                            </label>
                            <p className="field-note">批准和拒绝都需要填写理由；服务端会再次校验申请状态和 revision。</p>
                            {decisionError && <InlineNotice tone="error" title="审批失败">{decisionError}</InlineNotice>}
                            <div className="decision-actions">
                              <button type="button" className="button button--primary" disabled={deciding} onClick={() => void decide(true)}>{deciding ? '提交中…' : '批准'}</button>
                              <button type="button" className="button button--secondary" disabled={deciding} onClick={() => void decide(false)}>拒绝</button>
                            </div>
                          </>
                          : <p className="panel-note">该用户当前没有待审批的角色申请。</p>}
                    </aside>
                  </div>}
          </>}
  </section>
}

function UserSkeleton() {
  return <ul className="user-list" aria-label="正在加载用户" aria-busy="true">
    {[0, 1, 2].map((item) => <li className="user-row user-row--skeleton" key={item}>
      <span className="skeleton skeleton--line" />
      <span className="skeleton skeleton--line short" />
      <span className="skeleton skeleton--line" />
    </li>)}
  </ul>
}
