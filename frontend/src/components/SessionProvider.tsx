'use client'

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { errorMessage, request, setCsrf } from '@/api/client'
import type { AuthSession, User } from '@/types/core'
import { roleLabel } from '@/utils/format'
import LoginDialog from './LoginDialog'

const SessionContext = createContext<{
  user: User | null
  loading: boolean
  openLogin: (reason?: string) => void
  updateUser: (user: User) => void
  refreshUser: () => Promise<User>
}>({
  user: null,
  loading: true,
  openLogin: () => {},
  updateUser: () => {},
  refreshUser: async () => { throw new Error('Session provider not ready') },
})

export const useSession = () => useContext(SessionContext)

export default function SessionProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [accountBusy, setAccountBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [loginOpen, setLoginOpen] = useState(false)
  const [loginReason, setLoginReason] = useState('')
  const userRef = useRef<User | null>(null)

  useEffect(() => { userRef.current = user }, [user])

  const openLogin = useCallback((reason = '') => {
    setLoginReason(reason)
    setLoginOpen(true)
  }, [])

  const closeLogin = useCallback(() => {
    setLoginOpen(false)
    setLoginReason('')
  }, [])

  const updateUser = useCallback((next: User) => {
    setUser(next)
    userRef.current = next
  }, [])

  const refreshUser = useCallback(async () => {
    const session = await request<AuthSession>('/me')
    setCsrf(session.csrfToken)
    setUser(session.user)
    return session.user
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const session = await request<AuthSession>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    setCsrf(session.csrfToken)
    setUser(session.user)
    setLoginOpen(false)
    setLoginReason('')
    setMessage('')
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const expired = () => {
      const wasLoggedIn = Boolean(userRef.current)
      setUser(null)
      setCsrf('')
      if (wasLoggedIn) {
        setLoginReason('登录已失效。当前编辑内容已保留，请重新登录后继续。')
        setLoginOpen(true)
      }
    }
    window.addEventListener('codepoet:auth-required', expired)
    async function restore() {
      try {
        const csrf = await request<{ csrfToken: string }>('/auth/csrf', { signal: controller.signal })
        setCsrf(csrf.csrfToken)
        const session = await request<AuthSession>('/me', { signal: controller.signal })
        if (!controller.signal.aborted) {
          setCsrf(session.csrfToken)
          setUser(session.user)
        }
      } catch {
        // Initial restore is best-effort. The footer health check reports an
        // unreachable backend, and explicit login attempts show their own error.
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void restore()
    return () => {
      controller.abort()
      window.removeEventListener('codepoet:auth-required', expired)
    }
  }, [])

  async function logout() {
    if (accountBusy) return
    setAccountBusy(true)
    setMessage('')
    try {
      await request<void>('/auth/logout', { method: 'POST' })
      setUser(null)
      setCsrf('')
      setMessage('已退出登录。')
    } catch (cause) {
      setMessage(errorMessage(cause))
    } finally {
      setAccountBusy(false)
    }
  }

  const creatorActive = pathname === '/'
  const worksActive = pathname === '/works' || pathname.startsWith('/works/')
  const spaceActive = pathname === '/space' || pathname.startsWith('/space/')
  const templatesActive = pathname === '/templates' || pathname.startsWith('/templates/')
  const searchActive = pathname.startsWith('/search') || pathname.startsWith('/knowledge/')
  const manageActive = pathname === '/manage' || pathname.startsWith('/manage/')
  const templateManageActive = pathname.startsWith('/manage/templates')
  const reviewsActive = pathname === '/reviews' || pathname.startsWith('/reviews/')
  const adminUsersActive = pathname.startsWith('/admin/users')
  const adminOpsActive = pathname.startsWith('/admin/ops')
  const accountActive = pathname === '/account'
  const canManageKnowledge = Boolean(user?.permissions.some((permission) =>
    permission === 'knowledge.write' || permission === 'ontology.write' || permission === 'tags.write'))
  const canManageTemplates = Boolean(user?.permissions.some((permission) =>
    permission === 'templates.create' || permission === 'templates.editOwn' || permission === 'templates.editAny'))
  const canReview = Boolean(user?.permissions.includes('reviews.decide'))
  const canApproveUsers = Boolean(user?.permissions.includes('users.approveRole'))
  const canOps = Boolean(user?.permissions.some((permission) =>
    permission === 'exports.create' || permission === 'imports.apply' || permission === 'audit.read' || permission === 'rules.write'))
  const personName = user ? (user.nickname || user.username) : ''

  return <SessionContext.Provider value={{ user, loading, openLogin, updateUser, refreshUser }}>
    <header className="site-header">
      <div className="site-header__inner">
        <Link href="/" className="brand" aria-label="代码诗 CodePoet 首页">
          <span className="brand-seal" aria-hidden="true">诗</span>
          <span className="brand-name">代码诗</span>
          <span className="brand-sub">CodePoet</span>
        </Link>
        <nav className="site-nav" aria-label="主导航">
          <Link href="/" className={`nav-link${creatorActive ? ' is-active' : ''}`} aria-current={creatorActive ? 'page' : undefined}>创作台</Link>
          <Link href="/works" className={`nav-link${worksActive ? ' is-active' : ''}`} aria-current={worksActive ? 'page' : undefined}>我的作品</Link>
          {user && <Link href="/space" className={`nav-link${spaceActive ? ' is-active' : ''}`} aria-current={spaceActive ? 'page' : undefined}>个人空间</Link>}
          <Link href="/templates" className={`nav-link${templatesActive ? ' is-active' : ''}`} aria-current={templatesActive ? 'page' : undefined}>模板库</Link>
          <Link href="/search" className={`nav-link${searchActive ? ' is-active' : ''}`} aria-current={searchActive ? 'page' : undefined}>检索</Link>
          {canManageKnowledge && <Link href="/manage" className={`nav-link${manageActive && !templateManageActive ? ' is-active' : ''}`} aria-current={manageActive && !templateManageActive ? 'page' : undefined}>知识管理</Link>}
          {canManageTemplates && <Link href="/manage/templates" className={`nav-link${templateManageActive ? ' is-active' : ''}`} aria-current={templateManageActive ? 'page' : undefined}>模板管理</Link>}
          {canReview && <Link href="/reviews" className={`nav-link${reviewsActive ? ' is-active' : ''}`} aria-current={reviewsActive ? 'page' : undefined}>审核台</Link>}
          {canApproveUsers && <Link href="/admin/users" className={`nav-link${adminUsersActive ? ' is-active' : ''}`} aria-current={adminUsersActive ? 'page' : undefined}>用户管理</Link>}
          {canOps && <Link href="/admin/ops" className={`nav-link${adminOpsActive ? ' is-active' : ''}`} aria-current={adminOpsActive ? 'page' : undefined}>运维</Link>}
        </nav>
        <div className="site-account" aria-label="账号">
          {loading ? <span className="account-loading">正在恢复登录…</span>
            : user ? <>
              <Link href="/account" className={`account-person${accountActive ? ' is-active' : ''}`} aria-label="账号与资料">
                <span className="account-mark" aria-hidden="true">{personName.slice(0, 1)}</span>
                <span className="account-name" title={personName}>{personName}</span>
                <span className="account-role">{roleLabel(user.role)}</span>
              </Link>
              <button type="button" className="button button--ghost button--small" disabled={accountBusy} onClick={() => void logout()}>退出</button>
            </> : <>
              <span className="account-guest">访客</span>
              <button type="button" className="button button--secondary button--small" onClick={() => openLogin()}>登录</button>
            </>}
        </div>
      </div>
    </header>
    {message && <div className="global-note" role="status">
      <span>{message}</span>
      <button type="button" className="button button--link button--small" onClick={() => setMessage('')}>关闭</button>
    </div>}
    {children}
    <LoginDialog
      open={loginOpen}
      reason={loginReason}
      onClose={closeLogin}
      onLogin={login}
    />
  </SessionContext.Provider>
}
