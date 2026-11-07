import type { ReactNode } from 'react'

export function InlineNotice({ tone = 'info', title, children }: {
  tone?: 'info' | 'success' | 'error'
  title?: string
  children: ReactNode
}) {
  return <div className={`notice notice--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
    {title && <strong className="notice__title">{title}</strong>}
    <div className="notice__body">{children}</div>
  </div>
}

export function EmptyState({ title, children, action }: {
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return <div className="empty-state">
    <h3>{title}</h3>
    {children && <div className="empty-state__body">{children}</div>}
    {action && <div className="empty-state__action">{action}</div>}
  </div>
}

export function LoadingLines({ label = '正在加载' }: { label?: string }) {
  return <div className="loading-lines" role="status" aria-live="polite">
    <span className="sr-only">{label}</span>
    <span className="skeleton skeleton--line" aria-hidden="true" />
    <span className="skeleton skeleton--line short" aria-hidden="true" />
    <span className="skeleton skeleton--line" aria-hidden="true" />
  </div>
}
