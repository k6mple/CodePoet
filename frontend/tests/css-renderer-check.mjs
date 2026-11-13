// Real Chromium SVG/WAAPI rendering and export lifecycle; no API fixtures.
import assert from 'node:assert/strict'
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { browserModule } from './browser-modules.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
if (!process.argv[2]) throw new Error('Pass an output directory under local-work-logs')
const out = path.resolve(process.argv[2])
await mkdir(out, { recursive: true })
const registry = await browserModule(new URL('../src/renderers/registry.ts', import.meta.url))
const scene = await browserModule(new URL('../src/renderers/scene.ts', import.meta.url))
const api = await browserModule(new URL('../src/api/render.ts', import.meta.url))
const cases = []
const fixtures = new URL('../../shared/demo/fixtures/', import.meta.url)
for (const name of await readdir(fixtures)) {
  if (name.endsWith('.json')) cases.push({ name, ir: JSON.parse(await readFile(new URL(name, fixtures), 'utf8')) })
}
const browser = await chromium.launch({ channel: 'chrome', headless: true })
try {
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 }, deviceScaleFactor: 1 })
  await page.goto('about:blank')
  const result = await page.evaluate(async ({ registry, scene, api, cases }) => {
    const check = (value, message) => { if (!value) throw new Error(message) }
    const { registry: factory } = (0, eval)(registry)
    const { sampleScene } = (0, eval)(scene)
    const checks = []
    const urls = new Set()
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL)
    URL.createObjectURL = blob => { const url = create(blob); urls.add(url); return url }
    URL.revokeObjectURL = url => { urls.delete(url); revoke(url) }
    const pixels = async blob => {
      const image = await createImageBitmap(blob)
      const canvas = Object.assign(document.createElement('canvas'), { width: image.width, height: image.height })
      canvas.getContext('2d').drawImage(image, 0, 0)
      image.close()
      return canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
    }
    const equal = (a, b) => a.length === b.length && a.every((v, i) => v === b[i])
    for (const { name, ir } of cases) {
      const host = document.createElement('div'); document.body.append(host)
      const renderer = factory.create('css', host)
      check(renderer.backend === 'css' && renderer.rendererVersion === 'codepoet-css-1.0.0', 'real CSS version')
      await renderer.load(ir)
      for (const time of [...new Set([0, ir.durationMs / 2, ir.durationMs])]) {
        await renderer.seek(time)
        const sampled = sampleScene(ir, time)
        const paths = [...host.querySelectorAll('path')]
        check(paths.length === sampled.nodes.length, `${name}: SVG leaves`)
        check(host.querySelectorAll('canvas').length === 0, 'CSS preview must be SVG')
        paths.forEach((path, index) => {
          check(path.dataset.nodeId === sampled.nodes[index].id, 'IR array drawing order')
          const node=sampled.nodes[index]
          const expected=node.points.map(([x,y],point)=>`${point?'L':'M'}${x-sampled.viewport.x} ${y-sampled.viewport.y}`).join(' ')+(node.type==='polyline'?'':' Z')
          check(path.getAttribute('d')===expected,'world TRS geometry and viewport crop')
          check(path.getAttribute('fill')===(node.style.fill??'none'),'SVG fill color')
          check(path.getAnimations().length === 1, 'observable WAAPI effect')
          check(Math.abs(Number(getComputedStyle(path).opacity) - sampled.nodes[index].opacity) < 1e-5, 'effective opacity')
          check(path.getAnimations()[0].playState === 'paused', 'one page clock')
          check(path.getAttribute('stroke-width') === String(sampled.nodes[index].style.strokeWidth ?? 1), 'world stroke')
        })
        const first = await renderer.toPNG(), second = await renderer.toPNG()
        check(first.type === 'image/png' && equal(await pixels(first), await pixels(second)), 'stable CSS PNG pixels')
        const bitmap = await createImageBitmap(first)
        check(bitmap.width === ir.viewport.width && bitmap.height === ir.viewport.height, 'viewport dimensions')
        bitmap.close()
        checks.push({ name, timeMs: time, size: first.size, svg: true, waapi: true, stablePixels: true })
      }
      // Queue exports and seeks without awaiting: the first frame must stay frozen.
      await renderer.seek(0)
      const zero = await renderer.toPNG()
      const pending = renderer.toPNG()
      const seek = renderer.seek(ir.durationMs)
      check(equal(await pixels(await pending), await pixels(zero)), 'seek/export serialization')
      await seek
      const animations = [...host.querySelectorAll('path')].flatMap(path => path.getAnimations())
      renderer.dispose()
      check(host.children.length === 0 && animations.every(animation => animation.playState === 'idle'), 'dispose SVG and animations')
      let rejected = false
      try { await renderer.seek(0) } catch { rejected = true }
      check(rejected && urls.size === 0, 'disposed operations and URLs')
      host.remove()
    }
    // Pure server state-machine checks: explicitly not a real Turtle acceptance.
    const { pollRenderJob, uploadRenderFrames } = (0, eval)(api)
    const completed={id:'canvas-job',generationId:'g',backend:'canvas2d',executionMode:'browser_upload',
      status:'succeeded',timesMs:[0],rendererVersion:'codepoet-canvas-1.0.0',manifest:{irHash:'hash',frames:[{timeMs:0}]}}
    globalThis.fetch=()=>{throw new Error('completed job must never upload again')}
    check((await uploadRenderFrames(completed,{id:'g',backend:'canvas2d',irHash:'hash'},[0],()=>{})).id===completed.id,'successful checkpoint resumes without new exports/uploads')
    const initial = { id:'job', generationId:'generation', backend:'turtle', executionMode:'server', status:'queued', phase:'queued', timesMs:[0] }
    const generation = { id:'generation', backend:'turtle', irHash:'hash' }
    const done = { ...initial, status:'succeeded', phase:'completed', manifest:{ irHash:'hash', backend:'turtle', frames:[{timeMs:0}] } }
    let calls = 0
    globalThis.fetch = async (url, options) => {
      check(String(url) === '/api/v1/render-jobs/job' && !options.body, 'server polling never uploads PNG')
      calls++
      return Response.json(calls === 1 ? {...initial,status:'running'} : done)
    }
    check((await uploadRenderFrames(initial, generation, [0], () => {})).status === 'succeeded' && calls === 2, 'queued-running-succeeded')
    for (const status of ['failed','timed_out','cancelled']) {
      let rejected = false
      try { await pollRenderJob({...initial,status}, () => {}) } catch { rejected = true }
      check(rejected && calls === 2, 'terminal stops')
    }
    const controller = new AbortController()
    const waiting = pollRenderJob(initial, () => {}, controller.signal)
    controller.abort()
    await waiting.then(() => { throw new Error('abort must reject') }, () => {})
    check(calls === 2, 'unmount/abort stops pending poll')
    globalThis.fetch = async () => { throw new Error('offline') }
    await pollRenderJob(initial, () => {}).then(() => { throw new Error('offline must reject') }, () => {})
    globalThis.fetch = async url => { check(String(url).endsWith('/job'), 'retry retains job ID'); return Response.json(done) }
    check((await pollRenderJob(initial, () => {})).id === initial.id, 'resume same job after network failure')
    return { status:'passed', rendererVersion:'codepoet-css-1.0.0', checks, serverPolling:'pure-state-tests-only', objectURLs:urls.size }
  }, { registry, scene, api, cases })
  assert.ok(result.checks.length > 10)
  await writeFile(path.join(out, 'result.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify({status:result.status, frames:result.checks.length, objectURLs:result.objectURLs, serverPolling:result.serverPolling}))
} finally { await browser.close() }
