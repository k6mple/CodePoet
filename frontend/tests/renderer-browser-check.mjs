// Real Chromium pixel regression against the pre-S0-B1 renderer, plus protocol checks.
// Requires Playwright resolvable by Node. Run from the repo root:
// node frontend/tests/renderer-browser-check.mjs <new-output-directory>
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import vm from 'node:vm'
import ts from 'typescript'
import { browserModule } from './browser-modules.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
if (!process.argv[2]) throw new Error('Pass a new output directory')
const out = path.resolve(process.argv[2])
await mkdir(out, { recursive: true })
const baselineRef = 'a90faf1'
const legacySource = execFileSync('git', ['show', `${baselineRef}:frontend/src/renderers/canvas/renderScene.ts`], { encoding: 'utf8' })
const legacy = `(() => { const exports = {}; ${ts.transpileModule(legacySource, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
} }).outputText}; return exports; })()`
const current = await browserModule(new URL('../src/renderers/canvas/renderScene.ts', import.meta.url))
const scene = await browserModule(new URL('../src/renderers/scene.ts', import.meta.url))
const registry = await browserModule(new URL('../src/renderers/registry.ts', import.meta.url))
const result = { status: 'running', scope: 'pure-sampling-and-real-browser-renderer-only', baselineRef,
  baselineSourceSha256: createHash('sha256').update(legacySource).digest('hex'), checks: [] }
const record = (name, details = {}) => result.checks.push({ name, ...details })
const base = { schemaVersion: '1.0', seed: 42, canvas: { width: 200, height: 160, background: null },
  viewport: { x: 0, y: 0, width: 200, height: 160 }, durationMs: 2000, nodes: [], animations: [], narrative: [] }
const style = { fill: '#FF0000', stroke: '#151515', strokeWidth: 3, opacity: 0.5 }
const group = { id: 'parent', parentId: null, type: 'group', transform: { x: 100, y: 100, rotation: 90, scaleX: 2, scaleY: 1 }, style: { opacity: 0.5 } }
const child = { id: 'child', parentId: 'parent', type: 'polygon', transform: { x: 10 },
  geometry: { points: [[0, 0], [20, 0], [20, 20]] }, style }
const trs = { ...base, nodes: [group, child] }

let browser
try {
  // No document/window in this context: sampling must be reusable outside a browser.
  const { sampleScene, sampleTimes } = vm.runInNewContext(scene)
  const sampled = sampleScene(trs, 0)
  assert.ok(Math.abs(sampled.nodes[0].points[0][0] - 100) < 1e-10)
  assert.ok(Math.abs(sampled.nodes[0].points[0][1] - 120) < 1e-10)
  assert.ok(Math.abs(sampled.nodes[0].points[2][0] - 80) < 1e-10)
  assert.ok(Math.abs(sampled.nodes[0].points[2][1] - 160) < 1e-10)
  assert.equal(sampled.nodes[0].opacity, 0.25)
  record('pure-world-TRS-and-ancestor-opacity')
  const ordered = { ...trs, nodes: [child, { ...child, id: 'other', parentId: null }, group] }
  assert.equal(sampleScene(ordered).nodes.map(n => n.id).join(','), 'child,other')
  record('child-before-parent-and-interleaved-array-order')
  const negative = { ...base, nodes: [{ ...child, parentId: null, transform: { x: 20, scaleX: -2, scaleY: 3 } }] }
  assert.equal(JSON.stringify(sampleScene(negative).nodes[0].points[1]), '[-20,0]')
  record('negative-and-nonuniform-scale')
  const animated = { ...base, nodes: [{ ...child, parentId: null, transform: {} }], animations: [
    { nodeId: 'child', property: 'x', from: 10, to: 110, startMs: 500, endMs: 1500, easing: 'linear' },
    { nodeId: 'child', property: 'opacity', from: 0, to: 1, startMs: 0, endMs: 2000, easing: 'linear' },
  ] }
  for (const [time, expectedX, opacity] of [[-1, 10, 0], [500, 10, 0.25], [1000, 60, 0.5], [1500, 110, 0.75], [3000, 110, 1]]) {
    const sample = sampleScene(animated, time)
    assert.equal(sample.nodes[0].points[0][0], expectedX)
    assert.equal(sample.nodes[0].opacity, opacity)
  }
  assert.equal(JSON.stringify(sampleTimes(animated)), '[0,1000,2000]')
  assert.equal(JSON.stringify(sampleTimes({ ...base, durationMs: 0 })), '[0]')
  record('animation-boundaries-and-static-animated-sample-times')
  const before = JSON.stringify(trs)
  sampled.nodes[0].points[0][0] = -999
  sampled.nodes[0].style.fill = '#000000'
  sampled.viewport.x = 999
  assert.equal(JSON.stringify(trs), before)
  record('sampling-does-not-mutate-or-alias-source')
  assert.throws(() => sampleScene({ ...base, nodes: [{ ...child, parentId: 'missing' }] }))
  assert.throws(() => sampleScene({ ...base, nodes: [{ ...group, parentId: 'parent' }] }))
  assert.throws(() => sampleScene({ ...base, nodes: [{ ...child, parentId: null, material: { shaderRef: 'custom' } }] }))
  record('missing-parent-cycle-and-unsupported-material-rejected')

  const fixturesDir = new URL('../../shared/demo/fixtures/', import.meta.url)
  const cases = [{ name: 'trs', ir: trs }, { name: 'array-order', ir: ordered }, { name: 'negative-scale', ir: negative }, { name: 'animation-boundaries', ir: animated }]
  for (const name of await readdir(fixturesDir)) {
    if (name.endsWith('.json')) cases.push({ name, ir: JSON.parse(await readFile(new URL(name, fixturesDir), 'utf8')) })
  }
  // Overlapping independent leaves: grouping must never reorder or composite opacity.
  cases.push({ name: 'overlap-crop-stroke', ir: { ...trs,
    viewport: { x: 70, y: 80, width: 90, height: 70 },
    nodes: [child, { ...child, id: 'over', parentId: null, transform: { x: 85, y: 120, rotation: 35, scaleX: 3 }, style: { ...style, fill: '#0000FF', strokeWidth: 12 } }, group],
    animations: [{ nodeId: 'parent', property: 'rotation', from: 0, to: 180, startMs: 0, endMs: 2000, easing: 'linear' }],
  } })
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage()
  await page.goto('about:blank')
  const pixels = await page.evaluate(async ({ legacy, current, registry, cases }) => {
    const old = (0, eval)(legacy)
    const next = (0, eval)(current)
    const { registry: factory } = (0, eval)(registry)
    const results = []
    const check = (condition, message) => { if (!condition) throw new Error(message) }
    const canvas = (ir) => Object.assign(document.createElement('canvas'), { width: ir.viewport.width, height: ir.viewport.height })
    const data = c => Array.from(c.getContext('2d').getImageData(0, 0, c.width, c.height).data)
    const equal = (a, b) => a.length === b.length && a.every((v, i) => v === b[i])
    const decode = async blob => {
      const image = await createImageBitmap(blob)
      const c = Object.assign(document.createElement('canvas'), { width: image.width, height: image.height })
      c.getContext('2d').drawImage(image, 0, 0); image.close()
      return data(c)
    }
    for (const { name, ir } of cases) {
      for (const time of [...new Set([0, Math.floor(ir.durationMs / 2), ir.durationMs, -1, ir.durationMs + 1])]) {
        const a = canvas(ir), b = canvas(ir)
        old.renderScene(a.getContext('2d'), ir, time)
        next.renderScene(b.getContext('2d'), ir, time)
        check(equal(data(a), data(b)), `${name}@${time}: pixels changed`)
        const blob = await next.exportFrame(ir, time)
        const decoded = await decode(blob)
        const oldBlob = await old.exportFrame(ir, time)
        const oldDecoded = await decode(oldBlob)
        check(equal(oldDecoded, decoded), `${name}@${time}: decoded PNG changed`)
        check(equal(new Uint8Array(await oldBlob.arrayBuffer()), new Uint8Array(await blob.arrayBuffer())), `${name}@${time}: encoded PNG changed`)
        check(blob.type === 'image/png', 'PNG mime')
        results.push({ name, timeMs: time, width: a.width, height: a.height, exactPixels: true })
      }
    }
    const ir = cases.find(c => c.name === 'animation-boundaries').ir
    const preview = canvas(ir), exportHost = canvas(ir)
    next.renderScene(preview.getContext('2d'), ir, 1000)
    const previewPixels = data(preview)
    const renderer = factory.create('canvas2d', exportHost)
    check(renderer.backend === 'canvas2d' && renderer.rendererVersion === old.RENDERER_VERSION, 'version changed')
    await renderer.load(ir)
    // Call without awaiting to prove that seek/encoding cannot race on one instance.
    const work = []
    for (const t of [0, 1000, 2000]) { work.push(renderer.seek(t)); work.push(renderer.toPNG()) }
    const frames = await Promise.all(work)
    for (const [i, t] of [0, 1000, 2000].entries()) {
      check(equal(await decode(await old.exportFrame(ir, t)), await decode(frames[i * 2 + 1])), `queued frame ${t}`)
    }
    await Promise.all([next.exportFrame(ir, 0), next.exportFrame(ir, 2000)])
    check(equal(previewPixels, data(preview)), 'export changed preview')
    // Failed encoding is recoverable and cannot poison the queue.
    const toBlob = exportHost.toBlob.bind(exportHost)
    exportHost.toBlob = callback => callback(null)
    const rejects = async (operation, message) => {
      let failed = false; try { await operation() } catch { failed = true }
      check(failed, message)
    }
    await rejects(() => renderer.toPNG(), 'null encode accepted')
    exportHost.toBlob = callback => callback(new Blob([new Uint8Array(5 * 1024 * 1024 + 1)], { type: 'image/png' }))
    await rejects(() => renderer.toPNG(), 'oversize encode accepted')
    exportHost.toBlob = toBlob
    await renderer.seek(1000); await renderer.toPNG()
    renderer.dispose(); renderer.dispose()
    check(exportHost.width === 0 && exportHost.height === 0, 'backing store not released')
    await rejects(() => renderer.seek(0), 'seek after dispose')
    await rejects(() => renderer.load(ir), 'load after dispose')
    await rejects(() => renderer.toPNG(), 'export after dispose')
    const unloaded = factory.create('canvas2d', canvas(ir))
    await rejects(() => unloaded.seek(0), 'seek before load')
    await rejects(() => unloaded.toPNG(), 'export before load')
    // Dispose while toBlob is pending and ensure queued operations cannot resurrect it.
    const delayedHost = canvas(ir), delayed = factory.create('canvas2d', delayedHost)
    await delayed.load(ir)
    const originalToBlob = delayedHost.toBlob.bind(delayedHost)
    let captured
    delayedHost.toBlob = callback => originalToBlob(blob => { captured = () => callback(blob) })
    const pending = delayed.toPNG()
    const stopped = rejects(() => pending, 'pending encode survived dispose')
    while (!captured) await new Promise(resolve => setTimeout(resolve, 1))
    delayed.dispose(); captured(); await stopped
    await rejects(() => Promise.resolve(factory.create('webgl', document.createElement('div'))), 'WebGL accepted a non-canvas host')
    unloaded.dispose()
    return { frames: results, protocol: ['serial-seek-export', 'private-export-preserves-preview', 'encode-failure-recovery', 'dispose-and-no-resurrection', 'unloaded-and-unavailable-rejected'] }
  }, { legacy, current, registry, cases })
  record('pre-refactor-pixel-and-real-PNG-regression', { frames: pixels.frames })
  pixels.protocol.forEach(name => record(name))
  result.status = 'passed'
} catch (error) {
  result.status = 'failed'
  result.failure = error.stack || error.message
  process.exitCode = 1
} finally {
  await browser?.close()
  await writeFile(path.join(out, 'renderer-results.json'), JSON.stringify(result, null, 2) + '\n')
  console.log(JSON.stringify(result.status === 'passed' ? { status: result.status, checks: result.checks.length,
    pixelFrames: result.checks.find(c => c.frames)?.frames.length } : result, null, 2))
}
