// Real API checks. A diagnostic upload never counts as a passing G0 flow.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { chromium } = require('playwright')

const base = process.env.CODEPOET_G0_BASE_URL || 'http://127.0.0.1:13100'
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) {
  throw new Error('G0 checks require a local, isolated test deployment.')
}
const diagnostic = process.argv.includes('--diagnostic-upload')
const out = process.env.CODEPOET_G0_OUTPUT_DIR
  ? path.resolve(process.env.CODEPOET_G0_OUTPUT_DIR)
  : path.resolve(__dirname, '../work-logs/2026-11-09-g0-integration')
const resultFile = path.join(out, diagnostic ? 'g0-diagnostic-results.json' : 'g0-check-results.json')
fs.mkdirSync(out, { recursive: true })
const secrets = []
const result = {
  base, diagnostic, status: 'running', g0Passed: false,
  checks: [], blockers: [], requests: [], pageErrors: [], screenshots: [],
}
const record = (name, details = {}) => result.checks.push({ name, ...details })
const redact = (value) => secrets.reduce((text, secret) => text.split(secret).join('[redacted]'), String(value))
const generationId = (page) => page.getByText('生成编号', { exact: true }).locator('..').locator('dd').textContent()
const applied = (page) => page.getByText('已应用', { exact: true }).waitFor()
const saveButton = (page) => page.locator('.archive-actions button.button--primary').first()
const successLink = (page) => page.getByRole('link', { name: '打开已保存的作品', exact: true })
const screenshot = async (page, name) => {
  await page.evaluate(async () => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
    window.scrollTo(0, 0)
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  await page.screenshot({ path: path.join(out, name), fullPage: true })
  result.screenshots.push(name)
}

function observe(page, actor) {
  page.on('pageerror', (error) => result.pageErrors.push({ actor, message: redact(error.message) }))
  page.on('response', (response) => {
    const url = new URL(response.url())
    if (url.pathname.startsWith('/api/v1/')) {
      // Never record cookies, tokens, request bodies, or authentication responses.
      result.requests.push({ actor, method: response.request().method(), path: url.pathname,
        status: response.status(), requestId: response.headers()['x-request-id'] || '' })
    }
  })
}

async function registerAndLogin(page, suffix) {
  const account = { username: `g0${suffix}_${Date.now().toString(36)}`, password: crypto.randomBytes(20).toString('base64url') }
  secrets.push(account.password)
  await page.goto(base)
  await page.getByRole('button', { name: '登录', exact: true }).first().click()
  await page.getByRole('button', { name: '注册新账号', exact: true }).click()
  await page.locator('#login-username').fill(account.username)
  await page.locator('#login-password').fill(account.password)
  await page.locator('#register-nickname').fill(`G0 自动联调 ${suffix}`)
  await page.locator('dialog button[type=submit]').click()
  await page.locator('dialog button[type=submit]').filter({ hasText: '登录' }).waitFor()
  await page.locator('#login-password').fill(account.password)
  await page.locator('dialog button[type=submit]').click()
  await page.locator('dialog[open]').waitFor({ state: 'hidden' })
  await applied(page)
  return account
}

async function main() {
  const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHROME_PATH || 'chrome', headless: true })
  try {
    const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } })
    const page = await context.newPage()
    observe(page, 'owner')
    const account = await registerAndLogin(page, 'a')
    const originalId = await generationId(page)
    const originalPixels = await page.locator('canvas').first().evaluate((canvas) => canvas.toDataURL())
    record('register-login-default-generation', { generationId: originalId })

    await page.locator('#param-petalRadius').fill('36')
    await applied(page)
    const changedPixels = await page.locator('canvas').first().evaluate((canvas) => canvas.toDataURL())
    assert.notEqual(await generationId(page), originalId)
    assert.notEqual(changedPixels, originalPixels)
    record('parameter-changes-real-canvas')

    await page.locator('#param-petalRadius').fill('999')
    await page.getByText('修改未应用', { exact: true }).first().waitFor()
    assert.equal(await saveButton(page).isDisabled(), true)
    assert.equal(await page.locator('canvas').first().evaluate((canvas) => canvas.toDataURL()), changedPixels)
    record('invalid-number-retains-canvas-and-disables-save')
    await page.getByRole('button', { name: '恢复默认', exact: true }).click()
    await applied(page)

    const invalidResponse = page.waitForResponse((r) => r.url().endsWith('/api/v1/generations') && r.status() === 422)
    await page.locator('#param-petalRadius').fill('48')
    await page.locator('#param-frameRadius').fill('40')
    const rejected = await invalidResponse
    const rejection = (await rejected.json()).error
    await page.getByText('修改未应用', { exact: true }).first().waitFor()
    assert.equal(await saveButton(page).isDisabled(), true)
    record('real-cross-field-422', { code: rejection.code, requestId: rejection.requestId })
    await page.getByRole('button', { name: '恢复默认', exact: true }).click()
    await applied(page)

    // Invalidate the real server session without notifying the UI first.
    const beforeLoginId = await generationId(page)
    const logoutStatus = await page.evaluate(async () => {
      const { csrfToken } = await (await fetch('/api/v1/me')).json()
      return (await fetch('/api/v1/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': csrfToken } })).status
    })
    assert.equal(logoutStatus, 204)
    await saveButton(page).click()
    await page.locator('dialog[open]').waitFor()
    await page.locator('#login-username').fill(account.username)
    await page.locator('#login-password').fill(account.password)
    await page.locator('dialog button[type=submit]').click()
    await page.locator('dialog[open]').waitFor({ state: 'hidden' })
    await applied(page)
    assert.equal(await generationId(page), beforeLoginId)
    assert.equal(await saveButton(page).isEnabled(), true)
    record('real-401-relogin-preserves-generation')

    // Delay a real response; do not replace generation data with a fixture.
    let releaseFirst
    const firstSeen = new Promise((resolve) => { releaseFirst = resolve })
    let releaseDelayed
    const delayedDone = new Promise((resolve) => { releaseDelayed = resolve })
    const delay = async (route) => {
      const payload = route.request().postDataJSON()
      const response = await route.fetch()
      if (payload.params?.petalRadius === 30) {
        releaseFirst()
        await new Promise((resolve) => setTimeout(resolve, 900))
      }
      try { await route.fulfill({ response }) } catch { /* The obsolete request may already be cancelled. */ }
      finally { if (payload.params?.petalRadius === 30) releaseDelayed() }
    }
    await page.route('**/api/v1/generations', delay)
    await page.locator('#param-petalRadius').fill('30')
    await firstSeen
    await page.locator('#param-petalRadius').fill('34')
    await applied(page)
    await delayedDone
    const acceptedId = await generationId(page)
    const generationResponse = await context.request.get(`/api/v1/generations/${acceptedId}`)
    assert.equal(generationResponse.status(), 200)
    const generation = await generationResponse.json()
    assert.equal(generation.params.petalRadius, 34)
    await page.unroute('**/api/v1/generations', delay)
    record('late-real-response-cannot-overwrite-newer-generation', { generationId: acceptedId, radius: 34 })

    await page.setViewportSize({ width: 390, height: 844 })
    const mobile = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }))
    assert.ok(mobile.document <= mobile.viewport)
    await screenshot(page, 'automated-creator-390.png')
    record('creator-390-no-horizontal-overflow', mobile)
    await page.setViewportSize({ width: 1440, height: 1000 })

    // One transport failure tests the existing error recovery without fabricated success.
    await page.route('**/api/v1/render-jobs', (route) => route.abort('connectionfailed'), { times: 1 })
    await saveButton(page).click()
    await page.locator('.archive [role=alert]').waitFor()
    assert.equal(await generationId(page), acceptedId)
    assert.equal(await successLink(page).count(), 0)
    record('network-failure-retains-generation-without-false-save')

    await page.getByRole('textbox', { name: '作品名称', exact: true }).fill('G0 自动真实作品')
    const jobResponsePromise = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/render-jobs' && r.request().method() === 'POST')
    await saveButton(page).click()
    const jobResponse = await jobResponsePromise
    assert.equal(jobResponse.status(), 202)
    let job = await jobResponse.json()
    record('create-real-upload-job', { id: job.id, status: job.status, phase: job.phase, expiresAt: job.expiresAt,
      requestId: jobResponse.headers()['x-request-id'] })
    if (job.status !== 'running' || job.phase !== 'awaiting_frames') {
      await page.locator('.archive [role=alert]').waitFor()
      result.blockers.push({ id: 'BE-G0-001', expected: 'running/awaiting_frames', actual: `${job.status}/${job.phase}`,
        jobId: job.id, requestId: jobResponse.headers()['x-request-id'] })
      await screenshot(page, 'automated-backend-job-status-mismatch.png')
      if (!diagnostic) { result.status = 'blocked'; return }
      // Explicitly marked diagnostic: upload the real current Canvas via the API.
      const upload = await page.evaluate(async ({ id, irHash }) => {
        const { csrfToken } = await (await fetch('/api/v1/me')).json()
        const canvas = document.querySelector('canvas')
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'))
        const form = new FormData()
        form.set('file', blob, 'g0-diagnostic-0.png')
        form.set('timeMs', '0')
        form.set('irHash', irHash)
        const response = await fetch(`/api/v1/render-jobs/${id}/frames`, {
          method: 'POST', headers: { 'X-CSRF-Token': csrfToken }, body: form,
        })
        return { status: response.status, bytes: blob.size, body: await response.json(), requestId: response.headers.get('x-request-id') }
      }, { id: job.id, irHash: generation.irHash })
      assert.equal(upload.status, 200)
      assert.equal(upload.body.status, 'succeeded')
      job = upload.body
      record('diagnostic-only-real-png-upload', { status: upload.status, bytes: upload.bytes, requestId: upload.requestId })
      await saveButton(page).click()
    }
    await successLink(page).waitFor()
    const versionPath = await successLink(page).getAttribute('href')
    const versionId = versionPath.split('/').at(-1)
    const detailResponse = await context.request.get(`/api/v1/work-versions/${versionId}`)
    assert.equal(detailResponse.status(), 200)
    const detail = await detailResponse.json()
    const artifact = detail.renderJob.manifest.frames[0]
    assert.equal(detail.generation.id, acceptedId)
    assert.equal(detail.renderJob.status, 'succeeded')
    record('save-private-work', { assisted: result.blockers.length > 0, versionId, workId: detail.workVersion.workId,
      generationId: acceptedId, jobId: detail.renderJob.id, artifactId: artifact.artifactId })

    const viewer = await context.newPage()
    observe(viewer, 'owner-viewer')
    for (const operation of ['new-tab', 'refresh']) {
      if (operation === 'new-tab') await viewer.goto(versionPath)
      else await viewer.reload()
      await viewer.getByRole('heading', { name: 'G0 自动真实作品', exact: true }).waitFor()
      await viewer.waitForFunction(() => Array.from(document.images).some((img) => img.complete && img.naturalWidth === 800 && img.naturalHeight === 600))
      record(`reopen-${operation}-real-png`, { versionId })
    }
    await screenshot(viewer, 'automated-reopened-1440.png')
    const image = await context.request.get(`/api/v1/artifacts/${artifact.artifactId}`)
    assert.equal(image.status(), 200)
    const png = await image.body()
    assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10])
    assert.equal(crypto.createHash('sha256').update(png).digest('hex'), artifact.sha256)
    record('server-png-hash-matches-manifest', { bytes: png.length, sha256: artifact.sha256 })

    const other = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } })
    const otherPage = await other.newPage()
    observe(otherPage, 'other-user')
    await registerAndLogin(otherPage, 'b')
    for (const target of [
      `/works/${detail.workVersion.workId}`, `/work-versions/${versionId}`,
      `/generations/${acceptedId}`, `/render-jobs/${detail.renderJob.id}`, `/artifacts/${artifact.artifactId}`,
    ]) {
      const denied = await other.request.get(`/api/v1${target}`)
      assert.equal(denied.status(), 404)
      record('other-user-private-resource-404', { path: target, requestId: denied.headers()['x-request-id'] })
    }
    await otherPage.goto(versionPath)
    await otherPage.getByText('无法读取作品', { exact: true }).waitFor()
    assert.equal(await otherPage.locator('img').count(), 0)
    await screenshot(otherPage, 'automated-private-denied-1440.png')
    record('other-user-page-does-not-expose-private-png')
    const storage = await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }))
    assert.deepEqual(storage, { local: [], session: [] })
    record('no-authentication-in-web-storage', storage)
    assert.deepEqual(result.pageErrors, [])
    result.g0Passed = result.blockers.length === 0
    result.status = result.g0Passed ? 'passed' : 'blocked'
  } catch (error) {
    result.status = 'failed'
    result.failure = redact(error.stack || error.message)
  } finally {
    await browser.close()
    fs.writeFileSync(resultFile, JSON.stringify(result, null, 2) + '\n')
    console.log(JSON.stringify({ status: result.status, g0Passed: result.g0Passed, checks: result.checks.length,
      blockers: result.blockers, resultFile }, null, 2))
    process.exitCode = result.status === 'passed' ? 0 : result.status === 'blocked' ? 2 : 1
  }
}

main().catch((error) => { console.error(redact(error.message)); process.exitCode = 1 })
