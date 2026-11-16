'use client'

import { useState } from 'react'
import basic from '../../../shared/demo/fixtures/basic_shapes.json'
import probe from '../../../shared/demo/fixtures/canvas_probe.json'
import animation from '../../../shared/demo/fixtures/animation_sequence.json'
import narrative from '../../../shared/demo/fixtures/narrative_sequence.json'
import crop from '../../../shared/demo/fixtures/parent_child_crop.json'
import lotusTemplate from '../../../shared/demo/templates/lotus.json'
import bambooTemplate from '../../../shared/demo/templates/bamboo.json'
import narrativeTemplate from '../../../shared/demo/templates/narrative.json'
import type { DrawingIR } from '@/types/core'
import TimelinePlayer from './TimelinePlayer'
import { exportBackendFrame } from '@/renderers/export'
import { errorMessage } from '@/api/client'
import { InlineNotice } from './Feedback'
import IRControls from './IRControls'

type TemplateSeed = { versions: { irTemplate: DrawingIR }[] }
const templateIr = (seed: unknown) => (seed as TemplateSeed).versions[0].irTemplate

const fixtures = [
  { value: 'probe', label: 'canvas_probe · 方向与尺寸', ir: probe as DrawingIR },
  { value: 'basic', label: 'basic_shapes · 分组与图元', ir: basic as DrawingIR },
  { value: 'animation', label: 'animation_sequence · 3000ms 动画', ir: animation as DrawingIR },
  { value: 'narrative', label: 'narrative_sequence · 4000ms 叙事', ir: narrative as DrawingIR },
  { value: 'crop', label: 'parent_child_crop · 父子变换与裁剪', ir: crop as DrawingIR },
  { value: 'lotus', label: '莲·清正 · 模板原始 IR', ir: templateIr(lotusTemplate) },
  { value: 'bamboo', label: '竹·守正 · 模板原始 IR', ir: templateIr(bambooTemplate) },
  { value: 'narrative-template', label: '四秒·清莲叙事 · 模板原始 IR', ir: templateIr(narrativeTemplate) },
]

function FixtureEditor({ ir }: { ir: DrawingIR }) {
  const [draft, setDraft] = useState(() => JSON.stringify(ir, null, 2))
  const [selected, setSelected] = useState<string | null>(null)
  const [recorded, setRecorded] = useState('')
  return <details className="tech-details fixture-editor">
    <summary>本地 IR 编辑控件检查</summary>
    <p>这里只检查选择、JSON 和请求输入，不调用后端、不修改上方固定画布，也不创建生成记录或作品。</p>
    <IRControls ir={ir} draft={draft} selected={selected} onDraft={setDraft} onSelect={setSelected}
      disabled={false} subgraph applyLabel="记录完整 IR 输入" subgraphLabel="记录子树输入"
      onApply={(value) => setRecorded(JSON.stringify({ kind: '完整 IR', ir: value }, null, 2))}
      onSubgraph={(value) => setRecorded(JSON.stringify({ kind: '子树', ...value }, null, 2))} />
    {recorded && <output aria-label="本地控件输入记录">
      <p>本地记录，未请求服务器。</p><pre>{recorded}</pre>
    </output>}
  </details>
}

export default function FixtureViewer() {
  const [name, setName] = useState('probe')
  const [backend, setBackend] = useState('canvas2d')
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState(false)
  const selected = fixtures.find((item) => item.value === name) ?? fixtures[0]
  const ir = selected.ir

  async function download() {
    setExporting(true)
    setError('')
    try {
      const blob = await exportBackendFrame(backend, ir, ir.durationMs)
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
        <p>本地固定 IR，仅用于验证绘制、父子变换、裁剪、时间轴采样与 PNG 导出；不代表服务器生成、上传或保存成功。</p>
      </div>
    </header>

    <div className="fixture-layout">
      <aside className="fixture-controls" aria-labelledby="fixture-controls-heading">
        <h2 id="fixture-controls-heading">验证数据</h2>
        <label className="field">本地渲染后端
          <select aria-label="本地渲染后端" value={backend} disabled={exporting} onChange={event => setBackend(event.target.value)}>
            <option value="canvas2d">Canvas 2D</option><option value="css">CSS / SVG</option><option value="webgl">WebGL</option>
          </select>
        </label>
        <label className="field">
          <span>IR 样例</span>
          <select value={name} onChange={(event) => { setName(event.target.value); setError('') }}>
            {fixtures.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
          </select>
        </label>
        <dl className="fixture-meta">
          <div><dt>画布</dt><dd className="mono">{ir.canvas.width} × {ir.canvas.height}</dd></div>
          <div><dt>视口</dt><dd className="mono">{ir.viewport.x}, {ir.viewport.y} · {ir.viewport.width} × {ir.viewport.height}</dd></div>
          <div><dt>节点</dt><dd className="mono">{ir.nodes.length}</dd></div>
          <div><dt>时长</dt><dd className="mono">{ir.durationMs === 0 ? '静态' : `${ir.durationMs}ms`}</dd></div>
          <div><dt>叙事</dt><dd className="mono">{ir.narrative.length} 个时间点</dd></div>
        </dl>
        <button type="button" className="button button--primary" disabled={exporting} onClick={() => void download()}>
          {exporting ? '导出中…' : '导出结束帧 PNG'}
        </button>
        <p className="fixture-note">导出使用 IR 的结束时刻和 viewport 尺寸，不包含页面边框、时间轴控件、说明文字或工具栏。</p>
        {error && <InlineNotice tone="error" title="导出失败">{error}</InlineNotice>}
      </aside>

      <div className="fixture-stage">
        <p className="fixture-badge">本地示例 · 非服务器生成</p>
        <div className="stage-mount">
          <div className="stage-surface">
            <TimelinePlayer key={`${name}:${backend}`} ir={ir} backend={backend} paused={exporting} />
          </div>
          <div className="stage-caption">
            <span>{selected.label}</span>
            <span className="mono">{ir.viewport.width} × {ir.viewport.height}</span>
          </div>
        </div>
        <p className="fixture-note">时间轴只在本地 IR 上播放；真实作品的服务器 PNG 与生成记录在作品详情页，不使用这里的示例冒充。</p>
      </div>
    </div>
    <FixtureEditor key={name} ir={ir} />
  </section>
}
