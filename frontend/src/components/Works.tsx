'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { errorMessage, request } from '@/api/client'
import type { Page, Work, WorkDetail } from '@/types/core'
import { useSession } from './SessionProvider'
import CanvasPreview from './CanvasPreview'

export function WorkList() {
  const { user, loading } = useSession()
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Page<Work> | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    setData(null); setError('')
    if (!user) return
    const controller = new AbortController()
    request<Page<Work>>(`/works?scope=mine&page=${page}&pageSize=20`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setData(result) })
      .catch((cause) => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [user, page, attempt])
  return <section><h1>我的作品</h1>
    {loading ? <p>恢复登录中…</p> : !user ? <p>请先登录，查看本人作品。</p> : <>
      <button onClick={() => setAttempt((n) => n + 1)}>重新加载作品</button>
      {error && <p role="alert" className="error">{error}</p>}
      {!data && !error && <p>加载中…</p>}
      {data?.items.length === 0 && <p>尚无作品，去创作台生成并保存。</p>}
      <ul>{data?.items.map((work) => <li key={work.id}><Link href={`/works/versions/${encodeURIComponent(work.displayVersionId)}`}>{work.title}</Link></li>)}</ul>
      <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
      <span> 第 {page} 页 · 共 {data?.total ?? '—'} 个 </span>
      <button disabled={!data || page * 20 >= data.total} onClick={() => setPage(page + 1)}>下一页</button>
    </>}
  </section>
}

export function WorkViewer({ id }: { id: string }) {
  const { user, loading } = useSession()
  const [data, setData] = useState<WorkDetail | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [urls, setUrls] = useState<{ url: string; time: number }[]>([])
  useEffect(() => {
    if (loading) return
    const controller = new AbortController()
    const created: string[] = []
    setData(null); setUrls([]); setError('')
    async function load() {
      try {
        const detail = await request<WorkDetail>(`/work-versions/${encodeURIComponent(id)}`, { signal: controller.signal })
        if (controller.signal.aborted) return
        setData(detail)
        const frames = detail.renderJob.manifest?.frames
        if (detail.renderJob.status !== 'succeeded' || !frames?.length) throw new Error('该作品没有成功持久化的 PNG 帧')
        for (const frame of frames) {
          const blob = await request<Blob>(`/artifacts/${encodeURIComponent(frame.artifactId)}`, { signal: controller.signal, headers: { Accept: 'image/png' } })
          if (controller.signal.aborted) return
          const url = URL.createObjectURL(blob); created.push(url)
          setUrls((old) => [...old, { url, time: frame.timeMs }])
        }
      } catch (cause) { if (!controller.signal.aborted) setError(errorMessage(cause)) }
    }
    void load()
    return () => { controller.abort(); created.forEach((url) => URL.revokeObjectURL(url)) }
  }, [id, user, loading, attempt])
  return <section><h1>{data?.workVersion.title ?? '作品详情'}</h1>
    <button onClick={() => setAttempt((n) => n + 1)}>重新加载作品</button>
    {error && <p role="alert" className="error">{error}</p>}
    {!data && !error && <p>加载中…</p>}
    {data && <><p>版本 {data.workVersion.id} · {data.workVersion.status}</p>
      <h2>服务器保存的原始 PNG</h2>
      {urls.map(({ url, time }) => <figure key={url}><img src={url} alt={`已保存 PNG · ${time}ms`} /><figcaption>{time}ms · <a href={url} download={`work-${time}.png`}>下载 PNG</a></figcaption></figure>)}
      <h2>按保存 IR 绘制的预览</h2><CanvasPreview ir={data.generation.ir} timeMs={data.generation.ir.durationMs} />
      <details><summary>完整生成记录与 IR</summary><pre>{JSON.stringify(data.generation, null, 2)}</pre></details>
    </>}
  </section>
}
