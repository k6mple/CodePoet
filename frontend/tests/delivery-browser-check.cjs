// Additional real UI/API acceptance for P1-B editing and P1-A backup boundaries.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { chromium } = require('playwright')
const base = process.env.CODEPOET_P1_BASE_URL || 'http://127.0.0.1:13160'
const password = process.env.CODEPOET_ACCEPTANCE_PASSWORD
if (!password || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Requires an isolated local service and credential in environment')
const out = path.resolve(process.env.CODEPOET_DELIVERY_OUTPUT_DIR || 'frontend/work-logs/2026-11-12-p1-integration-delivery/delivery')
fs.mkdirSync(out, { recursive: true })
const result = { base, status: 'running', scope: 'real-subtree-quality-six-type-export-and-rule-import', checks: [], requests: [], pageErrors: [], screenshots: [], failures: [] }
const runId = Date.now().toString(36)
const redact = value => String(value).split(password).join('[redacted]')
const record = (name, details = {}) => result.checks.push({ name, ...details })
let phase = 'login'
let page

async function api(page, endpoint, method = 'GET', body) {
  return page.evaluate(async ({ endpoint, method, body }) => {
    const headers = {}
    if (method !== 'GET') {
      headers['X-CSRF-Token'] = (await (await fetch('/api/v1/me')).json()).csrfToken
      headers['Content-Type'] = 'application/json'
    }
    const response = await fetch(`/api/v1${endpoint}`, { method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    return { status: response.status, body: response.status === 204 ? null : await response.json(), requestId: response.headers.get('x-request-id') }
  }, { endpoint, method, body })
}
const ok = (r, status = 200) => { assert.equal(r.status, status, redact(JSON.stringify(r.body))); return r.body }
async function click(page, button, endpoint, method, status = 200) {
  const [response] = await Promise.all([page.waitForResponse(r => new URL(r.url()).pathname === `/api/v1${endpoint}` && r.request().method() === method), button.click()])
  assert.equal(response.status(), status, redact(await response.text()))
  return response.json()
}
async function actor(browser, username) {
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } })
  const p = await context.newPage()
  p.setDefaultTimeout(15000)
  p.on('pageerror', e => result.pageErrors.push({ actor: username, message: redact(e.message) }))
  p.on('response', r => { const url = new URL(r.url()); if (url.pathname.startsWith('/api/v1/')) result.requests.push({ actor: username, path: url.pathname, method: r.request().method(), status: r.status(), requestId: r.headers()['x-request-id'] || '' }) })
  p.on('dialog', d => d.accept())
  await p.goto('/')
  await p.getByRole('button', { name: '登录', exact: true }).first().click()
  await p.locator('#login-username').fill(username)
  await p.locator('#login-password').fill(password)
  await p.locator('dialog button[type=submit]').click()
  await p.locator('dialog[open]').waitFor({ state: 'hidden' })
  return { context, page: p }
}
async function generation(p) {
  const id = await p.getByText('生成编号', { exact: true }).locator('..').locator('dd').textContent()
  return ok(await api(p, `/generations/${id}`))
}
async function shot(p, name) {
  for (const input of await p.locator('input[type=password]:visible').all()) await input.fill('')
  await p.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0) })
  await p.screenshot({ path: path.join(out, name), fullPage: true })
  result.screenshots.push(name)
}
async function save(p, title) {
  await p.getByRole('textbox', { name: '作品名称', exact: true }).fill(title)
  await p.getByRole('button', { name: '保存私有作品', exact: true }).click()
  const link = p.getByRole('link', { name: '打开已保存的作品', exact: true })
  await link.waitFor()
  const href = await link.getAttribute('href')
  const detail = ok(await api(p, `/work-versions/${href.split('/').pop()}`))
  await p.goto(href)
  await p.getByRole('heading', { name: title, exact: true }).waitFor()
  for (const frame of detail.renderJob.manifest.frames) {
    const r = await p.context().request.get(`/api/v1/artifacts/${frame.artifactId}`)
    assert.equal(r.status(), 200)
    const png = await r.body()
    assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a')
    assert.equal(crypto.createHash('sha256').update(png).digest('hex'), frame.sha256)
  }
  return detail
}

async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  try {
    const owner = await actor(browser, 'creator_demo')
    const admin = await actor(browser, 'admin_demo')
    page = owner.page
    phase = 'subtree-edit-and-crop'
    await page.goto('/?template=tpl-bamboo')
    await page.getByText('已应用', { exact: true }).waitFor()
    const original = await generation(page)
    assert.equal(ok(await api(page, '/engine/capabilities')).features.subgraphEditing, true)
    await page.getByRole('button', { name: 'JSON IR', exact: true }).click()
    const root = original.ir.nodes.find(n => n.type !== 'group' && !original.ir.nodes.some(child => child.parentId === n.id))
    assert.ok(root)
    await page.getByRole('radio', { name: `${root.id} · ${root.type}`, exact: true }).check()
    await page.getByText('编辑子树与裁剪', { exact: true }).click()
    const input = page.getByRole('textbox', { name: '子树节点 JSON', exact: true })
    const apply = page.getByRole('button', { name: '校验并应用子树', exact: true })
    const changed = structuredClone(root)
    changed.transform = { ...changed.transform, x: (changed.transform?.x || 0) + 18 }
    await input.fill(JSON.stringify([{ ...changed, id: 'wrong-root' }]))
    await apply.click()
    await page.getByText('必须保留根节点 ID 和原 parentId；祖先节点保持只读。', { exact: true }).waitFor()
    assert.equal((await generation(page)).id, original.id)
    await input.fill(JSON.stringify([changed]))
    await page.getByRole('spinbutton', { name: 'x', exact: true }).fill('-1')
    await apply.click()
    await page.getByText('裁剪必须位于画布内，宽度和高度必须大于 0。', { exact: true }).waitFor()
    assert.equal((await generation(page)).id, original.id)
    record('invalid-root-and-crop-retain-old-generation')
    const crop = { x: 40, y: 20, width: 680, height: 520 }
    for (const [name, value] of Object.entries(crop)) await page.getByRole('spinbutton', { name, exact: true }).fill(String(value))
    const external = original.ir.nodes.find(n => n.id !== root.id)
    await input.fill(JSON.stringify([changed, external]))
    const rejected = await click(page, apply, `/generations/${original.id}/subgraph`, 'POST', 422)
    assert.equal((await generation(page)).id, original.id)
    record('real-subtree-id-conflict-422-retains-old-generation', { code: rejected.error.code })
    await input.fill(JSON.stringify([changed]))
    const edited = await click(page, apply, `/generations/${original.id}/subgraph`, 'POST', 201)
    const fresh = edited.generation
    assert.notEqual(fresh.id, original.id)
    assert.deepEqual(fresh.ir.viewport, crop)
    for (const node of original.ir.nodes.filter(n => n.id !== root.id)) assert.deepEqual(fresh.ir.nodes.find(n => n.id === node.id), node)
    assert.deepEqual(ok(await api(page, `/generations/${original.id}`)).ir, original.ir)
    record('real-subtree-snapshot-crop-and-unchanged-siblings', { baseGenerationId: original.id, generationId: fresh.id, changedNodeIds: edited.changedNodeIds, viewport: crop })
    await shot(page, 'subtree-applied.png')
    const bamboo = await save(page, `竹·局部编辑-${runId}`)
    assert.equal(bamboo.renderJob.manifest.frames[0].width, crop.width)
    assert.equal(bamboo.renderJob.manifest.frames[0].height, crop.height)
    assert.equal(bamboo.generation.validationReport.parameterValid, true)
    assert.ok(bamboo.renderJob.qualityReport.nonBackgroundPixelRatio > 0)
    await page.reload()
    await page.getByRole('heading', { name: bamboo.workVersion.title, exact: true }).waitFor()
    record('cropped-real-png-save-reopen-and-quality', { workId: bamboo.workVersion.workId, versionId: bamboo.workVersion.id, quality: bamboo.renderJob.qualityReport })

    phase = 'empty-output-quality'
    await page.goto('/?template=tpl-bamboo')
    await page.getByText('已应用', { exact: true }).waitFor()
    const baseGeneration = await generation(page)
    await page.getByRole('button', { name: 'JSON IR', exact: true }).click()
    const blank = structuredClone(baseGeneration.ir)
    for (const node of blank.nodes) node.style.opacity = 0
    await page.getByRole('textbox', { name: '完整 IR JSON', exact: true }).fill(JSON.stringify(blank))
    await click(page, page.getByRole('button', { name: '校验并应用 IR', exact: true }), '/generations', 'POST', 201)
    const blankWork = await save(page, `空白质量检查-${runId}`)
    assert.equal(blankWork.renderJob.qualityReport.emptyOutput, true)
    await page.getByText(/输出为空白，请检查画布/).waitFor()
    record('real-blank-png-quality-warning', { versionId: blankWork.workVersion.id, quality: blankWork.renderJob.qualityReport })
    await shot(page, 'blank-quality-warning.png')

    phase = 'six-type-export-and-rule-import'
    page = admin.page
    await page.goto('/admin/ops')
    await page.getByRole('checkbox', { name: '作品 IR 快照（仅留档，不可导回）', exact: true }).waitFor()
    const boxes = page.locator('fieldset').filter({ has: page.getByText('导出类型', { exact: true }) }).getByRole('checkbox')
    assert.equal(await boxes.count(), 6)
    for (const box of await boxes.all()) assert.equal(await box.isChecked(), true)
    await page.getByRole('checkbox', { name: '作品 IR 快照（仅留档，不可导回）', exact: true }).waitFor()
    const exported = await click(page, page.getByRole('button', { name: '生成导出包', exact: true }), '/admin/exports', 'POST')
    for (const [type, items] of Object.entries(exported.data)) assert.equal(exported.manifest.counts[type], items.length)
    assert.ok(exported.data.rules.length >= 8)
    assert.ok(exported.data.workSnapshots.length >= 3)
    assert.ok(exported.data.workSnapshots.every(s => s.missingArtifacts === false))
    const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: '下载 JSON', exact: true }).click()])
    const stream = await download.createReadStream()
    const chunks = []
    for await (const chunk of stream) chunks.push(chunk)
    assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()), exported)
    record('six-type-ui-export-counts-real-frame-completeness-and-download', { counts: exported.manifest.counts, includesArtifacts: exported.manifest.includesArtifacts })
    await shot(page, 'six-type-export.png')
    await page.setViewportSize({ width: 390, height: 844 })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    await shot(page, 'six-type-export-390.png')
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.getByRole('tab', { name: '导入', exact: true }).click()
    const packageInput = page.getByRole('textbox', { name: '或粘贴/编辑导入包 JSON', exact: true })
    const validate = page.getByRole('button', { name: '校验导入包', exact: true })
    const data = Object.fromEntries(Object.keys(exported.data).map(type => [type, []]))
    const keyword = `交付测试${runId}`
    const rule = { ...exported.data.rules[0], id: `rule-delivery-${runId}`, name: `导回规则-${runId}`, revision: 1, version: 1, priority: 0, match: { keywords: [keyword], entityIds: [], conceptIds: [], tagIds: [], operator: 'any' } }
    data.rules = [rule]
    const pack = { schemaVersion: exported.schemaVersion, exportedAt: new Date().toISOString(), manifest: { includesArtifacts: false, counts: Object.fromEntries(Object.entries(data).map(([key, items]) => [key, items.length])) }, data }
    for (const match of ['bad-shape', { entityIds: [{}] }]) {
      const invalid = structuredClone(pack)
      invalid.data.rules[0].match = match
      await packageInput.fill(JSON.stringify(invalid))
      const checked = await click(page, validate, '/admin/imports/validate', 'POST')
      assert.equal(checked.valid, false)
      assert.ok(checked.errors.some(e => e.code === 'INVALID_SHAPE' && e.path === 'data.rules[0]'))
      assert.equal(await page.getByRole('button', { name: '执行导入', exact: true }).isDisabled(), true)
    }
    record('malformed-rule-match-ui-validation-errors-without-500')
    const snapshotPack = structuredClone(pack)
    snapshotPack.data.rules = []
    snapshotPack.manifest.counts.rules = 0
    snapshotPack.data.workSnapshots = [exported.data.workSnapshots[0]]
    snapshotPack.manifest.counts.workSnapshots = 1
    await packageInput.fill(JSON.stringify(snapshotPack))
    const unsupported = await click(page, validate, '/admin/imports/validate', 'POST')
    assert.equal(unsupported.valid, false)
    assert.equal(await page.getByRole('button', { name: '执行导入', exact: true }).isDisabled(), true)
    record('work-snapshot-import-remains-rejected')
    await packageInput.fill(JSON.stringify(pack))
    const validated = await click(page, validate, '/admin/imports/validate', 'POST')
    assert.equal(validated.valid, true, JSON.stringify(validated.errors))
    const imported = await click(page, page.getByRole('button', { name: '执行导入', exact: true }), '/admin/imports/apply', 'POST', 201)
    assert.equal(imported.counts.rules, 1)
    await owner.page.goto('/')
    await owner.page.getByText('已应用', { exact: true }).waitFor()
    await owner.page.getByRole('button', { name: '语义生成', exact: true }).click()
    await owner.page.getByRole('textbox', { name: '典故或创作描述', exact: true }).fill(keyword)
    const semantic = await click(owner.page, owner.page.getByRole('button', { name: '按语义生成', exact: true }), '/generations', 'POST', 201)
    assert.ok(semantic.provenance.matchedRules.some(r => r.ruleId === rule.id))
    record('exported-rule-reimported-and-real-semantic-generation-hits-it', { ruleId: rule.id, generationId: semantic.id })
    assert.deepEqual(result.pageErrors, [])
    result.status = 'passed'
  } catch (error) {
    result.status = 'failed'
    result.failures.push({ phase, message: redact(error.stack || error.message) })
    if (page) await shot(page, `failure-${phase}.png`).catch(() => {})
  } finally {
    await browser.close()
    fs.writeFileSync(path.join(out, 'delivery-results.json'), redact(JSON.stringify(result, null, 2)) + '\n')
    console.log(JSON.stringify({ status: result.status, checks: result.checks.length, failures: result.failures, out }, null, 2))
    process.exitCode = result.status === 'passed' ? 0 : 1
  }
}
main().catch(error => { console.error(redact(error.stack)); process.exitCode = 1 })
