'use client'

import { useState } from 'react'
import basic from '../../../shared/demo/fixtures/basic_shapes.json'
import probe from '../../../shared/demo/fixtures/canvas_probe.json'
import type { DrawingIR } from '@/types/core'
import CanvasPreview from './CanvasPreview'
import { exportFrame } from '@/renderers/canvas/renderScene'
import { errorMessage } from '@/api/client'
import { InlineNotice } from './Feedback'

export default function FixtureViewer() {
  const [name, setName] = useState('probe')
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState(false)
  const ir = (name === 'probe' ? probe : basic) as DrawingIR
  const label = name === 'probe' ? 'canvas_probe · 方向与尺寸' : 'basic_shapes · 分组与图元'

  async function download() {
    setExporting(true)
    setError('')
    try {
      const blob = await exportFrame(ir, ir.durationMs)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${name}.png`
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setExporting(false)
    }
  }

  return <section className="fixture" aria-labelledby="fixture-heading">
    <header className="page-intro page-intro--compact">
      <div>
        <h1 id="fixture-heading">Canvas 校验</h1>
        <p>本地固定 IR，仅用于验证绘制、采样与 PNG 导出；不代表服务器生成、上传或保存成功。</p>
      </div>
    </header>

    <div className="fixture-layout">
      <aside className="fixture-controls" aria-labelledby="fixture-controls-heading">
        <h2 id="fixture-controls-heading">验证数据</h2>
        <label className="field">
          <span>IR 样例</span>
          <select value={name} onChange={(event) => { setName(event.target.value); setError('') }}>
            <option value="probe">canvas_probe · 方向与尺寸</option>
            <option value="basic">basic_shapes · 分组与图元</option>
          </select>
        </label>
        <dl className="fixture-meta">
          <div><dt>画布</dt><dd className="mono">{ir.viewport.width} × {ir.viewport.height}</dd></div>
          <div><dt>节点</dt><dd className="mono">{ir.nodes.length}</dd></div>
          <div><dt>时长</dt><dd className="mono">{ir.durationMs === 0 ? '静态' : `${ir.durationMs}ms`}</dd></div>
        </dl>
        <button type="button" className="button button--primary" disabled={exporting} onClick={() => void download()}>
          {exporting ? '导出中…' : '导出验证 PNG'}
        </button>
        <p className="fixture-note">导出文件使用 IR 的 viewport 尺寸，不包含页面边框、说明文字或工具栏。</p>
        {error && <InlineNotice tone="error" title="导出失败">{error}</InlineNotice>}
      </aside>

      <div className="fixture-stage">
        <p className="fixture-badge">本地示例 · 非服务器生成</p>
        <div className="stage-mount">
          <div className="stage-surface">
            <CanvasPreview ir={ir} timeMs={ir.durationMs} />
          </div>
          <div className="stage-caption">
            <span>{label}</span>
            <span className="mono">{ir.viewport.width} × {ir.viewport.height}</span>
          </div>
        </div>
      </div>
    </div>
  </section>
}
