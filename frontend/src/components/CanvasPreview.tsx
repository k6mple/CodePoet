'use client'

import { useEffect, useRef, useState } from 'react'
import type { DrawingIR } from '@/types/core'
import { renderScene } from '@/renderers/canvas/renderScene'
import { registry, hasBrowserRenderer } from '@/renderers/registry'
import type { Renderer } from '@/renderers/types'

function CssPreview({ ir, timeMs }: { ir: DrawingIR; timeMs: number }) {
  const host = useRef<HTMLDivElement>(null)
  const renderer = useRef<Renderer | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const instance = registry.create('css', host.current!)
    renderer.current = instance
    void instance.load(ir).catch(cause => {
      if (renderer.current === instance) setError(cause instanceof Error ? cause.message : '绘制失败')
    })
    return () => { renderer.current = null; instance.dispose() }
  }, [ir])
  useEffect(() => {
    const instance = renderer.current
    void instance?.seek(timeMs).then(() => {
      if (renderer.current === instance) setError('')
    }).catch(cause => {
      if (renderer.current === instance) setError(cause instanceof Error ? cause.message : '绘制失败')
    })
  }, [ir, timeMs])
  return <div className="canvas-wrap"><div ref={host} className="css-preview" />
    {error && <p className="error" role="alert">绘制失败：{error}</p>}
  </div>
}

function CanvasFrame({ ir, timeMs = 0 }: { ir: DrawingIR; timeMs?: number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    try {
      const ctx = ref.current?.getContext('2d')
      if (!ctx) throw new Error('Canvas2D 不可用')
      renderScene(ctx, ir, timeMs)
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '绘制失败')
    }
  }, [ir, timeMs])

  return <div className="canvas-wrap">
    <canvas ref={ref} width={ir.viewport.width} height={ir.viewport.height} role="img" aria-label="Canvas 作品预览">
      当前浏览器不支持 Canvas。
    </canvas>
    {error && <p className="error" role="alert">绘制失败：{error}</p>}
  </div>
}

export default function CanvasPreview({ ir, timeMs = 0, backend = 'canvas2d' }: { ir: DrawingIR; timeMs?: number; backend?: string }) {
  if (!hasBrowserRenderer(backend)) return <p role="status">当前浏览器没有 {backend} 适配器，无法重绘；服务器保存的 PNG 仍可查看。</p>
  return backend === 'css' ? <CssPreview ir={ir} timeMs={timeMs} /> : <CanvasFrame ir={ir} timeMs={timeMs} />
}
