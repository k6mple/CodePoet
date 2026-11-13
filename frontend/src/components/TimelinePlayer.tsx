'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import type { DrawingIR, Provenance } from '@/types/core'
import CanvasPreview from './CanvasPreview'
import { hasBrowserRenderer } from '@/renderers/registry'

export default function TimelinePlayer({ ir, backend = 'canvas2d', linkEntities = false, paused = false, initialTime = 0, onTimeChange, entities = [] }: {
  ir: DrawingIR
  backend?: string
  linkEntities?: boolean
  paused?: boolean
  initialTime?: number
  onTimeChange?: (timeMs: number) => void
  entities?: Provenance['matchedEntities']
}) {
  const timeRef = useRef(Math.max(0, Math.min(initialTime, ir.durationMs)))
  const [time, setTime] = useState(timeRef.current)
  const [playing, setPlaying] = useState(false)

  const narrative = useMemo(
    () => [...ir.narrative].sort((left, right) => left.timeMs - right.timeMs),
    [ir.narrative],
  )
  const currentNarrative = useMemo(
    () => [...narrative].reverse().find((entry) => entry.timeMs <= time) ?? null,
    [narrative, time],
  )

  useEffect(() => {
    setPlaying(false)
    timeRef.current = Math.max(0, Math.min(initialTime, ir.durationMs))
    setTime(timeRef.current)
  }, [ir, initialTime])

  useEffect(() => {
    if (paused) setPlaying(false)
  }, [paused])

  useEffect(() => {
    onTimeChange?.(Math.round(time))
  }, [time, onTimeChange])

  useEffect(() => {
    if (!playing || paused || ir.durationMs <= 0 || !hasBrowserRenderer(backend)) return
    const duration = ir.durationMs
    const startTime = timeRef.current
    const startWall = performance.now()
    let frame = 0
    const tick = (now: number) => {
      const next = Math.min(duration, startTime + (now - startWall))
      timeRef.current = next
      setTime(next)
      if (next >= duration) {
        setPlaying(false)
        return
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [playing, paused, ir.durationMs, backend])

  function seek(next: number) {
    const duration = ir.durationMs
    const clamped = Math.max(0, Math.min(duration, next))
    setPlaying(false)
    timeRef.current = clamped
    setTime(clamped)
  }

  function togglePlay() {
    if (ir.durationMs <= 0) return
    if (playing) {
      setPlaying(false)
      return
    }
    if (timeRef.current >= ir.durationMs) {
      timeRef.current = 0
      setTime(0)
    }
    setPlaying(true)
  }

  return <div className="timeline-player">
    <div className="timeline-canvas-frame">
      <CanvasPreview ir={ir} timeMs={time} backend={backend} />
    </div>
    {ir.durationMs > 0
      ? <>
        <fieldset className="timeline-controls" disabled={paused || !hasBrowserRenderer(backend)} aria-label="时间轴播放控制">
          <button type="button" className="button button--secondary button--small" onClick={togglePlay}>{playing ? '暂停' : '播放'}</button>
          <button type="button" className="button button--secondary button--small" disabled={time <= 0} onClick={() => seek(0)}>回到开始</button>
          <input
            type="range"
            min={0}
            max={ir.durationMs}
            step={1}
            value={Math.round(time)}
            aria-label="时间轴"
            onChange={(event) => seek(Number(event.target.value))}
          />
          <span className="mono timeline-time">{Math.round(time)} / {ir.durationMs} ms</span>
        </fieldset>
        {narrative.length > 0 && <div className="timeline-narrative">
          <div className="timeline-markers" role="group" aria-label="叙事时间点">
            {narrative.map((entry, index) => <button
              type="button"
              key={`${entry.timeMs}-${index}`}
              className={`timeline-marker${currentNarrative?.timeMs === entry.timeMs ? ' is-active' : ''}`}
              title={entry.caption}
              disabled={paused}
              onClick={() => seek(entry.timeMs)}
            >{entry.timeMs}ms</button>)}
          </div>
          {currentNarrative && <div className="timeline-caption">
            <p>{currentNarrative.caption || '本时间点没有题记。'}</p>
            {currentNarrative.entityIds.length > 0 && <div className="tag-line">
              <span className="tag-label">知识</span>
              {currentNarrative.entityIds.map((entityId) => {
                const snapshot = entities.find((entity) => entity.entityId === entityId)
                const name = typeof snapshot?.content.name === 'string' ? snapshot.content.name : entityId
                return linkEntities
                  ? <Link className="tag-chip" key={entityId} href={`/knowledge/${encodeURIComponent(entityId)}`} title={entityId}>{name}</Link>
                  : <span className="tag-chip" key={entityId}>{name}</span>
              })}
            </div>}
            {entities.filter((entity) => currentNarrative.entityIds.includes(entity.entityId)).map((entity) =>
              <details className="tech-details" key={entity.entityId}>
                <summary>创作时的知识快照 · rev {entity.revision}</summary>
                <pre>{JSON.stringify(entity.content, null, 2)}</pre>
              </details>)}
          </div>}
        </div>}
      </>
      : <p className="timeline-static">静态画面 · 无时间轴</p>}
  </div>
}
