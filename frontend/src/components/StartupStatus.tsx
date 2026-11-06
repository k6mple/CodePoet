'use client'

import { useEffect, useRef, useState } from 'react'
import { getHealth, type Health } from '@/api/health'

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
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : '无法连接后端')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          pending.current = false
          setLoading(false)
        }
      })
    return () => controller.abort()
  }, [attempt])

  return (
    <section aria-labelledby="status-heading">
      <h2 id="status-heading">开发环境连接检查</h2>
      <div aria-live="polite">
        {loading ? <p>正在连接后端…</p> : health
          ? <p className="success">后端与数据库已连接 · API {health.apiVersion}</p>
          : <p className="error">{error}。请确认后端已启动，再重试。</p>}
      </div>
      <button disabled={loading} onClick={() => {
        if (!pending.current) {
          pending.current = true
          setAttempt((value) => value + 1)
        }
      }}>{loading ? '检查中…' : '重新检查'}</button>
    </section>
  )
}
