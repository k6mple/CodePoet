// Real browser Canvas checks; no backend substitutes or simulated HTTP responses.
// Generate a DevTools evaluate_script function:
// node frontend/tests/canvas-browser-checks.mjs data/canvas-check.js
import { writeFile } from 'node:fs/promises'
import { browserModule } from './browser-modules.mjs'

const compiled = await browserModule(new URL('../src/renderers/canvas/renderScene.ts', import.meta.url))
async function checks() {
  const { renderScene, sampleTimes, exportFrame } = exports
  const results = []
  const assert = (name, condition, actual) => {
    results.push({ name, passed: Boolean(condition), actual })
    if (!condition) throw new Error(JSON.stringify(results))
  }
  const canvas = document.createElement('canvas'); canvas.width = 800; canvas.height = 600
  const ctx = canvas.getContext('2d')
  const pixel = (x, y) => Array.from(ctx.getImageData(x, y, 1, 1).data)
  const style = { fill: '#FF0000', stroke: null, strokeWidth: 0, opacity: 1 }
  const ir = { schemaVersion: '1.0', seed: 42, canvas: { width: 800, height: 600, background: null }, viewport: { x: 0, y: 0, width: 800, height: 600 }, durationMs: 2000,
    nodes: [{ id: 'parent', parentId: null, type: 'group', transform: { x: 100, y: 100, rotation: 90, scaleX: 2, scaleY: 1 }, style: { opacity: 0.5 } },
      { id: 'child', parentId: 'parent', type: 'polygon', transform: { x: 10, y: 0 }, geometry: { points: [[0,0], [20,0], [20,20], [0,20]] }, style: { ...style, opacity: 0.5 } }], animations: [], narrative: [] }
  renderScene(ctx, ir, 0)
  assert('parent T×R×S / child translation', pixel(90,140)[0] === 255 && pixel(120,140)[3] === 0, [pixel(90,140), pixel(120,140)])
  assert('ancestor opacity multiplication', pixel(90,140)[3] === 64, pixel(90,140))
  ir.nodes = [{ id: 'line', parentId: null, type: 'polyline', transform: { scaleX: 3, scaleY: 3 }, geometry: { points: [[10,10],[30,10]] }, style: { fill: null, stroke: '#000000', strokeWidth: 4, opacity: 1 } }]
  renderScene(ctx, ir, 0)
  assert('stroke width does not scale', pixel(50,30)[3] === 255 && pixel(50,34)[3] === 0, [pixel(50,30), pixel(50,34)])
  ir.nodes = [{ id: 'circle', parentId: null, type: 'circle', geometry: { cx: 100, cy: 100, r: 20 }, style }]
  ir.animations = [{ nodeId: 'circle', property: 'x', from: 0, to: 200, startMs: 500, endMs: 1500, easing: 'linear' }]
  for (const [time, x] of [[0,100],[1000,200],[2000,300]]) {
    renderScene(ctx, ir, time)
    assert(`animation at ${time}ms`, pixel(x,100)[0] === 255 && pixel(x,100)[3] === 255, pixel(x,100))
  }
  assert('sample times', JSON.stringify(sampleTimes(ir)) === '[0,1000,2000]', sampleTimes(ir))
  ir.durationMs = 0; ir.animations = []; ir.viewport = { x: 90, y: 90, width: 40, height: 30 }
  canvas.width = 40; canvas.height = 30
  renderScene(ctx, ir, 0)
  assert('viewport offset without scaling', pixel(10,10)[0] === 255 && pixel(39,29)[3] === 0, [pixel(10,10),pixel(39,29)])
  const blob = await exportFrame(ir, 0)
  const image = await createImageBitmap(blob)
  assert('real toBlob PNG dimensions', image.width === 40 && image.height === 30 && blob.type === 'image/png', { width: image.width, height: image.height, type: blob.type, bytes: blob.size })
  image.close()
  ir.nodes[0].style = { ...style, fill: null, stroke: null }
  renderScene(ctx, ir, 0)
  assert('null fill/stroke remain transparent and clear old frame', pixel(10,10)[3] === 0, pixel(10,10))
  return results
}
if (!process.argv[2]) throw new Error('Pass an output JavaScript file for DevTools evaluate_script sourcePath')
await writeFile(process.argv[2], `async () => { const exports = ${compiled};\nreturn await (${checks.toString()})();\n}`, 'utf8')
console.log(`Wrote browser Canvas checks: ${process.argv[2]}`)
