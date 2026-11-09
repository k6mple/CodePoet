'use client'

import { useEffect, useRef, useState } from 'react'
import { errorMessage, request } from '@/api/client'
import { browserRenderer, uploadRenderFrames } from '@/api/render'
import type { Comparison, RenderJob, WorkDetail } from '@/types/core'
import { InlineNotice } from './Feedback'

function percentage(value: number | null) {
  return value === null ? '—' : `${(value * 100).toFixed(4)}%`
}

export default function WorkReproduction({ work, signedIn, onLogin }: {
  work: WorkDetail
  signedIn: boolean
  onLogin: () => void
}) {
  const pending = useRef<AbortController | null>(null)
  const checkpoint = useRef<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState('')
  const [result, setResult] = useState<{ comparison: Comparison; job: RenderJob } | null>(null)
  const [time, setTime] = useState(0)

  useEffect(() => () => pending.current?.abort(), [])

  async function reproduce() {
    if (!signedIn) { onLogin(); return }
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    const { signal } = controller
    const originalJobId = work.renderJob.id
    setBusy(true)
    setError('')
    setResult(null)
    setProgress('正在准备历史版本的复现任务…')
    try {
      const created = checkpoint.current
        ? await request<RenderJob>(`/render-jobs/${encodeURIComponent(checkpoint.current)}`, { signal })
        : await request<RenderJob>(`/work-versions/${encodeURIComponent(work.workVersion.id)}/reproduce`, {
          method: 'POST', body: JSON.stringify(browserRenderer()), signal,
        })
      if (created.originalJobId !== originalJobId) throw new Error('复现任务未绑定到当前历史版本。')
      checkpoint.current = created.id
      const job = await uploadRenderFrames(created, work.generation, work.renderJob.timesMs, setProgress, signal)
      setProgress('正在获取服务端像素比较…')
      const comparison = await request<Comparison>(
        `/render-jobs/${encodeURIComponent(job.id)}/comparison?originalJobId=${encodeURIComponent(originalJobId)}`, { signal },
      )
      if (comparison.jobId !== job.id || comparison.originalJobId !== originalJobId) {
        throw new Error('比较结果未绑定到当前复现任务。')
      }
      setTime(work.renderJob.timesMs[0])
      setResult({ comparison, job })
      checkpoint.current = null
    } catch (cause) {
      if (!signal.aborted) setError(errorMessage(cause))
    } finally {
      pending.current = null
      if (!signal.aborted) {
        setProgress('')
        setBusy(false)
      }
    }
  }

  const original = work.renderJob.manifest?.frames.find((frame) => frame.timeMs === time)
  const reproduced = result?.job.manifest?.frames.find((frame) => frame.timeMs === time)
  const comparison = result?.comparison

  return <section className="ir-preview" aria-labelledby="reproduction-heading">
    <header>
      <h2 id="reproduction-heading">复现与像素对比</h2>
      <p>按历史 IR 和原采样时刻重绘。像素指标由服务端解码原始与复现 PNG 后返回。</p>
    </header>
    <div className="form-actions">
      <button className="button button--primary button--small" type="button" onClick={() => void reproduce()} disabled={busy}>
        {busy ? '复现中…' : error && checkpoint.current ? '重试复现任务' : '复现此版本'}
      </button>
      {error && checkpoint.current && <button className="button button--secondary button--small" type="button"
        onClick={() => { checkpoint.current = null; setError(''); void reproduce() }}>新建复现任务</button>}
    </div>
    {progress && <p role="status">{progress}</p>}
    {error && <InlineNotice tone="error" title="复现未完成">{error}</InlineNotice>}
    {comparison && <>
      <InlineNotice tone={comparison.compatible && comparison.exactMatch === true ? 'success' : 'info'}>
        <p>{comparison.compatible
          ? comparison.exactMatch === true ? '像素一致' : comparison.exactMatch === false ? '检测到像素差异' : '像素比较未返回结果'
          : '当前条件无法进行像素比较'}</p>
        {comparison.reason && <p>{comparison.reason}</p>}
        <p>平均绝对误差 {percentage(comparison.mae)} · 不同像素比例 {percentage(comparison.differentPixelRatio)}</p>
      </InlineNotice>
      <label className="field">对比时刻
        <select value={time} onChange={(event) => setTime(Number(event.target.value))}>
          {work.renderJob.timesMs.map((timeMs) => <option key={timeMs} value={timeMs}>{timeMs} ms</option>)}
        </select>
      </label>
      <div className="p1-comparison">
        {([['原始 PNG', original], ['复现 PNG', reproduced]] as const).map(([label, frame]) => <figure key={label}>
          <figcaption>{label} · {time} ms</figcaption>
          {frame && <img src={`/api/v1/artifacts/${encodeURIComponent(frame.artifactId)}`} alt={`${label}，${time} 毫秒`} />}
        </figure>)}
      </div>
      {comparison.frames.length > 0 && <ul className="issue-list">
        {comparison.frames.map((frame) => <li key={frame.timeMs}>
          {frame.timeMs} ms · 平均绝对误差 {percentage(frame.mae)} · 不同像素 {percentage(frame.differentPixelRatio)}
        </li>)}
      </ul>}
    </>}
  </section>
}
