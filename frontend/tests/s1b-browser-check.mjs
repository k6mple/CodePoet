// Real production-UI acceptance for S1-B: DSL sandbox (IR base + template base set_param) + policy editor.
// Requires an isolated localhost deployment; creates or uses a generation owned by creator_demo.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'

const { chromium } = createRequire(import.meta.url)('playwright')
const base = process.env.CODEPOET_S1B_BASE_URL
const output = process.env.CODEPOET_S1B_OUTPUT_DIR
const password = process.env.CODEPOET_ACCEPTANCE_PASSWORD
const requestedBaseGeneration = process.env.CODEPOET_S1B_BASE_GENERATION
if (!base || !['localhost', '127.0.0.1'].includes(new URL(base).hostname) || !output || !password) {
  throw new Error('Requires isolated localhost deployment, seed password and output dir')
}
const out = path.resolve(output)
if (!out.toLowerCase().includes(`${path.sep}local-work-logs${path.sep}`)) {
  throw new Error('S1-B acceptance output must stay under a local-work-logs directory')
}
await mkdir(out, { recursive: true })
const result = { status: 'running', scope: 's1b-production-ui-and-template-set-param', checks: [], pageErrors: [] }
const record = (name, details = {}) => result.checks.push({ name, ...details })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
let page

async function actor(username) {
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1100 } })
  const p = await context.newPage()
  p.on('pageerror', (error) => result.pageErrors.push(error.message.replaceAll(password, '[redacted]')))
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

const validSource = JSON.stringify({
  version: 1,
  ops: [
    { op: 'circle', cx: 400, cy: 300, r: 90, fill: '#E8A020', stroke: '#6E8B5A', strokeWidth: 4 },
    { op: 'polygon', points: [[160, 520], [640, 520], [400, 580]], fill: '#6E8B5A' },
  ],
})
const invalidSource = JSON.stringify({ version: 1, ops: [{ op: 'eval', code: 'alert(1)' }] })

async function submit(baseGenerationId, source) {
  await page.getByLabel('基底生成编号').fill(baseGenerationId)
  await page.getByLabel('DSL JSON AST').fill(source)
  await page.getByRole('button', { name: '提交执行', exact: true }).click()
}

function readNestedProperty(node, propPath) {
  return propPath.split('.').reduce((acc, key) => (acc == null ? undefined : acc[key]), node)
}

try {
  page = await actor('creator_demo')
  const templates = await api(page, '/templates?backend=canvas2d&pageSize=50')
  const lotus = templates.items.find((item) => item.id === 'tpl-lotus' && item.publishedVersionId)
    ?? templates.items.find((item) => item.publishedVersionId)
  assert.ok(lotus?.publishedVersionId, 'Requires at least one published canvas2d template')
  const version = await api(page, `/template-versions/${lotus.publishedVersionId}`)
  const numericParam = version.parameterSchema.find(
    (param) => (param.type === 'integer' || param.type === 'number')
      && version.bindings.some((binding) => binding.parameter === param.name),
  )
  assert.ok(numericParam, 'Published template must have a bound numeric parameter')
  const binding = version.bindings.find((item) => item.parameter === numericParam.name)
  const defaultParams = Object.fromEntries(version.parameterSchema.map((param) => [param.name, param.default]))
  const templateGen = await api(page, '/generations', 'POST', {
    mode: 'template',
    templateVersionId: version.id,
    params: defaultParams,
    backend: 'canvas2d',
    seed: 42,
  }, 201)

  let baseGeneration = requestedBaseGeneration
  if (!baseGeneration) {
    const irGen = await api(page, '/generations', 'POST', {
      mode: 'ir',
      baseGenerationId: templateGen.id,
      backend: 'canvas2d',
      ir: templateGen.ir,
    }, 201)
    baseGeneration = irGen.id
  }

  await page.goto('/sandbox')
  await page.getByRole('heading', { name: 'DSL 沙箱', exact: true }).waitFor()
  record('sandbox-page-loads', { irBaseGenerationId: baseGeneration, templateBaseGenerationId: templateGen.id })

  await submit(baseGeneration, validSource)
  await page.getByText('succeeded', { exact: true }).waitFor({ timeout: 90000 })
  const runPanel = page.locator('section[aria-labelledby="sandbox-run-heading"]')
  const runText = await runPanel.innerText()
  assert.match(runText, /墙钟时间/)
  assert.match(runText, /绘图调用/)
  await page.locator('canvas[aria-label$="作品预览"], svg[aria-label="CSS 作品预览"]').first().waitFor()
  record('valid-run-succeeds-with-fragment-preview', { runText: runText.slice(0, 600) })

  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(button => button.textContent?.includes('保存为私有作品'))?.disabled)
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(`S1B-IR-${Date.now()}`)
  await page.getByRole('button', { name: '保存为私有作品', exact: true }).click()
  const savedLink = page.getByRole('link', { name: '打开已保存的作品', exact: true })
  await savedLink.waitFor({ timeout: 90000 })
  const savedHref = await savedLink.getAttribute('href')
  record('fragment-saved-as-work', { href: savedHref })

  // Verify PR #30: template-base set_param succeeds in production UI and updates bound node property
  const targetValue = numericParam.default === numericParam.max ? numericParam.min : numericParam.max
  const setParamSource = JSON.stringify({
    version: 1,
    ops: [
      { op: 'set_param', name: numericParam.name, value: targetValue },
      { op: 'circle', cx: 400, cy: 300, r: 36, fill: '#E8A020', stroke: '#6E8B5A', strokeWidth: 3 },
    ],
  })
  await submit(templateGen.id, setParamSource)
  await page.getByText('succeeded', { exact: true }).waitFor({ timeout: 90000 })
  const previewHeader = await page.locator('section[aria-labelledby="sandbox-preview-heading"] .panel-head .mono').innerText()
  const fragmentGenId = previewHeader.split('·').pop().trim()
  const fragmentGen = await api(page, `/generations/${fragmentGenId}`)
  assert.equal(fragmentGen.mode, 'fragment')
  assert.equal(fragmentGen.provenance.baseGenerationId, templateGen.id)
  const boundNode = fragmentGen.ir.nodes.find((node) => node.id === binding.nodeId)
  assert.ok(boundNode, `Bound node ${binding.nodeId} must exist in fragment IR`)
  const actualPropValue = readNestedProperty(boundNode, binding.property)
  assert.equal(actualPropValue, targetValue, `Expected ${binding.nodeId}.${binding.property} to become ${targetValue}, got ${actualPropValue}`)

  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(button => button.textContent?.includes('保存为私有作品'))?.disabled)
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(`S1B-TPL-${Date.now()}`)
  await page.getByRole('button', { name: '保存为私有作品', exact: true }).click()
  await savedLink.waitFor({ timeout: 90000 })
  const tplSavedHref = await savedLink.getAttribute('href')
  record('template-base-set-param-succeeds-and-saves', {
    templateBaseGenerationId: templateGen.id,
    fragmentGenerationId: fragmentGenId,
    parameter: numericParam.name,
    boundNodeId: binding.nodeId,
    boundProperty: binding.property,
    previousValue: numericParam.default,
    updatedValue: actualPropValue,
    savedHref: tplSavedHref,
  })

  // Verify unknown parameter on template base -> DSL_INVALID_BASE + DSL_SET_PARAM_BASE
  const unknownParamSource = JSON.stringify({
    version: 1,
    ops: [{ op: 'set_param', name: 'nonExistentParam', value: 1 }],
  })
  await submit(templateGen.id, unknownParamSource)
  await page.getByText('DSL_INVALID_BASE', { exact: true }).waitFor({ timeout: 90000 })
  const unknownParamAudit = await runPanel.innerText()
  assert.match(unknownParamAudit, /DSL_SET_PARAM_BASE/)

  // Verify set_param on IR base -> DSL_INVALID_BASE + DSL_SET_PARAM_BASE
  await submit(baseGeneration, JSON.stringify({
    version: 1,
    ops: [{ op: 'set_param', name: numericParam.name, value: targetValue }],
  }))
  await page.getByText('DSL_INVALID_BASE', { exact: true }).waitFor({ timeout: 90000 })
  const irBaseSetParamAudit = await runPanel.innerText()
  assert.match(irBaseSetParamAudit, /DSL_SET_PARAM_BASE/)
  record('set-param-invalid-base-and-unknown-param-rejected', {
    unknownParamSnippet: unknownParamAudit.slice(0, 400),
    irBaseSnippet: irBaseSetParamAudit.slice(0, 400),
  })

  await submit(baseGeneration, invalidSource)
  await page.getByText('DSL_AUDIT_REJECTED', { exact: true }).waitFor({ timeout: 90000 })
  const auditText = await page.locator('section[aria-labelledby="sandbox-run-heading"]').innerText()
  assert.match(auditText, /DSL_OP_WHITELIST/)
  assert.match(auditText, /第 1 行第 0 列/)
  record('invalid-run-shows-audit-line-column', { auditText: auditText.slice(0, 600) })

  await page.screenshot({ path: path.join(out, 'sandbox.png'), fullPage: true })

  const admin = await actor('admin_demo')
  await admin.goto('/admin/policies')
  await admin.getByRole('heading', { name: '执行策略', exact: true }).waitFor()
  const policyText = await admin.locator('section[aria-labelledby="writable-policy-heading"]').innerText()
  assert.match(policyText, /当前硬上限 2000。/)
  assert.ok(!policyText.includes('硬上限 5000ms'), 'Stale hardcoded 5000ms note must not appear')
  record('policy-hard-limits-display-consistent')

  const revisionText = await admin.locator('.page-intro .mono').innerText()
  const initialRevision = Number(revisionText.match(/revision (\d+)/)?.[1])
  assert.ok(initialRevision >= 1)
  const drawCalls = admin.getByLabel('DSL 绘图调用上限')
  const before = Number(await drawCalls.inputValue())
  await drawCalls.fill('1')
  await admin.getByRole('button', { name: '保存策略', exact: true }).click()
  await admin.getByText(/策略已保存，revision \d+/).waitFor()
  const afterSave = Number((await admin.locator('.page-intro .mono').innerText()).match(/revision (\d+)/)?.[1])
  assert.equal(afterSave, initialRevision + 1)
  record('policy-ui-updates-and-bumps-revision', { initialRevision, afterSave })

  // Verify tightened dslMaxDrawCalls=1 actually triggers DSL_CALL_LIMIT in /sandbox UI
  await submit(baseGeneration, validSource)
  await page.getByText('DSL_CALL_LIMIT', { exact: true }).waitFor({ timeout: 90000 })
  const callLimitText = await runPanel.innerText()
  assert.match(callLimitText, /DSL_BUDGET_LIMIT/)
  record('policy-draw-call-limit-enforced-in-sandbox-ui', { snippet: callLimitText.slice(0, 400) })

  const current = await api(admin, '/admin/policies')
  const restored = structuredClone(current.values)
  restored.dslMaxDrawCalls = before
  const raced = await api(admin, '/admin/policies', 'PATCH', { expectedRevision: current.revision, values: restored })
  assert.equal(raced.revision, current.revision + 1)
  await admin.getByRole('button', { name: '保存策略', exact: true }).click()
  await admin.getByText('策略已被其他管理员修改。请重新加载当前版本后再保存。').waitFor()
  await admin.getByRole('button', { name: '重新加载', exact: true }).click()
  await admin.waitForFunction((revision) => document.querySelector('.page-intro .mono')?.textContent?.includes(`revision ${revision}`), raced.revision)
  record('policy-ui-409-shows-reload-action', { restoredDrawCalls: before, revision: raced.revision })

  await admin.screenshot({ path: path.join(out, 'policies.png'), fullPage: true })
  assert.deepEqual(result.pageErrors, [])
  result.status = 'passed'
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
