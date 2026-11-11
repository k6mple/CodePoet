// Actual 429, aborted frame transfer and five-minute server expiry; no clock/database manipulation.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { chromium } = require('playwright')
const base = process.env.CODEPOET_P1_BASE_URL || 'http://127.0.0.1:13140'
const password = process.env.CODEPOET_ACCEPTANCE_PASSWORD
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname) || !password) throw new Error('Requires isolated local service and environment credential')
const out = path.resolve(process.env.CODEPOET_RECOVERY_OUTPUT_DIR || 'frontend/work-logs/2026-11-11-main-integration/recovery')
fs.mkdirSync(out, { recursive: true })
const result = { status: 'running', checks: [], requests: [], pageErrors: [] }
const redact = value => String(value).split(password).join('[redacted]')
const record = (name, data = {}) => { result.checks.push({ name, ...data }); console.log(name) }
let phase = 'login'
async function api(page, endpoint, method = 'GET', body) {
  return page.evaluate(async ({ endpoint, method, body }) => {
    const headers = {}
    if (method !== 'GET') {
      headers['X-CSRF-Token'] = (await (await fetch('/api/v1/me')).json()).csrfToken
      headers['Content-Type'] = 'application/json'
    }
    const response = await fetch(`/api/v1${endpoint}`, { method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    return { status: response.status, body: await response.json(), requestId: response.headers.get('x-request-id') }
  }, { endpoint, method, body })
}
async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  let page
  try {
    const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } })
    page = await context.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => result.pageErrors.push(redact(e.message)))
    page.on('response', r => {
      const url = new URL(r.url())
      if (url.pathname.startsWith('/api/v1/')) result.requests.push({ method: r.request().method(), path: url.pathname, status: r.status(), requestId: r.headers()['x-request-id'] || '' })
    })
    await page.goto('/')
    await page.getByRole('button', { name: '登录', exact: true }).first().click()
    await page.locator('#login-username').fill('creator_demo')
    await page.locator('#login-password').fill(password)
    await page.locator('dialog button[type=submit]').click()
    await page.locator('dialog[open]').waitFor({ state: 'hidden' })
    const fresh = async title => {
      await page.goto('/?template=tpl-lotus')
      await page.getByText('已应用', { exact: true }).waitFor()
      await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(title)
      return page.getByText('生成编号', { exact: true }).locator('..').locator('dd').textContent()
    }
    const save = () => page.locator('.archive-actions button.button--primary').first().click()
    const saved = () => page.getByRole('link', { name: '打开已保存的作品', exact: true }).waitFor()
    phase = 'quota'
    const generationId = await fresh(`额度恢复-${Date.now().toString(36)}`)
    const payload = { generationId, backend: 'canvas2d', timesMs: [0], executionMode: 'browser_upload', rendererVersion: 'codepoet-canvas-1.0.0', clientEnvironment: { userAgent: await page.evaluate(() => navigator.userAgent), deviceScaleFactor: 1 } }
    const first = await api(page, '/render-jobs', 'POST', payload)
    const second = await api(page, '/render-jobs', 'POST', payload)
    assert.equal(first.status, 202)
    assert.equal(second.status, 202)
    const limited = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/render-jobs' && r.status() === 429)
    await save()
    const limit = await limited
    await page.getByText('保存未确认', { exact: true }).waitFor()
    record('real-429-retains-editing-state', { requestId: limit.headers()['x-request-id'], code: (await limit.json()).error.code })
    assert.equal((await api(page, `/render-jobs/${first.body.id}/cancel`, 'POST')).status, 200)
    assert.equal((await api(page, `/render-jobs/${second.body.id}/cancel`, 'POST')).status, 200)
    await save()
    await saved()
    record('quota-released-ui-retry-saves')
    phase = 'frame-network-retry'
    await fresh(`上传恢复-${Date.now().toString(36)}`)
    const failedTransfer = async route => route.abort('failed')
    await page.route('**/api/v1/render-jobs/*/frames', failedTransfer)
    await save()
    await page.getByText('保存未确认', { exact: true }).waitFor()
    const jobId = await page.locator('.archive-job').textContent()
    const before = await api(page, `/render-jobs/${jobId}`)
    assert.equal(before.body.status, 'running')
    await page.unroute('**/api/v1/render-jobs/*/frames', failedTransfer)
    await save()
    await saved()
    const after = await api(page, `/render-jobs/${jobId}`)
    assert.equal(after.body.status, 'succeeded')
    record('aborted-frame-upload-retries-original-task', { jobId, manifestFrames: after.body.manifest.frames.length })
    phase = 'real-expiry'
    await fresh(`过期恢复-${Date.now().toString(36)}`)
    await page.route('**/api/v1/render-jobs/*/frames', failedTransfer)
    await save()
    await page.getByText('保存未确认', { exact: true }).waitFor()
    const expiringId = await page.locator('.archive-job').textContent()
    await page.unroute('**/api/v1/render-jobs/*/frames', failedTransfer)
    const expiring = (await api(page, `/render-jobs/${expiringId}`)).body
    const waitUntil = Date.parse(expiring.expiresAt) + 1500
    const startedWaitingAt = new Date().toISOString()
    console.log(`Waiting for real server expiry at ${expiring.expiresAt}`)
    // Small waits keep the runner interruptible; the parent can continue reporting progress.
    while (Date.now() < waitUntil) await page.waitForTimeout(Math.min(1000, waitUntil - Date.now()))
    await save()
    await page.getByText('保存未确认', { exact: true }).waitFor()
    const expired = await api(page, `/render-jobs/${expiringId}`)
    assert.equal(expired.body.status, 'timed_out')
    assert.equal(expired.body.error.code, 'UPLOAD_EXPIRED')
    assert.equal(await page.getByRole('link', { name: '打开已保存的作品', exact: true }).count(), 0)
    record('five-minute-expired-task-does-not-report-success', { jobId: expiringId, createdAt: expiring.createdAt, expiresAt: expiring.expiresAt, startedWaitingAt, checkedAt: new Date().toISOString(), requestId: expired.requestId })
    await page.getByRole('button', { name: '下次新建上传任务', exact: true }).click()
    await save()
    await saved()
    assert.notEqual(await page.locator('.archive-job').textContent(), expiringId)
    record('expired-task-new-task-recovers-and-saves')
    const href = await page.getByRole('link', { name: '打开已保存的作品', exact: true }).getAttribute('href')
    const detail = (await api(page, `/work-versions/${href.split('/').pop()}`)).body
    const frame = detail.renderJob.manifest.frames[0]
    const response = await context.request.get(`/api/v1/artifacts/${frame.artifactId}`)
    assert.equal(response.status(), 200)
    assert.equal(crypto.createHash('sha256').update(await response.body()).digest('hex'), frame.sha256)
    record('recovered-work-real-png-hash-matches', { versionId: detail.workVersion.id, jobId: detail.renderJob.id })
    assert.deepEqual(result.pageErrors, [])
    result.status = 'passed'
  } catch (error) {
    result.status = 'failed'
    result.failure = { phase, message: redact(error.stack || error.message) }
  } finally {
    await browser.close()
    fs.writeFileSync(path.join(out, 'render-recovery-results.json'), redact(JSON.stringify(result, null, 2)) + '\n')
    console.log(JSON.stringify({ status: result.status, checks: result.checks.length, failure: result.failure, out }, null, 2))
    process.exitCode = result.status === 'passed' ? 0 : 1
  }
}
main().catch(error => { console.error(redact(error.stack)); process.exitCode = 1 })
