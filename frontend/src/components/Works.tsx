'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ApiError, errorMessage, request } from '@/api/client'
import type { Page, Work, WorkDetail } from '@/types/core'
import { formatDateTime, paddedIndex, workStatusLabel } from '@/utils/format'
import CanvasPreview from './CanvasPreview'
import { EmptyState, InlineNotice, LoadingLines } from './Feedback'
import { useSession } from './SessionProvider'

const PAGE_SIZE = 12

export function WorkList() {
  const { user, loading, openLogin } = useSession()
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Page<Work> | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => { setPage(1) }, [user?.id])

  useEffect(() => {
    setData(null)
    setError('')
    if (!user) return
    const controller = new AbortController()
    request<Page<Work>>(`/works?scope=mine&page=${page}&pageSize=${PAGE_SIZE}`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setData(result) })
      .catch((cause) => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [user, page, attempt])

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1

  return <section className="works-page" aria-labelledby="works-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="works-heading">我的作品</h1>
        <p>按保存时间排列的作品档案。每件作品都绑定服务器确认的生成记录与 PNG 上传任务。</p>
      </div>
      <div className="page-intro__actions">
        <Link href="/" className="button button--secondary">创作新作品</Link>
      </div>
    </header>

    {loading
      ? <LoadingLines label="正在恢复登录" />
      : !user
        ? <EmptyState
          title="登录后查看作品"
          action={<button type="button" className="button button--primary" onClick={() => openLogin('登录后即可查看并管理你的私有作品。')}>登录</button>}
        >
          你的作品保存在服务器上，不依赖当前浏览器。
        </EmptyState>
        : error
          ? <div className="works-error">
            <InlineNotice tone="error" title="作品列表加载失败">
              暂时无法读取作品列表。请查看技术详情；若后端已启动，可能是对应接口尚未实现。
              <details className="notice__details">
                <summary>查看技术详情</summary>
                <p className="notice__detail">{error}</p>
              </details>
            </InlineNotice>
            <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
          </div>
          : !data
            ? <WorkSkeleton />
            : data.items.length === 0
              ? <EmptyState
                title="还没有保存的作品"
                action={<Link href="/" className="button button--primary">前往创作台</Link>}
              >
                在创作台生成并保存作品后，它会出现在这里。
              </EmptyState>
              : <>
                <div className="works-toolbar">
                  <span className="mono">共 {data.total} 件</span>
                  <span className="mono">第 {data.page} / {totalPages} 页</span>
                </div>
                <ul className="work-grid">
                  {data.items.map((work, index) => <WorkCard
                    key={work.id}
                    work={work}
                    number={(data.page - 1) * data.pageSize + index + 1}
                  />)}
                </ul>
                <nav className="pager" aria-label="作品分页">
                  <button type="button" className="button button--secondary button--small" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
                  <span className="mono">第 {data.page} / {totalPages} 页</span>
                  <button type="button" className="button button--secondary button--small" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
                </nav>
              </>}
  </section>
}

function WorkCard({ work, number }: { work: Work; number: number }) {
  const [detail, setDetail] = useState<WorkDetail | null>(null)
  const [preview, setPreview] = useState('')
  const [previewError, setPreviewError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    let createdUrl = ''
    setDetail(null)
    setPreview('')
    setPreviewError('')
    async function load() {
      try {
        const result = await request<WorkDetail>(`/work-versions/${encodeURIComponent(work.displayVersionId)}`, { signal: controller.signal })
        if (controller.signal.aborted) return
        setDetail(result)
        const manifestFrames = result.renderJob.manifest?.frames
        const artifactId = work.thumbnailArtifactId ?? manifestFrames?.[manifestFrames.length - 1]?.artifactId
        if (!artifactId) {
          setPreviewError('没有可用的预览帧')
          return
        }
        const blob = await request<Blob>(`/artifacts/${encodeURIComponent(artifactId)}`, { signal: controller.signal, headers: { Accept: 'image/png' } })
        if (controller.signal.aborted) return
        createdUrl = URL.createObjectURL(blob)
        setPreview(createdUrl)
      } catch (cause) {
        if (!controller.signal.aborted) setPreviewError(errorMessage(cause))
      }
    }
    void load()
    return () => {
      controller.abort()
      if (createdUrl) URL.revokeObjectURL(createdUrl)
    }
  }, [work.displayVersionId, work.thumbnailArtifactId])

  const title = work.title || '未命名作品'
  const initial = title.trim().slice(0, 1) || '作'

  return <li className="work-card">
    <Link href={`/works/versions/${encodeURIComponent(work.displayVersionId)}`} className="work-card__link">
      <div className="work-card__frame">
        {preview
          ? <img src={preview} alt={`《${title}》的服务器保存预览`} loading="lazy" />
          : <div className="work-card__placeholder">
            <span aria-hidden="true">{initial}</span>
            <small>{previewError ? '预览不可用' : '读取预览…'}</small>
          </div>}
        <span className="work-card__index mono">{paddedIndex(number)}</span>
      </div>
      <div className="work-card__body">
        <div className="work-card__meta">
          <span>{detail ? workStatusLabel(detail.workVersion.status) : previewError ? '版本信息不可用' : '读取版本…'}</span>
          <span className="mono">{formatDateTime(work.createdAt)}</span>
        </div>
        <h2 className="work-card__title">{title}</h2>
        {work.description && <p className="work-card__desc">{work.description}</p>}
        <div className="work-card__foot">
          <span className="mono">{detail ? `第 ${detail.workVersion.versionNumber} 版` : '版本信息'}</span>
          <span className="work-card__cta">查看作品</span>
        </div>
      </div>
    </Link>
  </li>
}

function WorkSkeleton() {
  return <ul className="work-grid" aria-label="正在加载作品" aria-busy="true">
    {[0, 1, 2].map((item) => <li className="work-card work-card--skeleton" key={item}>
      <div className="work-card__frame skeleton" />
      <div className="work-card__body">
        <span className="skeleton skeleton--line short" />
        <span className="skeleton skeleton--line" />
        <span className="skeleton skeleton--line short" />
      </div>
    </li>)}
  </ul>
}

export function WorkViewer({ id }: { id: string }) {
  const { user, loading, openLogin } = useSession()
  const [data, setData] = useState<WorkDetail | null>(null)
  const [error, setError] = useState('')
  const [errorStatus, setErrorStatus] = useState<number | null>(null)
  const [frameError, setFrameError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [frames, setFrames] = useState<{ url: string; time: number }[]>([])
  const [selected, setSelected] = useState(0)

  useEffect(() => {
    if (loading) return
    const controller = new AbortController()
    const created: string[] = []
    setData(null)
    setError('')
    setErrorStatus(null)
    setFrameError('')
    setFrames([])
    setSelected(0)

    async function load() {
      try {
        const detail = await request<WorkDetail>(`/work-versions/${encodeURIComponent(id)}`, { signal: controller.signal })
        if (controller.signal.aborted) return
        setData(detail)
        const manifestFrames = detail.renderJob.manifest?.frames ?? []
        if (detail.renderJob.status !== 'succeeded' || manifestFrames.length === 0) {
          setFrameError('该作品没有成功保存的 PNG 帧。')
          return
        }
        try {
          const loaded: { url: string; time: number }[] = []
          for (const frame of manifestFrames) {
            const blob = await request<Blob>(`/artifacts/${encodeURIComponent(frame.artifactId)}`, {
              signal: controller.signal, headers: { Accept: 'image/png' },
            })
            if (controller.signal.aborted) return
            const url = URL.createObjectURL(blob)
            created.push(url)
            loaded.push({ url, time: frame.timeMs })
          }
          if (!controller.signal.aborted) {
            setFrames(loaded)
            setSelected(loaded.length - 1)
          }
        } catch (cause) {
          if (!controller.signal.aborted) setFrameError(errorMessage(cause))
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(errorMessage(cause))
          setErrorStatus(cause instanceof ApiError ? cause.status : null)
        }
      }
    }
    void load()
    return () => {
      controller.abort()
      created.forEach((url) => URL.revokeObjectURL(url))
    }
  }, [id, user, loading, attempt])

  const activeFrame = data?.renderJob.manifest?.frames[selected]

  return <section className="work-viewer" aria-labelledby="work-heading">
    <nav className="back-link" aria-label="返回">
      <Link href="/works">返回我的作品</Link>
    </nav>

    <header className="work-header">
      <h1 id="work-heading">{data ? (data.workVersion.title || '未命名作品') : error ? '作品加载失败' : '正在读取作品'}</h1>
      {data && <div className="work-header__meta">
        <span className={`status-chip is-${data.workVersion.status}`}>{workStatusLabel(data.workVersion.status)}</span>
        <span className="mono">第 {data.workVersion.versionNumber} 版</span>
        <span className="mono">{formatDateTime(data.workVersion.createdAt)}</span>
      </div>}
    </header>

    {error
      ? <div className="viewer-error">
        <InlineNotice tone="error" title="无法读取作品">
          这个作品版本可能不存在、无权访问，或后端尚未就绪。
          <details className="notice__details">
            <summary>查看技术详情</summary>
            <p className="notice__detail">{error}</p>
          </details>
        </InlineNotice>
        <div className="viewer-error__actions">
          <button type="button" className="button button--secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
          {(!user || errorStatus === 401) && <button type="button" className="button button--primary" onClick={() => openLogin('登录后即可查看你的私有作品。')}>登录</button>}
        </div>
      </div>
      : !data
        ? <div className="viewer-loading">
          <LoadingLines label="正在读取作品" />
          <p>正在读取服务器保存的帧…</p>
        </div>
        : <>
          <div className="work-layout">
            <div className="work-stage">
              <div className="work-plate">
                {frames.length
                  ? <img src={frames[selected]?.url} alt={`服务器保存的 PNG，${frames[selected]?.time} 毫秒`} />
                  : <div className="work-plate__empty">{frameError ? 'PNG 帧不可用' : '正在读取服务器 PNG…'}</div>}
              </div>
              {frames.length > 1 && <div className="frame-strip" role="group" aria-label="选择采样帧">
                {frames.map((frame, index) => <button
                  type="button"
                  key={`${frame.time}-${index}`}
                  aria-pressed={selected === index}
                  onClick={() => setSelected(index)}
                >
                  <img src={frame.url} alt="" />
                  <span>{frame.time} ms</span>
                </button>)}
              </div>}
              {frames.length > 0 && <div className="plate-meta">
                <p className="plate-caption">服务器保存的 PNG · {activeFrame?.width ?? '—'} × {activeFrame?.height ?? '—'} · {frames[selected]?.time} ms</p>
                <a className="button button--secondary button--small" href={frames[selected]?.url} download={`work-${data.workVersion.id}-${frames[selected]?.time}ms.png`}>下载这张 PNG</a>
              </div>}
              {frameError && <InlineNotice tone="error" title="PNG 读取失败">{frameError}</InlineNotice>}
            </div>

            <aside className="work-label" aria-label="作品信息">
              <h2>展签</h2>
              <dl>
                <div><dt>版本</dt><dd>第 {data.workVersion.versionNumber} 版</dd></div>
                <div><dt>状态</dt><dd>{workStatusLabel(data.workVersion.status)}</dd></div>
                <div><dt>保存时间</dt><dd className="mono">{formatDateTime(data.workVersion.createdAt)}</dd></div>
                <div><dt>画布</dt><dd className="mono">{data.generation.ir.viewport.width} × {data.generation.ir.viewport.height}</dd></div>
                <div><dt>采样</dt><dd className="mono">{data.renderJob.timesMs.map((time) => `${time}ms`).join(' / ') || '—'}</dd></div>
              </dl>
              <p className="work-label__note">服务器保存的 PNG 来自浏览器 Canvas 的真实绘制与上传；服务端验证了帧、尺寸和生成记录的一致性。</p>
            </aside>
          </div>

          <section className="ir-preview" aria-labelledby="ir-preview-heading">
            <header>
              <h2 id="ir-preview-heading">按保存 IR 重绘</h2>
              <p>此预览由当前浏览器按保存的 IR 重新绘制，不是服务器保存的 PNG。</p>
            </header>
            <div className="ir-preview__frame">
              <CanvasPreview ir={data.generation.ir} timeMs={data.generation.ir.durationMs} />
            </div>
          </section>

          <section className="technical" aria-labelledby="technical-heading">
            <h2 id="technical-heading">技术档案</h2>
            <details className="tech-details">
              <summary>生成与渲染记录</summary>
              <pre>{JSON.stringify({ generation: data.generation, renderJob: data.renderJob }, null, 2)}</pre>
            </details>
            <details className="tech-details">
              <summary>IR JSON</summary>
              <pre>{JSON.stringify(data.generation.ir, null, 2)}</pre>
            </details>
          </section>
        </>}
  </section>
}
