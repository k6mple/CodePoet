'use client'

import { useEffect, useRef, useState } from 'react'
import { getHealth, type Health } from '@/api/health'
import { errorMessage } from '@/api/client'

export default function StartupStatus() {
  const [loading, setLoading] = useState(true)
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const pending = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    pending.current = true
    setLoading(true)
    setHealth(null)
    setError('')
    getHealth(controller.signal)
      .then((result) => { if (!controller.signal.aborted) setHealth(result) })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
      .finally(() => {
        if (!controller.signal.aborted) {
          pending.current = false
          setLoading(false)
        }
      })
    return () => controller.abort()
  }, [attempt])

  const tone = loading ? 'is-pending' : health ? 'is-ok' : 'is-error'

  return <div className="system-status" aria-live="polite">
    <span className={`status-dot ${tone}`} aria-hidden="true" />
    <span className="system-status__label">
      {loading ? '正在检查后端…' : health
        ? <>后端与数据库已连接 <span className="mono">API {health.apiVersion}</span></>
        : '后端未连接'}
    </span>
    {error && <details className="system-status__details">
      <summary>查看诊断</summary>
      <p>{error}。请确认后端已启动，再重试。</p>
    </details>}
    <button type="button" className="button button--link button--small" disabled={loading} onClick={() => {
      if (!pending.current) {
        pending.current = true
        setAttempt((value) => value + 1)
      }
    }}>{loading ? '检查中…' : '重新检查'}</button>
  </div>
}
