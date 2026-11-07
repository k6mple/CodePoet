'use client'

import { useEffect, useRef, useState } from 'react'
import type { DrawingIR } from '@/types/core'
import { renderScene } from '@/renderers/canvas/renderScene'

export default function CanvasPreview({ ir, timeMs = 0 }: { ir: DrawingIR; timeMs?: number }) {
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
