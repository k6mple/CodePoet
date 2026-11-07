'use client'
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { ApiError, errorMessage, request, setCsrf } from '@/api/client'
import type { AuthSession, User } from '@/types/core'

const SessionContext = createContext<{ user: User | null; loading: boolean }>({ user: null, loading: true })
export const useSession = () => useContext(SessionContext)

export default function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    const expired = () => { setUser(null); setMessage('请登录；当前编辑内容已保留在本页。') }
    window.addEventListener('codepoet:auth-required', expired)
    async function restore() {
      try {
        const csrf = await request<{ csrfToken: string }>('/auth/csrf', { signal: controller.signal })
        setCsrf(csrf.csrfToken)
        const session = await request<AuthSession>('/me', { signal: controller.signal })
        if (!controller.signal.aborted) { setCsrf(session.csrfToken); setUser(session.user); setMessage('') }
      } catch (cause) {
        if (!controller.signal.aborted && !(cause instanceof ApiError && cause.status === 401)) setMessage(errorMessage(cause))
      } finally { if (!controller.signal.aborted) setLoading(false) }
    }
    void restore()
    return () => { controller.abort(); window.removeEventListener('codepoet:auth-required', expired) }
  }, [])
  async function login(form: HTMLFormElement) {
    if (busy) return
    setBusy(true); setMessage('')
    const data = new FormData(form)
    try {
      const session = await request<AuthSession>('/auth/login', { method: 'POST', body: JSON.stringify({ username: data.get('username'), password: data.get('password') }) })
      setCsrf(session.csrfToken); setUser(session.user); form.reset()
    } catch (cause) { setMessage(errorMessage(cause)) }
    finally { setBusy(false) }
  }
  return <SessionContext.Provider value={{ user, loading }}>
    <header className="site-header"><a href="/">代码诗</a><nav aria-label="主导航"><a href="/">创作台</a><a href="/works">我的作品</a><a href="/fixture">Canvas 验证</a></nav></header>
    <section className="account" aria-label="账号">
      {loading ? <p>正在恢复登录…</p> : user ? <>
        <p>{user.nickname || user.username} · {user.role}</p>
        <button disabled={busy} onClick={async () => {
          setBusy(true)
          try { await request<void>('/auth/logout', { method: 'POST' }); setUser(null); setCsrf(''); setMessage('已退出登录。') }
          catch (cause) { setMessage(errorMessage(cause)) } finally { setBusy(false) }
        }}>退出登录</button>
      </> : <form onSubmit={(event) => { event.preventDefault(); void login(event.currentTarget) }}>
        <label>用户名<input name="username" autoComplete="username" required minLength={3} maxLength={32} /></label>
        <label>密码<input name="password" type="password" autoComplete="current-password" required maxLength={128} /></label>
        <button disabled={busy}>{busy ? '登录中…' : '登录'}</button>
      </form>}
      {message && <p role="status" className="error">{message}</p>}
    </section>
    {children}
  </SessionContext.Provider>
}
