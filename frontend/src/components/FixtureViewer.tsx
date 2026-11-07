'use client'
import { useState } from 'react'
import basic from '../../../shared/demo/fixtures/basic_shapes.json'
import probe from '../../../shared/demo/fixtures/canvas_probe.json'
import type { DrawingIR } from '@/types/core'
import CanvasPreview from './CanvasPreview'
import { exportFrame } from '@/renderers/canvas/renderScene'
import { errorMessage } from '@/api/client'

export default function FixtureViewer() {
  const [name, setName] = useState('probe')
  const [error, setError] = useState('')
  const ir = (name === 'probe' ? probe : basic) as DrawingIR
  return <section><h1>Canvas fixture 验证</h1><p>本地固定 IR，仅验证绘制与 PNG 导出；不代表真实 API 生成或作品保存成功。</p>
    <label>验证数据<select value={name} onChange={(e) => setName(e.target.value)}><option value="probe">canvas_probe · 方向与尺寸</option><option value="basic">basic_shapes · 分组与图元</option></select></label>
    <CanvasPreview ir={ir} timeMs={ir.durationMs} />
    <button onClick={async () => {
      try {
        const blob = await exportFrame(ir, ir.durationMs)
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a'); a.href = url; a.download = `${name}.png`; a.click()
        setTimeout(() => URL.revokeObjectURL(url), 1000); setError('')
      } catch (cause) { setError(errorMessage(cause)) }
    }}>导出验证 PNG（不保存作品）</button>
    {error && <p role="alert" className="error">{error}</p>}
  </section>
}
