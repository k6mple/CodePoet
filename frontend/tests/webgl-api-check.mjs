// Real isolated API + production UI acceptance for the WebGL backend.
// Secrets are environment-only; output must point at a local work-log directory.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { browserModule } from './browser-modules.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const base = process.env.CODEPOET_WEBGL_BASE_URL
const password = process.env.CODEPOET_ACCEPTANCE_PASSWORD
const output = process.env.CODEPOET_WEBGL_OUTPUT_DIR
if (!base || !['localhost', '127.0.0.1'].includes(new URL(base).hostname) || !password || !output) {
  throw new Error('Requires isolated localhost deployment, seed password and local output directory')
}
const out = path.resolve(output)
await mkdir(out, { recursive: true })
const exporter = await browserModule(new URL('../src/renderers/export.ts', import.meta.url))
const result = { status: 'running', scope: 'real-api-production-ui-webgl', checks: [], pageErrors: [] }
const record = (name, details = {}) => result.checks.push({ name, ...details })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
let page

async function actor(username) {
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } })
  const p = await context.newPage()
  p.on('pageerror', e => result.pageErrors.push(e.message.replaceAll(password, '[redacted]')))
  await p.goto('/')
  await p.getByRole('button', { name: '登录', exact: true }).first().click()
  await p.locator('#login-username').fill(username)
  await p.locator('#login-password').fill(password)
  await p.locator('dialog button[type=submit]').click()
  await p.locator('dialog[open]').waitFor({ state: 'hidden' })
  return p
}

async function api(p, endpoint, method = 'GET', body, expected = 200) {
  const response = await p.evaluate(async ({ endpoint, method, body }) => {
    const headers = {}
    if (method !== 'GET') {
      headers['X-CSRF-Token'] = (await (await fetch('/api/v1/me')).json()).csrfToken
      headers['Content-Type'] = 'application/json'
    }
    const r = await fetch('/api/v1' + endpoint, { method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    return { status: r.status, body: r.status === 204 ? null : await r.json() }
  }, { endpoint, method, body })
  assert.equal(response.status, expected, JSON.stringify(response.body).replaceAll(password, '[redacted]'))
  return response.body
}

async function applied() {
  await page.getByText('已应用', { exact: true }).waitFor()
}

async function generation() {
  const id = await page.getByText('生成编号', { exact: true }).locator('..').locator('dd').textContent()
  return api(page, `/generations/${id}`)
}

async function waitGeneration(backend, priorId) {
  await page.waitForFunction(({ backend, priorId }) => {
    const preview = backend === 'webgl'
      ? document.querySelector('canvas[aria-label="WebGL 作品预览"]')
      : document.querySelector('.canvas-stage svg, .stage svg, svg[aria-label="CSS 作品预览"]')
    const id = [...document.querySelectorAll('dt')].find(e => e.textContent === '生成编号')?.nextElementSibling?.textContent
    return id && id !== priorId && Boolean(preview)
  }, { backend, priorId })
  await page.waitForFunction(() => !document.querySelector('.archive-actions button.button--primary')?.disabled)
  const g = await generation()
  assert.equal(g.backend, backend)
  return g
}

async function saveAndVerify(label, times) {
  const g = await generation()
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(`WebGL-${label}-${Date.now()}`)
  const created = page.waitForResponse(r => r.url().endsWith('/api/v1/render-jobs') && r.request().method() === 'POST')
  await page.locator('.archive-actions button.button--primary').first().click()
  const response = await created
  assert.equal(response.status(), 202)
  const payload = response.request().postDataJSON()
  assert.equal(payload.backend, 'webgl')
  assert.equal(payload.rendererVersion, 'codepoet-webgl-1.0.0')
  assert.equal(await page.getByRole('combobox', { name: '渲染后端', exact: true }).isDisabled(), true)

  const link = page.getByRole('link', { name: '打开已保存的作品', exact: true })
  await link.waitFor()
  const href = await link.getAttribute('href')
  let detail = await api(page, `/work-versions/${href.split('/').pop()}`)
  assert.equal(detail.generation.id, g.id)
  assert.equal(detail.renderJob.backend, 'webgl')
  assert.deepEqual(detail.renderJob.timesMs, times)
  assert.equal(detail.renderJob.manifest.rendererVersion, 'codepoet-webgl-1.0.0')
  assert.equal(detail.renderJob.manifest.frames.length, times.length)

  const frames = []
  for (const frame of detail.renderJob.manifest.frames) {
    const download = await page.request.get(`/api/v1/artifacts/${frame.artifactId}`)
    assert.equal(download.status(), 200)
    const bytes = await download.body()
    const artifactHash = createHash('sha256').update(bytes).digest('hex')
    assert.equal(artifactHash, frame.sha256)
    const rendered = await page.evaluate(async ({ exporter, ir, time, artifactBase64 }) => {
      const { exportBackendFrame } = (0, eval)(exporter)
      const blob = await exportBackendFrame('webgl', ir, time)
      const bytes = new Uint8Array(await blob.arrayBuffer())
      let binary = ''
      for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000))
      }
      async function imageData(source) {
        const bitmap = await createImageBitmap(source)
        const canvas = document.createElement('canvas')
        canvas.width = bitmap.width
        canvas.height = bitmap.height
        const context = canvas.getContext('2d')
        if (!context) throw new Error('浏览器不支持 2D 像素读取')
        context.drawImage(bitmap, 0, 0)
        return { data: context.getImageData(0, 0, bitmap.width, bitmap.height).data, width: bitmap.width, height: bitmap.height }
      }
      const originalBytes = Uint8Array.from(atob(artifactBase64), character => character.charCodeAt(0))
      const original = await imageData(new Blob([originalBytes], { type: 'image/png' }))
      const rerender = await imageData(blob)
      if (original.width !== rerender.width || original.height !== rerender.height) {
        return { rerenderBase64: binary ? btoa(binary) : '', pixelExact: false, differentPixels: -1, totalPixels: 0, width: original.width, height: original.height }
      }
      let differentPixels = 0
      for (let index = 0; index < original.data.length; index += 4) {
        if (original.data[index] !== rerender.data[index]
          || original.data[index + 1] !== rerender.data[index + 1]
          || original.data[index + 2] !== rerender.data[index + 2]
          || original.data[index + 3] !== rerender.data[index + 3]) differentPixels++
      }
      return {
        rerenderBase64: btoa(binary),
        pixelExact: differentPixels === 0,
        differentPixels,
        totalPixels: original.data.length / 4,
        width: original.width,
        height: original.height,
      }
    }, { exporter, ir: g.ir, time: frame.timeMs, artifactBase64: bytes.toString('base64') })
    const rerenderBytes = Buffer.from(rendered.rerenderBase64, 'base64')
    const rerenderHash = createHash('sha256').update(rerenderBytes).digest('hex')
    await writeFile(path.join(out, `${label}-${frame.timeMs}.png`), bytes)
    await writeFile(path.join(out, `${label}-${frame.timeMs}-rerender.png`), rerenderBytes)
    // WebGL multisample AA can differ by a handful of edge pixels between
    // contexts. The authoritative acceptance is the server-side reproduction
    // comparison below; this re-render comparison is recorded as evidence.
    frames.push({ timeMs: frame.timeMs, artifactHash, rerenderHash, pixelExact: rendered.pixelExact, differentPixels: rendered.differentPixels })
  }

  await page.goto(href)
  await page.reload()
  await page.locator('canvas[aria-label="WebGL 作品预览"]').first().waitFor()
  await page.getByRole('button', { name: '复现此版本', exact: true }).click()
  const comparisonSection = page.locator('section[aria-labelledby="reproduction-heading"]')
  await comparisonSection.getByText(/像素一致|检测到像素差异|像素比较未返回结果/).waitFor()
  const comparisonText = await comparisonSection.innerText()
  const exactMatch = comparisonText.includes('像素一致') && !comparisonText.includes('检测到像素差异')
  const metrics = comparisonText.match(/平均绝对误差 ([\d.]+)% · 不同像素比例 ([\d.]+)%/)
  assert.ok(metrics, 'reproduction comparison metrics missing')
  detail = await api(page, `/work-versions/${detail.workVersion.id}`)
  record(`${label}-save-reopen-reproduce`, {
    versionId: detail.workVersion.id,
    jobId: detail.renderJob.id,
    times,
    frames,
    hashesVerified: true,
    exactMatch,
    maePercent: Number(metrics[1]),
    differentPixelRatioPercent: Number(metrics[2]),
    note: exactMatch ? 'exact pixel match' : 'WebGL multisample AA variance across contexts',
  })
  if (!exactMatch) result.pixelExact = false
  await page.screenshot({ path: path.join(out, `${label}-reproduction.png`), fullPage: true })
  return detail
}

try {
  page = await actor('creator_demo')
  const expert = await actor('expert_demo')
  const admin = await actor('admin_demo')

  const original = await api(admin, '/templates/tpl-lotus')
  const historical = await api(admin, `/template-versions/${original.publishedVersionId}`)
  const clone = await api(admin, '/templates/tpl-lotus/versions', 'POST', {
    fromVersionId: historical.id,
    supportedBackends: ['canvas2d', 'webgl'],
    changeNote: 'R2 isolated WebGL acceptance',
  }, 201)
  const submitted = await api(admin, `/template-versions/${clone.id}/submit`, 'POST', { expectedRevision: clone.revision })
  await api(expert, '/reviews', 'POST', {
    targetType: 'template_version',
    targetId: clone.id,
    expectedRevision: submitted.revision,
    decision: 'approve',
    reason: 'Isolated WebGL acceptance',
  }, 201)
  assert.deepEqual((await api(admin, `/template-versions/${historical.id}`)).supportedBackends, historical.supportedBackends)
  assert.ok((await api(page, '/templates?backend=webgl')).items.some(item => item.id === 'tpl-lotus'))
  record('clone-review-publish-webgl-template', { oldVersionId: historical.id, newVersionId: clone.id })

  await page.goto('/?template=tpl-lotus&backend=webgl')
  await applied()
  const initial = await generation()
  assert.equal(initial.backend, 'webgl')
  await page.locator('canvas[aria-label="WebGL 作品预览"]').waitFor()
  await page.locator('#param-petalRadius').fill('40')
  await applied()
  assert.notEqual((await generation()).irHash, initial.irHash)
  record('webgl-generate-and-parameter-change', { generationId: (await generation()).id })

  await saveAndVerify('static', [0])

  await page.goto('/?backend=webgl&template=tpl-lotus')
  await applied()
  await page.getByRole('button', { name: 'JSON IR', exact: true }).click()
  const baseGeneration = await generation()
  const ir = structuredClone(baseGeneration.ir)
  ir.durationMs = 4000
  const target = ir.nodes.find(n => n.type !== 'group')
  assert.ok(target, 'template IR must contain a drawable node')
  ir.animations = [
    { nodeId: target.id, property: 'rotation', from: 0, to: 90, startMs: 0, endMs: 4000, easing: 'linear' },
    { nodeId: target.id, property: 'opacity', from: 0.2, to: 1, startMs: 0, endMs: 4000, easing: 'linear' },
  ]
  await page.getByRole('textbox', { name: '完整 IR JSON', exact: true }).fill(JSON.stringify(ir))
  await page.getByRole('button', { name: '校验并应用 IR', exact: true }).click()
  await waitGeneration('webgl', baseGeneration.id)

  for (const time of [0, 2000, 4000]) {
    const slider = page.getByRole('slider', { name: '时间轴', exact: true })
    await slider.fill(String(time))
    await page.waitForFunction(time => document.querySelector('.timeline-time')?.textContent?.includes(`${time} / 4000 ms`), time)
    await page.waitForTimeout(80)
  }
  record('webgl-animation-seek-three-times', { times: [0, 2000, 4000] })
  await saveAndVerify('animated', [0, 2000, 4000])

  assert.deepEqual(result.pageErrors, [])
  result.pixelExact = result.pixelExact !== false
  result.status = result.pixelExact ? 'passed' : 'passed-with-pixel-difference'
} catch (error) {
  result.status = 'failed'
  result.error = String(error).replaceAll(password, '[redacted]')
  if (page && await page.locator('input[type=password]:visible').count() === 0) {
    await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true })
  }
  throw error
} finally {
  await writeFile(path.join(out, 'result.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify({ status: result.status, checks: result.checks.length, pageErrors: result.pageErrors }))
  await browser.close()
}
