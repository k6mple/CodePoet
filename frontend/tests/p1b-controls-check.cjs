// Local fixture/component checks only. This does not certify any backend API.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const { chromium } = require('playwright')

const base = process.env.CODEPOET_P1B_BASE_URL || 'http://127.0.0.1:13110'
const out = path.resolve(__dirname, '../work-logs/2026-11-09-p1b-ir-tree-timeline')
fs.mkdirSync(out, { recursive: true })
const result = { scope: 'local-fixture-components-only', status: 'running', checks: [], pageErrors: [], businessWrites: [] }
const record = (name, actual = {}) => result.checks.push({ name, ...actual })
const compile = (source) => ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
} }).outputText
const json = (value) => JSON.parse(JSON.stringify(value))

async function main() {
  const exports = {}
  vm.runInNewContext(compile(fs.readFileSync(path.resolve(__dirname, '../src/utils/irTree.ts'), 'utf8')), { exports })
  const make = (id, parentId = null) => ({ id, parentId, type: 'group' })
  const tree = exports.buildIRTree([make('z'), make('b', 'z'), make('a', 'z'), make('a-root')])
  assert.deepEqual(json(tree.map((layer) => layer.node.id)), ['z', 'a-root'])
  assert.deepEqual(json(tree[0].children.map((layer) => layer.node.id)), ['b', 'a'])
  record('layer-tree-preserves-sibling-draw-order')
  const earlyChild = exports.buildIRTree([make('child', 'parent'), make('parent')])
  assert.equal(earlyChild[0].children[0].node.id, 'child')
  record('parent-link-works-when-child-is-listed-first')
  const damaged = exports.buildIRTree([make('orphan', 'missing'), make('x', 'y'), make('y', 'x')])
  const flatten = (layers) => layers.flatMap((layer) => [layer.node.id, ...flatten(layer.children)])
  assert.deepEqual(json(flatten(damaged)), ['orphan', 'x', 'y'])
  record('orphan-and-cycle-remain-inspectable-without-recursion-loop')

  const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHROME_PATH || 'chrome', headless: true })
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
    const page = await context.newPage()
    page.on('pageerror', (error) => result.pageErrors.push(error.message))
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.startsWith('/api/v1/') && request.method() !== 'GET') {
        result.businessWrites.push({ method: request.method(), path: new URL(request.url()).pathname })
      }
    })
    await page.addInitScript(() => {
      const request = window.requestAnimationFrame.bind(window)
      const cancel = window.cancelAnimationFrame.bind(window)
      window.__p1bFrames = new Set()
      window.requestAnimationFrame = (callback) => {
        const id = request((time) => { window.__p1bFrames.delete(id); callback(time) })
        window.__p1bFrames.add(id)
        return id
      }
      window.cancelAnimationFrame = (id) => { window.__p1bFrames.delete(id); cancel(id) }
    })
    const screenshot = async (name) => {
      await page.evaluate(async () => {
        document.activeElement?.blur()
        scrollTo(0, 0)
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      })
      await page.screenshot({ path: path.join(out, name), fullPage: true })
    }
    await page.goto(`${base}/fixture`)
    const fixture = page.getByRole('combobox', { name: 'IR 样例', exact: true })
    await fixture.selectOption('crop')
    const editor = page.locator('.fixture-editor')
    await editor.locator(':scope > summary').click()
    const radio = (name) => editor.getByRole('radio', { name, exact: true })
    assert.equal(await radio('parent · group').isChecked(), true)
    const nesting = await editor.locator('.ir-node-list').first().evaluate((list) => ({
      roots: [...list.children].map((li) => li.querySelector('input').value),
      children: [...list.querySelector('ul').children].map((li) => li.querySelector('input').value),
    }))
    assert.deepEqual(nesting, { roots: ['parent', 'frame-line'], children: ['child-polygon', 'child-circle'] })
    record('real-component-renders-nested-layers', nesting)
    await radio('parent · group').focus()
    await page.keyboard.press('ArrowDown')
    assert.equal(await radio('child-polygon · polygon').isChecked(), true)
    record('native-keyboard-node-selection')
    await editor.getByText('编辑子树与裁剪', { exact: true }).click()
    await editor.getByText('只读祖先：parent。保留根 ID 和父节点。', { exact: true }).waitFor()
    const subtree = editor.getByRole('textbox', { name: '子树节点 JSON', exact: true })
    const polygon = JSON.parse(await subtree.inputValue())
    assert.deepEqual(polygon.map((node) => node.id), ['child-polygon'])
    polygon[0].style.opacity = 0.55
    const polygonDraft = JSON.stringify(polygon, null, 2)
    await subtree.fill(polygonDraft)
    await radio('child-circle · circle').check()
    const circle = JSON.parse(await subtree.inputValue())
    circle[0].geometry.r = 21
    const circleDraft = JSON.stringify(circle, null, 2)
    await subtree.fill(circleDraft)
    await radio('child-polygon · polygon').check()
    assert.equal(await subtree.inputValue(), polygonDraft)
    await radio('child-circle · circle').check()
    assert.equal(await subtree.inputValue(), circleDraft)
    record('separate-subtree-drafts-survive-node-switching')
    await editor.getByRole('button', { name: '记录子树输入', exact: true }).click()
    const output = editor.getByLabel('本地控件输入记录')
    const payload = JSON.parse(await output.locator('pre').textContent())
    assert.equal(payload.rootNodeId, 'child-circle')
    assert.equal(payload.replacementNodes[0].parentId, 'parent')
    assert.equal(payload.replacementNodes[0].geometry.r, 21)
    assert.deepEqual(payload.viewport, { x: 200, y: 150, width: 400, height: 300 })
    record('subtree-payload-keeps-root-parent-and-crop')
    const lastRecord = await output.textContent()
    circle[0].parentId = null
    await subtree.fill(JSON.stringify(circle))
    await editor.getByRole('button', { name: '记录子树输入', exact: true }).click()
    await editor.getByText('必须保留根节点 ID 和原 parentId；祖先节点保持只读。', { exact: true }).waitFor()
    assert.equal(await output.textContent(), lastRecord)
    await subtree.fill(circleDraft)
    await editor.getByLabel('width', { exact: true }).fill('0')
    await editor.getByRole('button', { name: '记录子树输入', exact: true }).click()
    await editor.getByText('裁剪必须位于画布内，宽度和高度必须大于 0。', { exact: true }).waitFor()
    assert.equal(await output.textContent(), lastRecord)
    await editor.getByLabel('width', { exact: true }).fill('400')
    await editor.getByLabel('x', { exact: true }).fill('2.5')
    await editor.getByRole('button', { name: '记录子树输入', exact: true }).click()
    await editor.getByText('裁剪字段 x 必须是整数。', { exact: true }).waitFor()
    record('invalid-root-and-crop-do-not-replace-recorded-input')
    const completeIR = editor.getByRole('textbox', { name: '完整 IR JSON', exact: true })
    await completeIR.fill('{')
    await editor.getByRole('button', { name: '记录完整 IR 输入', exact: true }).click()
    assert.equal(await output.textContent(), lastRecord)
    assert.equal(await editor.getByRole('alert').count(), 1)
    record('invalid-full-ir-is-rejected-without-side-effects')
    await page.setViewportSize({ width: 390, height: 844 })
    const dimensions = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }))
    assert.ok(dimensions.document <= dimensions.viewport)
    await screenshot('ir-tree-390.png')
    record('ir-editor-mobile-no-overflow', dimensions)
    await page.setViewportSize({ width: 1440, height: 1000 })

    await fixture.selectOption('narrative')
    await page.getByRole('button', { name: '1500ms', exact: true }).click()
    const timeline = page.getByRole('slider', { name: '时间轴', exact: true })
    assert.equal(await timeline.inputValue(), '1500')
    await page.getByText('出淤泥而不染。', { exact: true }).waitFor()
    record('narrative-marker-seeks-to-exact-time')
    await page.getByRole('button', { name: '播放', exact: true }).click()
    await page.waitForFunction(() => Number(document.querySelector('input[aria-label="时间轴"]').value) > 1650)
    await page.getByRole('button', { name: '暂停', exact: true }).click()
    const frozen = await timeline.inputValue()
    await page.waitForTimeout(160)
    assert.equal(await timeline.inputValue(), frozen)
    record('pause-freezes-playback-time')
    await timeline.focus()
    await page.keyboard.press('End')
    assert.equal(await timeline.inputValue(), '4000')
    await page.getByRole('button', { name: '播放', exact: true }).click()
    await page.waitForFunction(() => {
      const value = Number(document.querySelector('input[aria-label="时间轴"]').value)
      return value > 0 && value < 800
    })
    await page.getByRole('button', { name: '暂停', exact: true }).click()
    record('replay-at-end-restarts-from-zero')

    await page.evaluate(() => {
      window.__nativeToBlob = HTMLCanvasElement.prototype.toBlob
      HTMLCanvasElement.prototype.toBlob = function (callback, ...args) {
        window.__nativeToBlob.call(this, (blob) => setTimeout(() => callback(blob), 250), ...args)
      }
    })
    await page.getByRole('button', { name: '播放', exact: true }).click()
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: '导出结束帧 PNG', exact: true }).click()
    assert.equal(await timeline.isDisabled(), true)
    const duringExport = await timeline.inputValue()
    await page.waitForTimeout(100)
    assert.equal(await timeline.inputValue(), duringExport)
    const file = await download
    await file.saveAs(path.join(out, 'narrative-end.png'))
    await page.getByRole('button', { name: '导出结束帧 PNG', exact: true }).waitFor()
    assert.equal(await timeline.isEnabled(), true)
    await page.evaluate(() => { HTMLCanvasElement.prototype.toBlob = window.__nativeToBlob; delete window.__nativeToBlob })
    record('export-pauses-clock-and-restores-controls')

    const renderer = compile(fs.readFileSync(path.resolve(__dirname, '../src/renderers/canvas/renderScene.ts'), 'utf8'))
    const ir = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../shared/demo/fixtures/narrative_sequence.json'), 'utf8'))
    const frames = await page.evaluate(`(async () => { const exports = {}; ${renderer}
      const ir = ${JSON.stringify(ir)}; const results = [];
      for (const time of exports.sampleTimes(ir)) {
        const blob = await exports.exportFrame(ir, time); const bytes = await blob.arrayBuffer();
        const image = await createImageBitmap(blob);
        const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(x=>x.toString(16).padStart(2,'0')).join('');
        results.push({time,width:image.width,height:image.height,bytes:blob.size,signature:Array.from(new Uint8Array(bytes).slice(0,8)),hash}); image.close();
      } return results; })()`)
    assert.deepEqual(frames.map((frame) => frame.time), [0, 2000, 4000])
    for (const frame of frames) {
      assert.equal(frame.width, 800); assert.equal(frame.height, 600)
      assert.deepEqual(frame.signature, [137, 80, 78, 71, 13, 10, 26, 10])
    }
    assert.notEqual(frames[0].hash, frames[1].hash)
    record('real-local-three-frame-png-export', { frames })
    await screenshot('timeline-1440.png')

    await page.getByRole('button', { name: '播放', exact: true }).click()
    await page.waitForFunction(() => Number(document.querySelector('input[aria-label="时间轴"]').value) > 100)
    await page.getByRole('link', { name: '模板库', exact: true }).first().click()
    await page.waitForFunction(() => !document.querySelector('.timeline-player') && window.__p1bFrames.size === 0)
    record('leaving-page-cancels-animation-frame')
    assert.deepEqual(result.pageErrors, [])
    assert.deepEqual(result.businessWrites, [])
    record('fixture-never-writes-generations-or-works')
    result.status = 'passed'
  } catch (error) {
    result.status = 'failed'
    result.failure = error.stack || error.message
  } finally {
    await browser.close()
    fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(result, null, 2) + '\n')
    console.log(JSON.stringify({ status: result.status, checks: result.checks.length, failure: result.failure || null }, null, 2))
    process.exitCode = result.status === 'passed' ? 0 : 1
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
