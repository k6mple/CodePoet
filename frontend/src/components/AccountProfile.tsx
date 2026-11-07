'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import type { AuthSession } from '@/types/core'
import { accountStatusLabel, applicationStatusLabel, formatDateTime, roleLabel } from '@/utils/format'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'

export default function AccountProfile() {
  const { user, loading, openLogin, updateUser, refreshUser } = useSession()
  const [nickname, setNickname] = useState('')
  const [bio, setBio] = useState('')
  const [saving, setSaving] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!user) return
    setNickname(user.nickname)
    setBio(user.bio)
  }, [user?.id, user?.revision])

  async function save() {
    if (!user || saving) return
    const trimmed = nickname.trim()
    if (!trimmed) {
      setError('昵称不能为空。')
      return
    }
    if (trimmed === user.nickname && bio === user.bio) {
      setMessage('没有需要保存的修改。')
      return
    }
    setSaving(true)
    setError('')
    setMessage('')
    try {
      const session = await request<AuthSession>('/me', {
        method: 'PATCH',
        body: JSON.stringify({ expectedRevision: user.revision, nickname: trimmed, bio }),
      })
      updateUser(session.user)
      setMessage('资料已保存。')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setSaving(false)
    }
  }

  async function reload() {
    if (reloading) return
    setReloading(true)
    setError('')
    setMessage('')
    try {
      await refreshUser()
      setMessage('已重新加载账号资料。')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setReloading(false)
    }
  }

  const canManageKnowledge = Boolean(user?.permissions.some((permission) =>
    permission === 'knowledge.write' || permission === 'ontology.write' || permission === 'tags.write'))
  const canApproveUsers = Boolean(user?.permissions.includes('users.approveRole'))
  const application = user?.roleApplication

  return <section className="account-page" aria-labelledby="account-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="account-heading">账号与资料</h1>
        <p>管理昵称与简介，查看角色申请状态和服务器授予的权限。</p>
      </div>
    </header>

    {loading
      ? <LoadingLines label="正在恢复登录" />
      : !user
        ? <EmptyState
          title="登录后查看账号"
          action={<button type="button" className="button button--primary" onClick={() => openLogin('登录后即可管理个人资料与角色申请。')}>登录</button>}
        >
          账号资料保存在服务器上，不依赖当前浏览器。
        </EmptyState>
        : <div className="account-layout">
          <section className="account-panel" aria-labelledby="profile-heading">
            <div className="panel-head">
              <h2 id="profile-heading">个人资料</h2>
              <span className="mono">revision {user.revision}</span>
            </div>
            <form className="account-form" onSubmit={(event) => { event.preventDefault(); void save() }}>
              <label className="field">
                <span>昵称</span>
                <input
                  required
                  maxLength={40}
                  value={nickname}
                  disabled={saving}
                  onChange={(event) => setNickname(event.target.value)}
                />
              </label>
              <label className="field">
                <span>简介</span>
                <textarea
                  rows={5}
                  maxLength={500}
                  value={bio}
                  disabled={saving}
                  onChange={(event) => setBio(event.target.value)}
                />
              </label>
              <p className="field-note">用户名 @{user.username} 不可修改。保存时会带上当前 revision，避免覆盖他人的修改。</p>
              <div className="form-actions">
                <button type="submit" className="button button--primary" disabled={saving}>{saving ? '保存中…' : '保存资料'}</button>
                <button type="button" className="button button--secondary button--small" disabled={saving} onClick={() => {
                  setNickname(user.nickname)
                  setBio(user.bio)
                  setMessage('已恢复为当前服务器资料。')
                  setError('')
                }}>重置</button>
              </div>
            </form>
            {message && <InlineNotice tone="success">{message}</InlineNotice>}
            {error && <InlineNotice tone="error" title="操作失败">
              {error}
              <div className="notice__actions">
                <button type="button" className="button button--secondary button--small" disabled={reloading} onClick={() => void reload()}>
                  {reloading ? '重新加载中…' : '重新加载账号'}
                </button>
              </div>
            </InlineNotice>}
          </section>

          <aside className="account-side" aria-label="身份与权限">
            <section className="account-panel" aria-labelledby="identity-heading">
              <div className="panel-head">
                <h2 id="identity-heading">身份</h2>
                <span className={`status-chip is-${user.status}`}>{accountStatusLabel(user.status)}</span>
              </div>
              <dl className="label-list">
                <div><dt>用户名</dt><dd className="mono">@{user.username}</dd></div>
                <div><dt>当前角色</dt><dd>{roleLabel(user.role)}</dd></div>
                <div><dt>注册时间</dt><dd className="mono">{formatDateTime(user.createdAt)}</dd></div>
                <div><dt>资料更新时间</dt><dd className="mono">{formatDateTime(user.updatedAt)}</dd></div>
              </dl>
            </section>

            <section className="account-panel" aria-labelledby="application-heading">
              <div className="panel-head">
                <h2 id="application-heading">角色申请</h2>
                {application && <span className={`status-chip is-${application.status}`}>{applicationStatusLabel(application.status)}</span>}
              </div>
              {application
                ? <div className="application-state">
                  <p className="application-title">申请角色：{roleLabel(application.requestedRole)}</p>
                  {application.status === 'pending' && <p className="panel-note">审批前你仍是{roleLabel(user.role)}。管理员批准后角色才会改变。</p>}
                  {application.reason && <p className="panel-note">理由：{application.reason}</p>}
                  {application.decidedAt && <p className="panel-note mono">决定时间 {formatDateTime(application.decidedAt)}</p>}
                </div>
                : <p className="panel-note">当前没有待审批的角色申请。角色申请在注册时提交。</p>}
            </section>

            <section className="account-panel" aria-labelledby="permissions-heading">
              <div className="panel-head">
                <h2 id="permissions-heading">权限</h2>
                <span className="mono">{user.permissions.length}</span>
              </div>
              <p className="panel-note">菜单与操作依据服务器返回的权限显示；页面隐藏不能替代服务端校验。</p>
              <details className="tech-details">
                <summary>查看权限列表</summary>
                <ul className="permission-list">
                  {user.permissions.map((permission) => <li className="mono" key={permission}>{permission}</li>)}
                </ul>
              </details>
              <div className="account-links">
                {canManageKnowledge && <Link href="/manage" className="button button--secondary button--small">进入知识管理</Link>}
                {canApproveUsers && <Link href="/admin/users" className="button button--secondary button--small">用户与角色审批</Link>}
              </div>
            </section>
          </aside>
        </div>}
  </section>
}
