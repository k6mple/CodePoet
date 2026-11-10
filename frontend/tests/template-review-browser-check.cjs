// Real browser/UI + API acceptance. Never turns a missing backend operation into success.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const base = process.env.CODEPOET_REVIEW_BASE_URL || 'http://127.0.0.1:13130'
const password = process.env.CODEPOET_ACCEPTANCE_PASSWORD
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Requires an isolated local deployment')
if (!password) throw new Error('CODEPOET_ACCEPTANCE_PASSWORD is required; it is never saved')
const out = path.resolve(process.env.CODEPOET_REVIEW_OUTPUT_DIR || 'frontend/work-logs/2026-11-10-g0-template-review-acceptance/templates')
fs.mkdirSync(out, { recursive: true })
const result = { base, status: 'running', templateReviewPassed: false, checks: [], blockers: [], requests: [], pageErrors: [], screenshots: [] }
const record = (name, details = {}) => result.checks.push({ name, ...details })
const redact = (value) => String(value).split(password).join('[redacted]')
let phase = 'initializing'
let failurePage

function observe(page, actor) {
  page.on('pageerror', (error) => result.pageErrors.push({ actor, message: redact(error.message) }))
  page.on('response', (response) => {
    const url = new URL(response.url())
    if (url.pathname.startsWith('/api/v1/')) result.requests.push({ actor, path: url.pathname,
      method: response.request().method(), status: response.status(), requestId: response.headers()['x-request-id'] || '' })
  })
  page.on('dialog', (dialog) => dialog.accept(dialog.type() === 'prompt' ? '真实验收归档' : undefined))
}

async function shot(page, name) {
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur() })
  await page.screenshot({ path: path.join(out, name), fullPage: true })
  result.screenshots.push(name)
}

async function login(browser, username, actor) {
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  observe(page, actor)
  await page.goto(base)
  await page.getByRole('button', { name: '登录', exact: true }).first().click()
  await page.locator('#login-username').fill(username)
  await page.locator('#login-password').fill(password)
  await page.locator('dialog button[type=submit]').click()
  await page.locator('dialog[open]').waitFor({ state: 'hidden' })
  return { context, page }
}

async function api(page, endpoint, method = 'GET', body) {
  return page.evaluate(async ({ endpoint, method, body }) => {
    const headers = {}
    if (method !== 'GET') {
      const me = await (await fetch('/api/v1/me')).json()
      headers['X-CSRF-Token'] = me.csrfToken
      headers['Content-Type'] = 'application/json'
    }
    const response = await fetch(`/api/v1${endpoint}`, { method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    return { status: response.status, body: response.status === 204 ? null : await response.json(), requestId: response.headers.get('x-request-id') }
  }, { endpoint, method, body })
}

async function publicRead(context, endpoint, expected = 200) {
  const response = await context.request.get(`/api/v1${endpoint}`)
  assert.equal(response.status(), expected, `public ${endpoint}`)
  return response.json()
}

async function clickResponse(page, button, endpoint, method, expected) {
  const waiting = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/v1${endpoint}` && r.request().method() === method)
  await button.click()
  const response = await waiting
  assert.equal(response.status(), expected, `${method} ${endpoint}: ${await response.text()}`)
  return response.json()
}

const row = (page, number) => page.locator('.version-row').filter({ has: page.locator('strong', { hasText: new RegExp(`^v${number}$`) }) })
async function reloadEditor(page, templateId) {
  await page.goto(`/manage/templates/${templateId}`)
  await page.getByRole('button', { name: '保存元数据', exact: true }).waitFor()
  await page.locator('.version-row').first().waitFor()
}

async function decide(page, version, decision, reason) {
  await page.goto('/reviews')
  const item = page.locator('.review-row').filter({ has: page.locator('strong', { hasText: version.metadata.name }) })
  await item.first().locator('.review-row__main').click()
  await page.getByRole('button', { name: '通过', exact: true }).waitFor()
  await page.waitForFunction(() => !document.querySelector('.review-decision button')?.disabled)
  if (decision === 'reject') {
    await page.getByRole('button', { name: '退回', exact: true }).click()
    await page.getByText('退回必须填写理由。', { exact: true }).waitFor()
    record('empty-rejection-reason-blocked-in-ui', { versionId: version.id })
  }
  await page.getByRole('textbox', { name: '审核理由', exact: true }).fill(reason)
  const reviewed = await clickResponse(page, page.getByRole('button', { name: decision === 'approve' ? '通过' : '退回', exact: true }), '/reviews', 'POST', 201)
  assert.equal(reviewed.targetId, version.id)
  assert.equal(reviewed.decision, decision)
  return reviewed
}

async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  try {
    const owner = await login(browser, 'developer_demo', 'developer')
    const reviewer = await login(browser, 'expert_demo', 'expert')
    const anon = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } })
    const ownerPage = owner.page
    failurePage = ownerPage
    const name = `模板审核验收 ${Date.now().toString(36)}`
    phase = 'create-template-and-version'
    await ownerPage.goto('/manage/templates')
    await ownerPage.getByRole('button', { name: '新建模板', exact: true }).click()
    const create = ownerPage.locator('section[aria-labelledby=create-template-heading]')
    await create.getByRole('textbox', { name: '模板名称', exact: true }).fill(name)
    await create.getByRole('textbox', { name: '说明（最多 1000 字）', exact: true }).fill('隔离环境真实模板审核验收')
    const template = await clickResponse(ownerPage, create.getByRole('button', { name: '创建模板容器', exact: true }), '/templates', 'POST', 201)
    await ownerPage.waitForURL(`**/manage/templates/${template.id}`)
    await ownerPage.getByRole('button', { name: '新建版本', exact: true }).click()
    await ownerPage.getByRole('textbox', { name: '变更说明（最多 500 字）', exact: true }).fill('首版真实验收')
    const v1 = await clickResponse(ownerPage, ownerPage.getByRole('button', { name: '创建版本', exact: true }), `/templates/${template.id}/versions`, 'POST', 201)
    assert.equal(v1.status, 'draft')
    await row(ownerPage, 1).waitFor()
    await publicRead(anon, `/templates/${template.id}`, 404)
    await publicRead(anon, `/template-versions/${v1.id}`, 404)
    record('ui-create-template-and-draft-version-private', { templateId: template.id, versionId: v1.id })

    phase = 'draft-preview-and-private-work'
    await row(ownerPage, 1).getByRole('button', { name: '查看', exact: true }).click()
    await ownerPage.getByRole('link', { name: '在创作台私有预览默认参数', exact: true }).click()
    await ownerPage.getByText('已应用', { exact: true }).waitFor()
    const generationId = await ownerPage.getByText('生成编号', { exact: true }).locator('..').locator('dd').textContent()
    const generated = await api(ownerPage, `/generations/${generationId}`)
    assert.equal(generated.body.templateVersionId, v1.id)
    assert.equal(generated.body.unpublishedSource, true)
    await ownerPage.getByRole('textbox', { name: '作品名称', exact: true }).fill('未发布来源真实验收')
    const saved = await clickResponse(ownerPage, ownerPage.locator('.archive-actions button.button--primary').first(), '/works', 'POST', 201)
    const workSubmit = await api(ownerPage, `/work-versions/${saved.version.id}/submit`, 'POST', { expectedRevision: saved.version.revision })
    assert.equal(workSubmit.status, 409)
    assert.equal(workSubmit.body.error.code, 'UNPUBLISHED_SOURCE')
    await shot(ownerPage, 'draft-private-preview.png')
    record('draft-preview-real-canvas-and-png-work-cannot-publish', { generationId, workVersionId: saved.version.id, requestId: workSubmit.requestId })

    phase = 'submit-and-reject'
    await reloadEditor(ownerPage, template.id)
    const submitted = await clickResponse(ownerPage, row(ownerPage, 1).getByRole('button', { name: '送审', exact: true }), `/template-versions/${v1.id}/submit`, 'POST', 200)
    assert.equal(submitted.status, 'submitted')
    await row(ownerPage, 1).getByText('待审核', { exact: true }).waitFor()
    assert.equal(await row(ownerPage, 1).getByRole('button', { name: '编辑', exact: true }).count(), 0)
    const rejectReason = '真实验收：请调整图元后重新送审'
    await decide(reviewer.page, v1, 'reject', rejectReason)
    await reloadEditor(ownerPage, template.id)
    await ownerPage.getByText(`退回理由：${rejectReason}`, { exact: true }).waitFor()
    record('ui-submit-other-reviewer-reject-and-show-reason', { versionId: v1.id })

    phase = 'edit-rejected-version'
    const rejectedVersion = (await api(ownerPage, `/template-versions/${v1.id}`)).body
    await row(ownerPage, 1).getByRole('button', { name: '编辑', exact: true }).click()
    await ownerPage.getByRole('textbox', { name: '变更说明（最多 500 字）', exact: true }).fill('退回后修订：需要可持久化')
    const responseWait = ownerPage.waitForResponse((r) => new URL(r.url()).pathname === `/api/v1/template-versions/${v1.id}` && r.request().method() === 'PATCH')
    await ownerPage.getByRole('button', { name: '保存版本', exact: true }).click()
    const patchResponse = await responseWait
    if (patchResponse.status() === 405 || patchResponse.status() === 404) {
      await ownerPage.getByText('版本保存失败', { exact: true }).waitFor()
      assert.equal(await ownerPage.getByRole('textbox', { name: '变更说明（最多 500 字）', exact: true }).inputValue(), '退回后修订：需要可持久化')
      const unchanged = await api(ownerPage, `/template-versions/${v1.id}`)
      assert.equal(unchanged.body.changeNote, v1.changeNote)
      result.blockers.push({ id: 'BE-TEMPLATE-EDIT-001', operation: 'PATCH /template-versions/{id}', status: patchResponse.status(),
        versionId: v1.id, requestId: patchResponse.headers()['x-request-id'], consequence: 'Returned version cannot be revised and persisted; editor keeps unsaved draft.' })
      await shot(ownerPage, 'rejected-edit-backend-missing.png')
      await ownerPage.getByRole('button', { name: '关闭编辑', exact: true }).click()
      record('missing-patch-retains-draft-and-server-version', { status: patchResponse.status() })
    } else {
      assert.equal(patchResponse.status(), 200, await patchResponse.text())
      const updated = await patchResponse.json()
      assert.equal(updated.revision, rejectedVersion.revision + 1)
      const persisted = await api(ownerPage, `/template-versions/${v1.id}`)
      assert.equal(persisted.body.changeNote, '退回后修订：需要可持久化')
      const stale = await api(ownerPage, `/template-versions/${v1.id}`, 'PATCH', patchResponse.request().postDataJSON())
      assert.equal(stale.status, 409)
      record('ui-rejected-version-revision-saved-and-stale-write-rejected')
    }

    phase = 'resubmit-unchanged-and-publish'
    // This only verifies the transition; it does not count as successful revision when PATCH is missing.
    await clickResponse(ownerPage, row(ownerPage, 1).getByRole('button', { name: '送审', exact: true }), `/template-versions/${v1.id}/submit`, 'POST', 200)
    await decide(reviewer.page, v1, 'approve', '真实验收通过')
    const published = await publicRead(anon, `/templates/${template.id}`)
    assert.equal(published.publishedVersionId, v1.id)
    if (result.blockers.length === 0) {
      const publishedVersion = await api(ownerPage, `/template-versions/${v1.id}`)
      const readonly = await api(ownerPage, `/template-versions/${v1.id}`, 'PATCH', {
        expectedRevision: publishedVersion.body.revision, changeNote: '已发布版本不可覆盖',
      })
      assert.equal(readonly.status, 409)
      record('published-version-patch-is-readonly')
    }
    const publicPage = await anon.newPage()
    await publicPage.goto(`/templates/${template.id}`)
    await publicPage.getByRole('heading', { name, exact: true }).waitFor()
    await publicPage.locator('.template-label').getByText(/v1 · rev/).waitFor()
    await shot(publicPage, 'public-version-one.png')
    record('ui-approve-publishes-concrete-version', { versionId: v1.id })

    phase = 'clone-and-preserve-old-public-pointer'
    await reloadEditor(ownerPage, template.id)
    const v2 = await clickResponse(ownerPage, row(ownerPage, 1).getByRole('button', { name: '克隆', exact: true }), `/templates/${template.id}/versions`, 'POST', 201)
    assert.equal(v2.status, 'draft')
    assert.deepEqual(v2.irTemplate, v1.irTemplate)
    await row(ownerPage, 2).waitFor()
    await clickResponse(ownerPage, row(ownerPage, 2).getByRole('button', { name: '送审', exact: true }), `/template-versions/${v2.id}/submit`, 'POST', 200)
    assert.equal((await publicRead(anon, `/templates/${template.id}`)).publishedVersionId, v1.id)
    await publicRead(anon, `/template-versions/${v2.id}`, 404)
    await publicPage.reload()
    await publicPage.locator('.template-label').getByText(/v1 · rev/).waitFor()
    record('new-version-pending-keeps-old-public-version', { oldVersionId: v1.id, pendingVersionId: v2.id })
    await decide(reviewer.page, v2, 'approve', '第二版真实审核通过')
    assert.equal((await publicRead(anon, `/templates/${template.id}`)).publishedVersionId, v2.id)
    await publicPage.reload()
    await publicPage.locator('.template-label').getByText(/v2 · rev/).waitFor()
    record('approve-new-version-switches-public-pointer', { versionId: v2.id })

    phase = 'archive-and-history'
    await reloadEditor(ownerPage, template.id)
    const archived = await clickResponse(ownerPage, row(ownerPage, 2).getByRole('button', { name: '归档', exact: true }), `/template-versions/${v2.id}/archive`, 'POST', 200)
    assert.equal(archived.status, 'archived')
    assert.equal((await publicRead(anon, `/templates/${template.id}`)).publishedVersionId, v1.id)
    await reloadEditor(ownerPage, template.id)
    await clickResponse(ownerPage, row(ownerPage, 1).getByRole('button', { name: '归档', exact: true }), `/template-versions/${v1.id}/archive`, 'POST', 200)
    const publicList = await publicRead(anon, '/templates?pageSize=100')
    assert.ok(!publicList.items.some((item) => item.id === template.id))
    const history = await api(ownerPage, `/work-versions/${saved.version.id}`)
    assert.equal(history.status, 200)
    assert.equal(history.body.generation.irHash, generated.body.irHash)
    const artifactId = history.body.renderJob.manifest.frames[0].artifactId
    const artifact = await owner.context.request.get(`/api/v1/artifacts/${artifactId}`)
    assert.equal(artifact.status(), 200)
    assert.deepEqual([...(await artifact.body()).subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10])
    await shot(ownerPage, 'archived-versions.png')
    record('ui-archive-fallback-remove-from-public-and-keep-history', { templateId: template.id, artifactId })

    phase = 'self-review-and-role-boundaries'
    const ownTemplate = await api(reviewer.page, '/templates', 'POST', { metadata: { name: `自审保护 ${Date.now()}`, description: '隔离验收' } })
    assert.equal(ownTemplate.status, 201)
    const ownVersion = await api(reviewer.page, `/templates/${ownTemplate.body.id}/versions`, 'POST', {
      irTemplate: v1.irTemplate, parameterSchema: v1.parameterSchema, bindings: v1.bindings,
      constraints: v1.constraints, supportedBackends: ['canvas2d'], changeNote: '自审保护',
    })
    assert.equal(ownVersion.status, 201)
    const ownSubmit = await api(reviewer.page, `/template-versions/${ownVersion.body.id}/submit`, 'POST', { expectedRevision: ownVersion.body.revision })
    assert.equal(ownSubmit.status, 200)
    await reviewer.page.goto('/reviews')
    const selfRow = reviewer.page.locator('.review-row').filter({ hasText: ownTemplate.body.metadata.name })
    await selfRow.getByText('不能自审', { exact: true }).waitFor()
    await selfRow.locator('.review-row__main').click()
    await reviewer.page.getByText('不能审核自己的提交', { exact: true }).waitFor()
    assert.equal(await reviewer.page.getByRole('button', { name: '通过', exact: true }).count(), 0)
    const selfReview = await api(reviewer.page, '/reviews', 'POST', { targetType: 'template_version', targetId: ownVersion.body.id,
      expectedRevision: ownSubmit.body.revision, decision: 'approve', reason: '应被拒绝' })
    assert.equal(selfReview.status, 403)
    assert.equal(selfReview.body.error.code, 'SELF_REVIEW_FORBIDDEN')
    const denied = await api(ownerPage, '/reviews')
    assert.equal(denied.status, 403)
    await shot(reviewer.page, 'self-review-blocked.png')
    record('ui-and-api-block-self-review-and-developer-review', { selfStatus: selfReview.status, developerStatus: denied.status })

    await ownerPage.setViewportSize({ width: 390, height: 844 })
    const mobile = await ownerPage.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }))
    assert.ok(mobile.document <= mobile.width)
    await shot(ownerPage, 'template-manager-390.png')
    record('template-manager-390-no-horizontal-overflow', mobile)
    assert.deepEqual(result.pageErrors, [])
    result.templateReviewPassed = result.blockers.length === 0
    result.status = result.templateReviewPassed ? 'passed' : 'blocked'
  } catch (error) {
    result.status = 'failed'
    result.failure = { phase, message: redact(error.stack || error.message) }
    if (failurePage && !failurePage.isClosed() && !await failurePage.locator('dialog[open]').count()) {
      await shot(failurePage, 'failure.png').catch(() => {})
    }
  } finally {
    await browser.close()
    fs.writeFileSync(path.join(out, 'template-review-results.json'), JSON.stringify(result, null, 2) + '\n')
    console.log(JSON.stringify({ status: result.status, templateReviewPassed: result.templateReviewPassed, checks: result.checks.length,
      blockers: result.blockers, failure: result.failure, out }, null, 2))
    process.exitCode = result.status === 'passed' ? 0 : result.status === 'blocked' ? 2 : 1
  }
}
main().catch((error) => { console.error(redact(error.message)); process.exitCode = 1 })
