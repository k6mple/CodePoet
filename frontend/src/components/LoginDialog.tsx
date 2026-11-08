'use client'

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { errorMessage, request } from '@/api/client'
import type { User } from '@/types/core'

type Mode = 'login' | 'register'

export default function LoginDialog({ open, reason, onClose, onLogin }: {
  open: boolean
  reason: string
  onClose: () => void
  onLogin: (username: string, password: string) => Promise<void>
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const usernameRef = useRef<HTMLInputElement>(null)
  const triggerRef = useRef<HTMLElement | null>(null)
  const openRef = useRef(open)
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [nickname, setNickname] = useState('')
  const [requestedRole, setRequestedRole] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')
  const [registerSuccess, setRegisterSuccess] = useState('')

  useEffect(() => { openRef.current = open }, [open])

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open) {
      if (!dialog.open) {
        triggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
        dialog.showModal()
        window.setTimeout(() => usernameRef.current?.focus(), 0)
      }
    } else if (dialog.open) {
      dialog.close()
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [open])

  useEffect(() => {
    if (open) return
    setPassword('')
    setMode('login')
    setNickname('')
    setRequestedRole('')
    setFormError('')
    setRegisterSuccess('')
  }, [open])

  useEffect(() => {
    if (open) return
    triggerRef.current?.focus()
    triggerRef.current = null
  }, [open])

  function trapFocus(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return
    const dialog = dialogRef.current
    if (!dialog) return
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    )).filter((element) => element.offsetParent !== null)
    if (focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    const active = document.activeElement
    if (!event.shiftKey && active === last) {
      event.preventDefault()
      first.focus()
    } else if (event.shiftKey && (active === first || !dialog.contains(active))) {
      event.preventDefault()
      last.focus()
    }
  }

  function switchMode(next: Mode) {
    if (submitting) return
    setMode(next)
    setFormError('')
    if (next === 'register') setRegisterSuccess('')
    window.setTimeout(() => usernameRef.current?.focus(), 0)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setFormError('')
    try {
      if (mode === 'register') {
        const payload: { username: string; password: string; nickname?: string; requestedRole?: string } = {
          username: username.trim(),
          password,
        }
        const trimmedNickname = nickname.trim()
        if (trimmedNickname) payload.nickname = trimmedNickname
        if (requestedRole) payload.requestedRole = requestedRole
        const created = await request<User>('/auth/register', {
          method: 'POST',
          body: JSON.stringify(payload),
        })
        if (!openRef.current) return
        setRegisterSuccess(`注册成功：${created.username}。请登录。${requestedRole
          ? '角色申请已提交，管理员审批前你仍是普通创作者。'
          : '你现在是普通创作者。'}`)
        setPassword('')
        setMode('login')
      } else {
        setRegisterSuccess('')
        await onLogin(username.trim(), password)
      }
    } catch (cause) {
      if (openRef.current) setFormError(errorMessage(cause))
    } finally {
      setSubmitting(false)
    }
  }

  const isLogin = mode === 'login'

  return <dialog
    ref={dialogRef}
    className="login-dialog"
    aria-labelledby="login-title"
    aria-describedby="login-lead"
    onCancel={(event) => { event.preventDefault(); onClose() }}
    onClose={() => { if (open) onClose() }}
    onClick={(event) => { if (event.target === event.currentTarget) onClose() }}
    onKeyDown={trapFocus}
  >
    <div className="login-panel">
      <header className="login-panel__head">
        <h2 id="login-title">{isLogin ? '登录' : '注册'}</h2>
        <button type="button" className="button button--link button--small" onClick={onClose}>关闭</button>
      </header>
      <p id="login-lead" className="login-lead">{isLogin
        ? '登录后可生成、上传并保存私有作品。'
        : '注册后默认为普通创作者；申请更高角色需要管理员审批。'}</p>
      {isLogin && reason && <p className="login-reason" role="status">{reason}</p>}
      {isLogin && registerSuccess && <p className="login-success" role="status">{registerSuccess}</p>}
      <form onSubmit={(event) => void submit(event)} aria-busy={submitting}>
        <label htmlFor="login-username">
          用户名
          <input
            ref={usernameRef}
            id="login-username"
            name="username"
            autoComplete="username"
            required
            minLength={3}
            maxLength={32}
            pattern="[A-Za-z0-9_]+"
            title="3–32 位字母、数字或下划线"
            value={username}
            disabled={submitting}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label htmlFor="login-password">
          密码
          <input
            id="login-password"
            name="password"
            type="password"
            autoComplete={isLogin ? 'current-password' : 'new-password'}
            required
            minLength={isLogin ? undefined : 10}
            maxLength={128}
            title={isLogin ? undefined : '10–128 位'}
            value={password}
            disabled={submitting}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {!isLogin && <>
          <label htmlFor="register-nickname">
            昵称（可选）
            <input
              id="register-nickname"
              name="nickname"
              autoComplete="nickname"
              maxLength={40}
              value={nickname}
              disabled={submitting}
              onChange={(event) => setNickname(event.target.value)}
            />
          </label>
          <label htmlFor="register-role">
            申请角色
            <select
              id="register-role"
              name="requestedRole"
              value={requestedRole}
              disabled={submitting}
              onChange={(event) => setRequestedRole(event.target.value)}
            >
              <option value="">普通创作者（默认）</option>
              <option value="developer">模板开发者（需审批）</option>
              <option value="expert">领域专家（需审批）</option>
            </select>
          </label>
          <p className="login-hint">用户名 3–32 位字母、数字或下划线；密码至少 10 位。管理员账号由系统初始化。</p>
        </>}
        {formError && <p className="form-error" role="alert">{formError}</p>}
        <div className="login-actions">
          <button type="submit" className="button button--primary" disabled={submitting}>
            {submitting ? (isLogin ? '登录中…' : '注册中…') : (isLogin ? '登录' : '注册')}
          </button>
          <button type="button" className="button button--secondary" onClick={onClose}>取消</button>
        </div>
        <div className="login-switch">
          <span>{isLogin ? '还没有账号？' : '已有账号？'}</span>
          <button type="button" className="button button--link button--small" disabled={submitting} onClick={() => switchMode(isLogin ? 'register' : 'login')}>
            {isLogin ? '注册新账号' : '返回登录'}
          </button>
        </div>
      </form>
      <p className="login-note">{isLogin
        ? '会话由服务器 Cookie 管理，不写入浏览器本地存储。'
        : '注册不会自动登录；角色申请提交后由管理员审批，批准前仍按普通创作者处理。'}</p>
    </div>
  </dialog>
}
