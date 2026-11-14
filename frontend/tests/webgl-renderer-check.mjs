// Real Chromium WebGL geometry, transparency, animation, crop and lifecycle
// checks. No API fixtures: the renderer is exercised directly in the browser.
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { browserModule } from './browser-modules.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
if (!process.argv[2]) throw new Error('Pass an output directory under frontend/local-work-logs')
const out = path.resolve(process.argv[2])
const localRoot = path.resolve(fileURLToPath(new URL('../local-work-logs/', import.meta.url)))
const relative = path.relative(localRoot, out)
if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
  throw new Error('Output directory must be under frontend/local-work-logs')
}
await mkdir(out, { recursive: true })
const registry = await browserModule(new URL('../src/renderers/registry.ts', import.meta.url))
const exporter = await browserModule(new URL('../src/renderers/export.ts', import.meta.url))

const WHITE = [255, 255, 255, 255]
const RED = [255, 0, 0, 255]
const GREEN = [0, 255, 0, 255]
const BLUE = [0, 0, 255, 255]

function ir({ viewport = { x: 0, y: 0, width: 800, height: 600 }, nodes = [], animations = [], background = '#FFFFFF' }) {
  return {
    schemaVersion: '1.0',
    seed: 7,
    canvas: { width: 800, height: 600, background },
    viewport,
    durationMs: animations.length ? 4000 : 0,
    nodes,
    animations,
    narrative: [],
  }
}

const cases = [
  {
    name: 'transparent-overlap',
    capture: true,
    ir: ir({
      nodes: [
        { id: 'a', parentId: null, type: 'polygon', geometry: { points: [[100, 100], [400, 100], [400, 400], [100, 400]] }, style: { fill: '#FF0000', stroke: null, strokeWidth: 0, opacity: 0.5 } },
        { id: 'b', parentId: null, type: 'polygon', geometry: { points: [[250, 250], [550, 250], [550, 550], [250, 550]] }, style: { fill: '#0000FF', stroke: null, strokeWidth: 0, opacity: 0.5 } },
      ],
    }),
    times: [0],
    samples: [
      { label: 'red only', x: 150, y: 150, expected: [255, 128, 128, 255], tolerance: 4 },
      { label: 'blue only', x: 500, y: 500, expected: [128, 128, 255, 255], tolerance: 4 },
      { label: 'red then blue overlap', x: 300, y: 300, expected: [128, 64, 192, 255], tolerance: 4 },
      { label: 'background', x: 50, y: 50, expected: WHITE, tolerance: 2 },
    ],
  },
  {
    name: 'colored-background',
    capture: true,
    ir: ir({
      background: '#102030',
      nodes: [
        { id: 'c', parentId: null, type: 'circle', geometry: { cx: 400, cy: 300, r: 80 }, style: { fill: '#FF0000', stroke: null, strokeWidth: 0, opacity: 1 } },
      ],
    }),
    times: [0],
    samples: [
      { label: 'clear uses IR background', x: 50, y: 50, expected: [16, 32, 48, 255], tolerance: 2 },
      { label: 'shape on colored background', x: 400, y: 300, expected: RED, tolerance: 4 },
    ],
  },
  {
    name: 'concave-polygon',
    capture: true,
    ir: ir({
      nodes: [
        { id: 'l-shape', parentId: null, type: 'polygon', geometry: { points: [[100, 100], [400, 100], [400, 200], [200, 200], [200, 400], [100, 400]] }, style: { fill: '#00FF00', stroke: null, strokeWidth: 0, opacity: 1 } },
      ],
    }),
    times: [0],
    samples: [
      { label: 'top arm', x: 150, y: 150, expected: GREEN, tolerance: 4 },
      { label: 'top-right arm', x: 300, y: 150, expected: GREEN, tolerance: 4 },
      { label: 'left arm', x: 150, y: 300, expected: GREEN, tolerance: 4 },
      { label: 'notch', x: 300, y: 300, expected: WHITE, tolerance: 2 },
      { label: 'outside', x: 450, y: 450, expected: WHITE, tolerance: 2 },
    ],
  },
  {
    name: 'parent-trs',
    capture: true,
    ir: ir({
      nodes: [
        { id: 'g', parentId: null, type: 'group', transform: { x: 400, y: 300, rotation: 90, scaleX: 2, scaleY: 2 }, style: { opacity: 0.5 } },
        { id: 'c', parentId: 'g', type: 'circle', geometry: { cx: 50, cy: 0, r: 20 }, style: { fill: '#FF0000', stroke: null, strokeWidth: 0, opacity: 1 } },
      ],
    }),
    times: [0],
    samples: [
      { label: 'rotated/scaled center with ancestor opacity', x: 400, y: 400, expected: [255, 128, 128, 255], tolerance: 4 },
      { label: 'outside 40px radius', x: 400, y: 350, expected: WHITE, tolerance: 2 },
      { label: 'away', x: 400, y: 300, expected: WHITE, tolerance: 2 },
    ],
  },
  {
    name: 'thick-stroke-crop',
    capture: true,
    ir: ir({
      nodes: [
        { id: 'line', parentId: null, type: 'polyline', geometry: { points: [[-100, 300], [900, 300]] }, style: { fill: null, stroke: '#0000FF', strokeWidth: 20, opacity: 1 } },
      ],
    }),
    times: [0],
    samples: [
      { label: 'stroke center', x: 400, y: 300, expected: BLUE, tolerance: 4 },
      { label: 'inside half width', x: 400, y: 309, expected: BLUE, tolerance: 4 },
      { label: 'outside half width', x: 400, y: 315, expected: WHITE, tolerance: 2 },
      { label: 'clipped left edge', x: 5, y: 300, expected: BLUE, tolerance: 4 },
    ],
  },
  {
    name: 'parent-scaled-stroke',
    capture: true,
    ir: ir({
      nodes: [
        { id: 'g', parentId: null, type: 'group', transform: { x: 0, y: 0, rotation: 0, scaleX: 2, scaleY: 2 }, style: { opacity: 1 } },
        { id: 'line', parentId: 'g', type: 'polyline', geometry: { points: [[100, 150], [350, 150]] }, style: { fill: null, stroke: '#0000FF', strokeWidth: 20, opacity: 1 } },
      ],
    }),
    times: [0],
    samples: [
      { label: 'scaled geometry center', x: 400, y: 300, expected: BLUE, tolerance: 4 },
      { label: 'logical half width', x: 400, y: 309, expected: BLUE, tolerance: 4 },
      { label: 'stroke width must not scale', x: 400, y: 315, expected: WHITE, tolerance: 2 },
    ],
  },
  {
    name: 'stroked-polygon-opacity',
    capture: true,
    ir: ir({
      nodes: [
        { id: 'rect', parentId: null, type: 'polygon', geometry: { points: [[200, 200], [600, 200], [600, 400], [200, 400]] }, style: { fill: null, stroke: '#0000FF', strokeWidth: 20, opacity: 0.5 } },
      ],
    }),
    times: [0],
    samples: [
      { label: 'stroke edge', x: 400, y: 200, expected: [128, 128, 255, 255], tolerance: 4 },
      { label: 'miter corner once', x: 200, y: 200, expected: [128, 128, 255, 255], tolerance: 4 },
      { label: 'inside is empty', x: 400, y: 300, expected: WHITE, tolerance: 2 },
    ],
  },
  {
    name: 'stroke-caps-and-miter',
    capture: true,
    ir: ir({
      nodes: [
        { id: 'v', parentId: null, type: 'polyline', geometry: { points: [[200, 300], [400, 300], [400, 500]] }, style: { fill: null, stroke: '#0000FF', strokeWidth: 12, opacity: 1 } },
      ],
    }),
    times: [0],
    samples: [
      { label: 'inside endpoint', x: 400, y: 494, expected: BLUE, tolerance: 4 },
      { label: 'butt cap stops at endpoint', x: 400, y: 504, expected: WHITE, tolerance: 4 },
      { label: 'outer miter corner', x: 405, y: 295, expected: BLUE, tolerance: 5 },
    ],
  },
  {
    name: 'fill-and-stroke-opacity',
    capture: true,
    ir: ir({
      nodes: [
        { id: 'c', parentId: null, type: 'circle', geometry: { cx: 400, cy: 300, r: 80 }, style: { fill: '#FF0000', stroke: '#0000FF', strokeWidth: 20, opacity: 0.5 } },
      ],
    }),
    times: [0],
    samples: [
      { label: 'fill only center', x: 400, y: 300, expected: [255, 128, 128, 255], tolerance: 4 },
      { label: 'fill then stroke', x: 475, y: 300, expected: [128, 64, 192, 255], tolerance: 5 },
      { label: 'stroke only', x: 485, y: 300, expected: [128, 128, 255, 255], tolerance: 5 },
      { label: 'outside', x: 510, y: 300, expected: WHITE, tolerance: 2 },
    ],
  },
  {
    name: 'stroke-self-overlap',
    capture: true,
    ir: ir({
      nodes: [
        { id: 'dot', parentId: null, type: 'circle', geometry: { cx: 400, cy: 300, r: 2 }, style: { fill: null, stroke: '#0000FF', strokeWidth: 20, opacity: 0.5 } },
      ],
    }),
    times: [0],
    samples: [
      { label: 'tiny circle stroke blends once', x: 400, y: 300, expected: [128, 128, 255, 255], tolerance: 5 },
    ],
  },
  {
    name: 'stroke-backtrack',
    capture: true,
    ir: ir({
      nodes: [
        { id: 'back', parentId: null, type: 'polyline', geometry: { points: [[200, 300], [600, 300], [200, 300]] }, style: { fill: null, stroke: '#0000FF', strokeWidth: 20, opacity: 0.5 } },
      ],
    }),
    times: [0],
    samples: [
      { label: 'backtrack blends once', x: 400, y: 300, expected: [128, 128, 255, 255], tolerance: 5 },
    ],
  },
  {
    name: 'parent-child-animation',
    capture: true,
    ir: ir({
      nodes: [
        { id: 'g', parentId: null, type: 'group', transform: { x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1 }, style: { opacity: 1 } },
        { id: 'c', parentId: 'g', type: 'circle', geometry: { cx: 100, cy: 300, r: 30 }, style: { fill: '#FF0000', stroke: null, strokeWidth: 0, opacity: 1 } },
      ],
      animations: [
        { nodeId: 'g', property: 'x', from: 0, to: 200, startMs: 0, endMs: 4000, easing: 'linear' },
        { nodeId: 'c', property: 'x', from: 0, to: 100, startMs: 0, endMs: 4000, easing: 'linear' },
      ],
    }),
    times: [0, 2000, 4000],
    samples: [
      { label: 't0 center', timeMs: 0, x: 100, y: 300, expected: RED, tolerance: 4 },
      { label: 't0 old position empty', timeMs: 0, x: 300, y: 300, expected: WHITE, tolerance: 2 },
      { label: 't2000 center', timeMs: 2000, x: 250, y: 300, expected: RED, tolerance: 4 },
      { label: 't2000 old position empty', timeMs: 2000, x: 100, y: 300, expected: WHITE, tolerance: 2 },
      { label: 't4000 center', timeMs: 4000, x: 400, y: 300, expected: RED, tolerance: 4 },
      { label: 't4000 old position empty', timeMs: 4000, x: 250, y: 300, expected: WHITE, tolerance: 2 },
    ],
  },
  {
    name: 'viewport-crop-offset',
    capture: true,
    ir: ir({
      viewport: { x: 100, y: 100, width: 400, height: 300 },
      nodes: [
        { id: 'visible', parentId: null, type: 'circle', geometry: { cx: 100, cy: 100, r: 40 }, style: { fill: '#FF0000', stroke: null, strokeWidth: 0, opacity: 1 } },
        { id: 'outside', parentId: null, type: 'circle', geometry: { cx: 700, cy: 500, r: 40 }, style: { fill: '#0000FF', stroke: null, strokeWidth: 0, opacity: 1 } },
      ],
    }),
    times: [0],
    size: [400, 300],
    samples: [
      { label: 'offset center', x: 10, y: 10, expected: RED, tolerance: 4 },
      { label: 'offset background', x: 200, y: 150, expected: WHITE, tolerance: 2 },
      { label: 'outside node not drawn', x: 390, y: 290, expected: WHITE, tolerance: 2 },
    ],
  },
]

let browser
let result
const pageErrors = []
try {
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 }, deviceScaleFactor: 1 })
  page.on('pageerror', error => pageErrors.push(error.message))
  await page.goto('about:blank')
  const evaluated = await page.evaluate(async ({ registry, exporter, cases }) => {
    const check = (value, message) => { if (!value) throw new Error(message) }
    const { registry: factory } = (0, eval)(registry)
    const { exportBackendFrame } = (0, eval)(exporter)
    const checks = []
    const images = []
    const probeCanvas = document.createElement('canvas')
    const probeGl = probeCanvas.getContext('webgl')
    const debugInfo = probeGl?.getExtension('WEBGL_debug_renderer_info')
    const webglContext = {
      available: Boolean(probeGl),
      vendor: debugInfo ? probeGl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) : null,
      renderer: debugInfo ? probeGl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) : null,
      version: probeGl ? probeGl.getParameter(probeGl.VERSION) : null,
    }
    check(Boolean(probeGl), 'WebGL context unavailable')
    probeGl?.getExtension('WEBGL_lose_context')?.loseContext()
    const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))
    const pixels = async blob => {
      const bitmap = await createImageBitmap(blob)
      const canvas = document.createElement('canvas')
      canvas.width = bitmap.width
      canvas.height = bitmap.height
      const context = canvas.getContext('2d')
      context.drawImage(bitmap, 0, 0)
      const data = context.getImageData(0, 0, canvas.width, canvas.height).data
      bitmap.close()
      return { width: canvas.width, height: canvas.height, data }
    }
    const base64 = async blob => {
      const bytes = new Uint8Array(await blob.arrayBuffer())
      let binary = ''
      for (let index = 0; index < bytes.length; index += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000))
      }
      return btoa(binary)
    }
    const checkColor = (name, image, sample) => {
      const offset = (sample.y * image.width + sample.x) * 4
      const actual = [image.data[offset], image.data[offset + 1], image.data[offset + 2], image.data[offset + 3]]
      const tolerance = sample.tolerance ?? 3
      check(sample.expected.every((channel, index) => Math.abs(channel - actual[index]) <= tolerance),
        `${name} ${sample.label}: expected ${sample.expected.join(',')} got ${actual.join(',')}`)
    }
    const findCase = (name) => {
      const found = cases.find((item) => item.name === name)
      check(found, `missing case ${name}`)
      return found
    }
    const rejects = async (operation, message) => {
      let rejected = false
      try { await operation() } catch { rejected = true }
      check(rejected, message)
    }
    const eventOnce = (target, type) => new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`${type} timeout`)), 3000)
      target.addEventListener(type, () => { clearTimeout(timer); resolve() }, { once: true })
    })
    const compareCrossBackend = async (name, sourceIr, timeMs = 0) => {
      const [canvasBlob, webglBlob] = await Promise.all([
        exportBackendFrame('canvas2d', sourceIr, timeMs),
        exportBackendFrame('webgl', sourceIr, timeMs),
      ])
      const [canvasImage, webglImage] = await Promise.all([pixels(canvasBlob), pixels(webglBlob)])
      check(canvasImage.width === webglImage.width && canvasImage.height === webglImage.height, `${name}: cross-backend dimensions`)
      const width = canvasImage.width, height = canvasImage.height, total = width * height
      const mask = (image) => {
        const result = new Uint8Array(total)
        const background = [image.data[0], image.data[1], image.data[2], image.data[3]]
        for (let index = 0; index < total; index += 1) {
          const offset = index * 4
          if (
            Math.abs(image.data[offset] - background[0]) > 8
            || Math.abs(image.data[offset + 1] - background[1]) > 8
            || Math.abs(image.data[offset + 2] - background[2]) > 8
            || Math.abs(image.data[offset + 3] - background[3]) > 8
          ) result[index] = 1
        }
        return result
      }
      // A pixel is "interior" only when its 5x5 neighborhood is foreground in
      // both images. Dilating foreground (the previous version) made every
      // foreground pixel an edge pixel, so interior comparison was vacuous.
      const interior = (foreground) => {
        const result = new Uint8Array(total)
        for (let y = 2; y < height - 2; y += 1) {
          for (let x = 2; x < width - 2; x += 1) {
            const index = y * width + x
            if (!foreground[index]) continue
            let inside = true
            for (let dy = -2; dy <= 2 && inside; dy += 1) {
              for (let dx = -2; dx <= 2; dx += 1) {
                if (!foreground[(y + dy) * width + x + dx]) { inside = false; break }
              }
            }
            if (inside) result[index] = 1
          }
        }
        return result
      }
      const canvasMask = mask(canvasImage)
      const webglMask = mask(webglImage)
      const canvasInterior = interior(canvasMask)
      const webglInterior = interior(webglMask)
      let interiorMismatchPixels = 0, foregroundSymmetricDifference = 0, sumDelta = 0, maxChannelDelta = 0, differentPixels = 0
      for (let index = 0; index < total; index += 1) {
        const offset = index * 4
        let delta = 0
        for (let channel = 0; channel < 4; channel += 1) {
          const current = Math.abs(canvasImage.data[offset + channel] - webglImage.data[offset + channel])
          sumDelta += current
          delta = Math.max(delta, current)
        }
        maxChannelDelta = Math.max(maxChannelDelta, delta)
        if (delta > 2) differentPixels += 1
        if (delta > 2 && canvasInterior[index] && webglInterior[index]) interiorMismatchPixels += 1
        if (canvasMask[index] !== webglMask[index]) foregroundSymmetricDifference += 1
      }
      const foregroundSymmetricDifferenceRatio = foregroundSymmetricDifference / total
      check(interiorMismatchPixels === 0, `${name}: interior cross-backend mismatch ${interiorMismatchPixels}`)
      // A missing/thin feature must fail even when it leaves no interior
      // pixels in one image. AA edge differences stay below this bound.
      check(foregroundSymmetricDifferenceRatio <= 0.02, `${name}: foreground coverage differs ${foregroundSymmetricDifferenceRatio}`)
      return {
        name, timeMs, width, height,
        interiorMismatchPixels,
        foregroundSymmetricDifference,
        foregroundSymmetricDifferenceRatio,
        differentPixels,
        mae: sumDelta / (total * 4),
        maxChannelDelta,
      }
    }

    for (const testCase of cases) {
      const canvas = document.createElement('canvas')
      document.body.append(canvas)
      const renderer = factory.create('webgl', canvas)
      check(renderer.backend === 'webgl' && renderer.rendererVersion === 'codepoet-webgl-1.0.0', `${testCase.name}: real WebGL version`)
      // Do not call getContext before load: that would create a default
      // context (stencil:false) and make the renderer's requested attributes
      // silently ignored.
      await renderer.load(testCase.ir)
      check(renderer.stencilAvailable, `${testCase.name}: stencil buffer unavailable`)
      for (const time of testCase.times) {
        await renderer.seek(time)
        const blob = await renderer.toPNG()
        check(blob.type === 'image/png' && blob.size > 0 && blob.size <= 5 * 1024 * 1024, `${testCase.name}@${time}: PNG limits`)
        const image = await pixels(blob)
        check(image.width === testCase.ir.viewport.width && image.height === testCase.ir.viewport.height, `${testCase.name}@${time}: viewport dimensions`)
        if (testCase.size) check(image.width === testCase.size[0] && image.height === testCase.size[1], `${testCase.name}: configured size`)
        for (const sample of testCase.samples) {
          if (sample.timeMs !== undefined && sample.timeMs !== time) continue
          checkColor(testCase.name, image, sample)
        }
        if (testCase.capture) {
          images.push({ name: `${testCase.name}-${time}.png`, base64: await base64(blob), timeMs: time, width: image.width, height: image.height, size: blob.size })
        }
        checks.push({ name: testCase.name, timeMs: time, width: image.width, height: image.height, size: blob.size })
      }
      // Export must capture the frame it was queued at, even if a later seek
      // targets a different time. Seek back to 0 first so this is not a no-op.
      await renderer.seek(0)
      const zero = await renderer.toPNG()
      const pending = renderer.toPNG()
      const lastTime = testCase.times[testCase.times.length - 1]
      const seek = renderer.seek(lastTime)
      const pendingImage = await pixels(await pending)
      const zeroImage = await pixels(await zero)
      check(pendingImage.data.every((value, index) => value === zeroImage.data[index]), `${testCase.name}: queued export must capture frame 0 before seek`)
      await seek
      if (lastTime !== 0) {
        const lastImage = await pixels(await renderer.toPNG())
        check(!lastImage.data.every((value, index) => value === zeroImage.data[index]), `${testCase.name}: seek to ${lastTime} must change the frame`)
      }
      renderer.dispose()
      renderer.dispose()
      check(canvas.width === 0 && canvas.height === 0, `${testCase.name}: dispose releases backing store`)
    }

    // Context loss must reject operations, restore the same renderer, and redraw identically.
    const lossCanvas = document.createElement('canvas')
    document.body.append(lossCanvas)
    const lossRenderer = factory.create('webgl', lossCanvas)
    await lossRenderer.load(findCase('transparent-overlap').ir)
    const beforeImage = await pixels(await lossRenderer.toPNG())
    const gl = lossCanvas.getContext('webgl')
    const extension = gl?.getExtension('WEBGL_lose_context')
    check(Boolean(extension), 'WEBGL_lose_context unavailable')
    const lostEvent = eventOnce(lossCanvas, 'webglcontextlost')
    const restoredEvent = eventOnce(lossCanvas, 'webglcontextrestored')
    extension.loseContext()
    await lostEvent
    await rejects(() => lossRenderer.seek(0), 'seek while context lost must reject')
    await rejects(() => lossRenderer.toPNG(), 'toPNG while context lost must reject')
    // Chrome wants the loss event to settle before restoreContext().
    await wait(80)
    extension.restoreContext()
    await restoredEvent
    await lossRenderer.seek(0)
    const afterImage = await pixels(await lossRenderer.toPNG())
    check(beforeImage.data.every((value, index) => value === afterImage.data[index]), 'post-restore frame equals pre-loss frame')
    lossRenderer.dispose()
    lossRenderer.dispose()
    check(lossCanvas.width === 0 && lossCanvas.height === 0, 'dispose zeroes WebGL backing store')
    await rejects(() => lossRenderer.seek(0), 'seek after dispose must reject')
    await rejects(() => lossRenderer.toPNG(), 'toPNG after dispose must reject')
    await rejects(() => lossRenderer.load(findCase('transparent-overlap').ir), 'load after dispose must reject')

    // Transparent background: compare the WebGL PNG against the Canvas2D
    // straight-alpha PNG at the same pixel, not against a hard-coded color.
    const transparentIr = {
      ...findCase('concave-polygon').ir,
      canvas: { ...findCase('concave-polygon').ir.canvas, background: null },
      nodes: [
        { id: 't', parentId: null, type: 'circle', geometry: { cx: 400, cy: 300, r: 80 }, style: { fill: '#FF0000', stroke: null, strokeWidth: 0, opacity: 0.5 } },
      ],
      animations: [],
      durationMs: 0,
    }
    const transparentCanvas = await exportBackendFrame('canvas2d', transparentIr, 0)
    const transparentWebgl = await exportBackendFrame('webgl', transparentIr, 0)
    const [transparentCanvasImage, transparentWebglImage] = await Promise.all([pixels(transparentCanvas), pixels(transparentWebgl)])
    const transparentOffset = (300 * transparentCanvasImage.width + 400) * 4
    const transparentCanvasPixel = Array.from(transparentCanvasImage.data.slice(transparentOffset, transparentOffset + 4))
    const transparentWebglPixel = Array.from(transparentWebglImage.data.slice(transparentOffset, transparentOffset + 4))
    check(transparentCanvasPixel.every((channel, index) => Math.abs(channel - transparentWebglPixel[index]) <= 2),
      `transparent background differs: canvas ${transparentCanvasPixel.join(',')} webgl ${transparentWebglPixel.join(',')}`)
    check(transparentCanvasImage.width === transparentWebglImage.width && transparentCanvasImage.height === transparentWebglImage.height, 'transparent background dimensions')
    // The backend IR schema requires a non-null background; this is a
    // frontend-defensive alpha path, not a normal contract fixture.
    for (const [x, y] of [[0, 0], [700, 500]]) {
      const offset = (y * transparentWebglImage.width + x) * 4
      check(transparentCanvasImage.data[offset + 3] === 0 && transparentWebglImage.data[offset + 3] === 0,
        `transparent background must stay transparent at ${x},${y}`)
    }
    images.push({ name: 'transparent-canvas.png', base64: await base64(transparentCanvas), width: transparentCanvasImage.width, height: transparentCanvasImage.height, size: transparentCanvas.size })
    images.push({ name: 'transparent-webgl.png', base64: await base64(transparentWebgl), width: transparentWebglImage.width, height: transparentWebglImage.height, size: transparentWebgl.size })

    // Cross-backend differences are reported, not used as a hash pass/fail:
    // edges may differ by anti-aliasing, interiors must match.
    const crossBackend = [
      await compareCrossBackend('transparent-overlap', findCase('transparent-overlap').ir, 0),
      await compareCrossBackend('concave-polygon', findCase('concave-polygon').ir, 0),
      await compareCrossBackend('parent-trs', findCase('parent-trs').ir, 0),
      await compareCrossBackend('thick-stroke-crop', findCase('thick-stroke-crop').ir, 0),
      await compareCrossBackend('parent-child-animation@2000', findCase('parent-child-animation').ir, 2000),
    ]

    // A preview canvas is reused when the IR identity changes. dispose(false)
    // must keep the context usable for the next renderer on that canvas.
    const reuseCanvas = document.createElement('canvas')
    document.body.append(reuseCanvas)
    const firstRenderer = factory.create('webgl', reuseCanvas)
    await firstRenderer.load(findCase('concave-polygon').ir)
    firstRenderer.dispose(false)
    const secondRenderer = factory.create('webgl', reuseCanvas)
    await secondRenderer.load(findCase('thick-stroke-crop').ir)
    await secondRenderer.seek(0)
    const reusedBlob = await secondRenderer.toPNG()
    check(reusedBlob.type === 'image/png' && reusedBlob.size > 0, 'same canvas after dispose(false) must render again')
    secondRenderer.dispose()

    // A self-intersecting polyline fill is valid IR but not representable by
    // ear clipping; it must fail loudly instead of silently drawing nothing.
    const bowtieCanvas = document.createElement('canvas')
    document.body.append(bowtieCanvas)
    const bowtieRenderer = factory.create('webgl', bowtieCanvas)
    let selfIntersectingRejected = false
    try {
      await bowtieRenderer.load({
        ...findCase('concave-polygon').ir,
        nodes: [{
          id: 'bowtie', parentId: null, type: 'polyline',
          geometry: { points: [[200, 100], [600, 500], [200, 500], [600, 100]] },
          style: { fill: '#00AA00', stroke: null, strokeWidth: 0, opacity: 1 },
        }],
      })
    } catch {
      selfIntersectingRejected = true
    }
    check(selfIntersectingRejected, 'self-intersecting polyline fill must reject explicitly')
    bowtieRenderer.dispose()

    // exportBackendFrame must create a canvas host for WebGL and return a PNG.
    const exported = await exportBackendFrame('webgl', findCase('thick-stroke-crop').ir, 0)
    const exportedImage = await pixels(exported)
    check(exported.type === 'image/png' && exportedImage.width === 800 && exportedImage.height === 600, 'exportBackendFrame webgl')
    checkColor('exportBackendFrame', exportedImage, findCase('thick-stroke-crop').samples[0])

    return {
      status: 'passed',
      checks,
      images,
      webglContext,
      crossBackend,
      transparentBackend: { canvas: transparentCanvasPixel, webgl: transparentWebglPixel },
      protocol: ['real-webgl-context', 'viewport-size-and-crop', 'fill-orders-and-concave', 'stroke-width-and-clip', 'stroke-width-not-scaled', 'parent-child-animation', 'serial-seek-export', 'context-loss-and-restore', 'dispose', 'transparent-background', 'cross-backend-aa-report', 'exportBackendFrame'],
    }
  }, { registry, exporter, cases })
  if (pageErrors.length) throw new Error(`page errors: ${pageErrors.join(' | ')}`)
  result = { ...evaluated, pageErrors }
} catch (error) {
  result = { status: 'failed', failure: error.stack || error.message, pageErrors, checks: [], images: [], protocol: [] }
  process.exitCode = 1
} finally {
  if (result) {
    for (const image of result.images) {
      await writeFile(path.join(out, image.name), Buffer.from(image.base64, 'base64'))
      delete image.base64
    }
    await writeFile(path.join(out, 'webgl-results.json'), JSON.stringify(result, null, 2) + '\n')
    console.log(JSON.stringify({ status: result.status, checks: result.checks.length, images: result.images.length }, null, 2))
  }
  if (browser) await browser.close()
}
