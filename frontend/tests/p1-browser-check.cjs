// Selected P1 paths against real local APIs and UI. Does not certify all GA/GB requirements.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { chromium } = require('playwright')

const base = process.env.CODEPOET_P1_BASE_URL || 'http://127.0.0.1:13140'
const password = process.env.CODEPOET_ACCEPTANCE_PASSWORD
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname) || !password) throw new Error('Requires local isolated deployment and seed credential in environment')
const out = path.resolve(process.env.CODEPOET_P1_OUTPUT_DIR || 'frontend/work-logs/2026-11-11-main-integration/p1')
fs.mkdirSync(out, { recursive: true })
const result = { base, scope: 'selected-p1-real-ui-and-api', status: 'running', gaPassed: false, gbPassed: false, checks: [], blockers: [], failures: [], requests: [], pageErrors: [], screenshots: [] }
const secrets = [password]
const redact = v => secrets.reduce((s, secret) => s.split(secret).join('[redacted]'), String(v))
const runId = Date.now().toString(36)
const record = (name, details = {}) => result.checks.push({ name, ...details })
let phase = 'initializing'
let owner, admin, expert, other
const state = {}

function observe(page, actor) {
  page.setDefaultTimeout(15000)
  page.on('pageerror', error => result.pageErrors.push({ actor, message: redact(error.message) }))
  page.on('response', response => {
    const url = new URL(response.url())
    if (url.pathname.startsWith('/api/v1/')) result.requests.push({ actor, path: url.pathname, method: response.request().method(), status: response.status(), requestId: response.headers()['x-request-id'] || '' })
  })
  page.on('dialog', d => d.accept())
}
async function shot(page, name) {
  // Never persist a credential input, even on a failed login.
  for (const input of await page.locator('input[type=password]:visible').all()) await input.fill('')
  await page.evaluate(async () => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
    window.scrollTo(0, 0)
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  await page.screenshot({ path: path.join(out, name), fullPage: true })
  result.screenshots.push(name)
}
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
function ok(response, expected = 200) {
  assert.equal(response.status, expected, redact(JSON.stringify(response.body)))
  return response.body
}
async function clickResponse(page, button, endpoint, method, expected = 200) {
  const [response] = await Promise.all([
    page.waitForResponse(r => new URL(r.url()).pathname === `/api/v1${endpoint}` && r.request().method() === method),
    button.click(),
  ])
  assert.equal(response.status(), expected, redact(await response.text()))
  return response.json()
}
async function actor(browser, username) {
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  observe(page, username)
  await page.goto('/')
  await page.getByRole('button', { name: '登录', exact: true }).first().click()
  await page.locator('#login-username').fill(username)
  await page.locator('#login-password').fill(password)
  await page.locator('dialog button[type=submit]').click()
  await page.locator('dialog[open]').waitFor({ state: 'hidden' })
  return { context, page }
}
async function generation(page) {
  const id = await page.getByText('生成编号', { exact: true }).locator('..').locator('dd').textContent()
  return ok(await api(page, `/generations/${id}`))
}
async function applied(page) { await page.getByText('已应用', { exact: true }).waitFor() }
async function saveWork(page, name) {
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(name)
  await page.locator('.archive-actions button.button--primary').first().click()
  const link = page.getByRole('link', { name: '打开已保存的作品', exact: true })
  await link.waitFor()
  const href = await link.getAttribute('href')
  const detail = ok(await api(page, `/work-versions/${href.split('/').pop()}`))
  await page.goto(href)
  await page.getByRole('heading', { name, exact: true }).waitFor()
  return detail
}
async function decideWork(page, detail) {
  await page.goto('/reviews')
  const row = page.locator('.review-row').filter({ hasText: detail.workVersion.title })
  await row.first().locator('.review-row__main').click()
  const approve = page.getByRole('button', { name: '通过', exact: true })
  await approve.waitFor()
  await page.getByRole('textbox', { name: '审核理由', exact: true }).fill('真实联调审核')
  const review = await clickResponse(page, approve, '/reviews', 'POST', 201)
  assert.equal(review.targetId, detail.workVersion.id)
  return review
}
async function step(name, page, fn) {
  phase = name
  console.log(`Checking ${name}`)
  try { await fn(); console.log(`Completed ${name}`) } catch (error) {
    result.failures.push({ phase: name, message: redact(error.stack || error.message) })
    console.log(`Failed ${name}: ${redact(error.message)}`)
    await shot(page, `failure-${name}.png`).catch(() => {})
  }
}

async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  try {
    owner = await actor(browser, 'creator_demo')
    admin = await actor(browser, 'admin_demo')
    expert = await actor(browser, 'expert_demo')
    other = await actor(browser, 'developer_demo')
    const page = owner.page
    await step('sessions-restore', page, async () => {
      await page.goto('/?template=tpl-lotus')
      await applied(page)
      await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(`会话验收-${runId}`)
      const initial = await generation(page)
      const created = await clickResponse(page, page.getByRole('button', { name: '保存创作进度', exact: true }), '/sessions', 'POST', 201)
      await page.locator('#param-centerSize').fill('50')
      await applied(page)
      const changed = await generation(page)
      assert.notEqual(initial.irHash, changed.irHash)
      const updated = await clickResponse(page, page.getByRole('button', { name: '保存进度并追加快照', exact: true }), `/sessions/${created.id}`, 'PATCH')
      assert.equal(updated.revision, created.revision + 1)
      const snapshots = ok(await api(page, `/sessions/${created.id}/snapshots`)).items
      assert.equal(snapshots.length, 2)
      const first = snapshots.find(s => s.state.generationId === initial.id)
      assert.ok(first)
      await page.goto('/space?tab=sessions')
      await page.getByRole('button').filter({ hasText: `会话验收-${runId}` }).first().click()
      // Match by displayed revision; IDs are deliberately abbreviated in the UI.
      const oldRow = page.locator('.snapshot-row').filter({ hasText: `rev ${first.sessionRevision} ·` })
      const restored = await clickResponse(page, oldRow.getByRole('button', { name: '恢复到此快照' }), `/sessions/${created.id}/restore`, 'POST')
      assert.equal(restored.session.state.generationId, initial.id)
      const after = ok(await api(page, `/sessions/${created.id}/snapshots`)).items
      assert.equal(after.length, 4)
      for (const old of snapshots) assert.deepEqual(after.find(s => s.id === old.id), old)
      await page.getByRole('link', { name: '在创作台继续', exact: true }).click()
      await applied(page)
      assert.equal((await generation(page)).irHash, initial.irHash)
      const cross = await api(other.page, `/sessions/${created.id}`)
      assert.equal(cross.status, 404)
      record('two-saves-restore-appends-history-and-resumes', { sessionId: created.id, snapshotCount: after.length, crossAccountStatus: cross.status })
      await shot(page, 'restored-session.png')
      state.session = created
    })
    await step('works-favorites-versions', page, async () => {
      await page.goto('/?template=tpl-lotus')
      await applied(page)
      const detail = await saveWork(page, `作品验收-${runId}`)
      state.work = detail
      await clickResponse(page, page.getByRole('button', { name: '收藏作品', exact: true }), `/works/${detail.workVersion.workId}/favorite`, 'PUT')
      const favoriteRead = await api(page, `/works/${detail.workVersion.workId}`)
      const favoriteList = await api(page, '/works?scope=favorites')
      if (!favoriteRead.body.isFavorite || !favoriteList.body.items.some(w => w.id === detail.workVersion.workId)) {
        result.blockers.push({ id: 'BE-FAVORITES-READ-001', workId: detail.workVersion.workId, getStatus: favoriteRead.status, isFavorite: favoriteRead.body.isFavorite, favoriteListTotal: favoriteList.body.total, requestId: favoriteRead.requestId, listRequestId: favoriteList.requestId })
        record('favorite-write-succeeds-read-path-blocked', { workId: detail.workVersion.workId })
      } else {
        await page.reload()
        await page.getByRole('button', { name: '取消收藏', exact: true }).waitFor()
        await page.goto('/space?tab=favorites')
        await page.getByRole('link', { name: detail.workVersion.title, exact: true }).waitFor()
        record('favorite-persists-on-reload-and-personal-space')
      }
      await page.goto(`/works/versions/${detail.workVersion.id}`)
      await clickResponse(page, page.getByRole('button', { name: '提交作品审核', exact: true }), `/work-versions/${detail.workVersion.id}/submit`, 'POST')
      await decideWork(expert.page, detail)
      const anonymous = await browser.newContext({ baseURL: base })
      assert.equal((await anonymous.request.get(`/api/v1/work-versions/${detail.workVersion.id}`)).status(), 200)
      await page.goto(`/?work=${detail.workVersion.workId}&from=${detail.workVersion.id}`)
      const irInput = page.getByRole('textbox', { name: '完整 IR JSON', exact: true })
      await irInput.waitFor()
      const ir = JSON.parse(await irInput.inputValue())
      ir.canvas.background = '#E8E0D0'
      await irInput.fill(JSON.stringify(ir))
      await clickResponse(page, page.getByRole('button', { name: '校验并应用 IR', exact: true }), '/generations', 'POST', 201)
      await page.getByRole('textbox', { name: '版本说明', exact: true }).fill('第二次真实保存')
      await page.getByRole('button', { name: '保存为新版本', exact: true }).click()
      const newLink = page.getByRole('link', { name: '打开已保存的作品', exact: true })
      await newLink.waitFor()
      const id = (await newLink.getAttribute('href')).split('/').pop()
      const second = ok(await api(page, `/work-versions/${id}`))
      assert.equal(second.workVersion.versionNumber, 2)
      assert.notEqual(second.generation.irHash, detail.generation.irHash)
      await page.goto(`/works/versions/${id}`)
      await clickResponse(page, page.getByRole('button', { name: '提交作品审核', exact: true }), `/work-versions/${id}/submit`, 'POST')
      const publicResponse = await anonymous.request.get(`/api/v1/works/${detail.workVersion.workId}`)
      assert.equal(publicResponse.status(), 200)
      const publicWork = await publicResponse.json()
      if (publicWork.displayVersionId !== detail.workVersion.id) {
        result.blockers.push({ id: 'BE-WORK-PUBLIC-POINTER-001', workId: detail.workVersion.workId, publishedVersionId: publicWork.publishedVersionId, expectedDisplayVersionId: detail.workVersion.id, actualDisplayVersionId: publicWork.displayVersionId, pendingVersionId: id, requestId: publicResponse.headers()['x-request-id'] })
      }
      assert.equal((await anonymous.request.get(`/api/v1/work-versions/${id}`)).status(), 404)
      const old = ok(await api(page, `/work-versions/${detail.workVersion.id}`))
      assert.equal(old.generation.irHash, detail.generation.irHash)
      await anonymous.close()
      record('work-two-versions-history-unchanged-and-anonymous-pending-404', { workId: detail.workVersion.workId, firstVersion: detail.workVersion.id, secondVersion: id, publicPointerPassed: publicWork.displayVersionId === detail.workVersion.id })
    })
    await step('role-approval-profile', admin.page, async () => {
      const context = await browser.newContext({ baseURL: base })
      const applicant = await context.newPage()
      observe(applicant, 'applicant')
      const name = `p1_${runId}`
      const applicantPassword = crypto.randomBytes(22).toString('base64url')
      secrets.push(applicantPassword)
      await applicant.goto('/')
      await applicant.getByRole('button', { name: '登录', exact: true }).first().click()
      await applicant.getByRole('button', { name: '注册新账号', exact: true }).click()
      await applicant.locator('#login-username').fill(name)
      await applicant.locator('#login-password').fill(applicantPassword)
      await applicant.locator('#register-role').selectOption('developer')
      await applicant.locator('dialog button[type=submit]').click()
      await applicant.locator('dialog button[type=submit]').filter({ hasText: '登录' }).waitFor()
      await applicant.locator('#login-password').fill(applicantPassword)
      await applicant.locator('dialog button[type=submit]').click()
      await applicant.locator('dialog[open]').waitFor({ state: 'hidden' })
      const before = ok(await api(applicant, '/me')).user
      assert.equal(before.role, 'creator')
      assert.equal((await api(applicant, '/tags', 'POST', { name: `未授权-${runId}`, description: '', parentId: null, conflictsWith: [], status: 'active' })).status, 403)
      await admin.page.goto('/admin/users')
      const row = admin.page.locator('.user-row').filter({ hasText: name })
      await row.getByRole('button', { name: '审批', exact: true }).click()
      await admin.page.getByRole('textbox', { name: '审批理由' }).fill('真实联调批准')
      await clickResponse(admin.page, admin.page.getByRole('button', { name: '批准', exact: true }), `/admin/users/${before.id}/role-decision`, 'POST')
      await applicant.goto('/account')
      await applicant.getByRole('button', { name: '保存资料', exact: true }).waitFor()
      const after = ok(await api(applicant, '/me')).user
      assert.equal(after.role, 'developer')
      assert.ok(after.permissions.includes('knowledge.write'))
      assert.equal((await api(applicant, '/tags', 'POST', { name: `审批后-${runId}`, description: '', parentId: null, conflictsWith: [], status: 'active' })).status, 201)
      await applicant.getByRole('textbox', { name: '昵称', exact: true }).fill(`已批准-${runId}`)
      await applicant.getByRole('textbox', { name: '简介', exact: true }).fill('真实接口个人资料验收')
      await clickResponse(applicant, applicant.getByRole('button', { name: '保存资料', exact: true }), '/me', 'PATCH')
      await applicant.reload()
      assert.equal(await applicant.getByRole('textbox', { name: '昵称', exact: true }).inputValue(), `已批准-${runId}`)
      record('role-approval-refresh-and-profile-persistence', { userId: before.id, roleBefore: before.role, roleAfter: after.role })
      await context.close()
    })
    await step('knowledge-taxonomy', other.page, async () => {
      const page = other.page
      const parent = ok(await api(page, '/tags', 'POST', { name: `继承父-${runId}`, description: '', parentId: null, conflictsWith: [], status: 'active' }), 201)
      const child = ok(await api(page, '/tags', 'POST', { name: `继承子-${runId}`, description: '', parentId: parent.id, conflictsWith: [], status: 'active' }), 201)
      const conflict = ok(await api(page, '/tags', 'POST', { name: `互斥-${runId}`, description: '', parentId: null, conflictsWith: [child.id], status: 'active' }), 201)
      const cycle = await api(page, `/tags/${parent.id}`, 'PATCH', { expectedRevision: parent.revision, parentId: child.id })
      assert.equal(cycle.status, 422)
      await page.goto('/manage')
      await page.getByRole('tab', { name: '标签', exact: true }).click()
      await page.locator('.tree-row').filter({ hasText: parent.name }).filter({ hasNotText: child.name }).getByRole('button', { name: '编辑', exact: true }).click()
      const parentSelect = page.getByRole('combobox', { name: '父节点', exact: true })
      assert.equal(await parentSelect.locator(`option[value="${child.id}"]`).count(), 0)
      await page.getByRole('tab', { name: '概念', exact: true }).click()
      await page.getByRole('button', { name: '新增概念', exact: true }).click()
      await page.getByRole('textbox', { name: '名称', exact: true }).fill(`概念验收-${runId}`)
      const concept = await clickResponse(page, page.getByRole('button', { name: '创建概念', exact: true }), '/ontology/concepts', 'POST', 201)
      const subConcept = ok(await api(page, '/ontology/concepts', 'POST', { name: `子概念-${runId}`, description: '', parentId: concept.id, status: 'active' }), 201)
      const conceptCycle = await api(page, `/ontology/concepts/${concept.id}`, 'PATCH', { expectedRevision: concept.revision, parentId: subConcept.id })
      assert.equal(conceptCycle.status, 422)
      record('concept-ui-create-and-server-cycle-rejection', { conceptId: concept.id, code: conceptCycle.body.error.code })
      await page.getByRole('tab', { name: '知识', exact: true }).click()
      await page.getByRole('button', { name: '新增知识', exact: true }).click()
      const form = page.locator('form').filter({ has: page.getByRole('button', { name: '创建知识', exact: true }) })
      await form.getByRole('textbox', { name: '名称', exact: true }).fill(`典故验收-${runId}`)
      await form.getByRole('textbox', { name: '正文（最多 20000 字）', exact: true }).fill('本条为联调测试数据，不作为历史史实。')
      await form.getByRole('checkbox', { name: new RegExp(child.name) }).check()
      const created = await clickResponse(page, form.getByRole('button', { name: '创建知识', exact: true }), '/knowledge', 'POST', 201)
      assert.ok(created.tagIds.includes(child.id))
      assert.ok(created.inheritedTagIds.includes(parent.id))
      const bad = await api(page, `/knowledge/${created.id}`, 'PATCH', { expectedRevision: created.revision, tagIds: [child.id, conflict.id] })
      assert.equal(bad.status, 422)
      const read = ok(await api(page, `/knowledge/${created.id}`))
      assert.equal(read.revision, created.revision)
      await page.locator('.knowledge-row').filter({ hasText: created.name }).getByRole('button', { name: '编辑', exact: true }).click()
      await page.getByRole('textbox', { name: '摘要（最多 500 字）', exact: true }).fill('持久化修改验收')
      const changed = await clickResponse(page, page.getByRole('button', { name: '保存修改', exact: true }), `/knowledge/${created.id}`, 'PATCH')
      assert.equal(changed.revision, created.revision + 1)
      record('knowledge-ui-write-inheritance-and-taxonomy-constraints', { entityId: created.id, inheritedTag: parent.id, cycleCode: cycle.body.error.code, conflictCode: bad.body.error.code, fixturePreparation: 'real API tags' })
      await shot(page, 'knowledge-inheritance.png')
    })
    await step('semantic-animation-reproduce', page, async () => {
      await page.goto('/')
      await applied(page)
      await page.getByRole('button', { name: '语义生成', exact: true }).click()
      await page.getByRole('textbox', { name: '典故或创作描述', exact: true }).fill('莲花清正')
      const lotus = await clickResponse(page, page.getByRole('button', { name: '按语义生成', exact: true }), '/generations', 'POST', 201)
      await page.getByRole('textbox', { name: '典故或创作描述', exact: true }).fill('竹节守正')
      const bamboo = await clickResponse(page, page.getByRole('button', { name: '按语义生成', exact: true }), '/generations', 'POST', 201)
      assert.notEqual(lotus.irHash, bamboo.irHash)
      assert.ok(lotus.provenance.matchedRules.length > 0)
      assert.ok(bamboo.provenance.matchedRules.length > 0)
      record('lotus-bamboo-real-semantic-difference', { lotus: lotus.id, bamboo: bamboo.id, lotusRules: lotus.provenance.matchedRules.map(r => r.ruleId), bambooRules: bamboo.provenance.matchedRules.map(r => r.ruleId) })
      await page.getByRole('textbox', { name: '典故或创作描述', exact: true }).fill(`完全无匹配xyz${runId}`)
      const noMatch = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/generations' && r.status() === 422)
      await page.getByRole('button', { name: '按语义生成', exact: true }).click()
      const rejected = await (await noMatch).json()
      assert.equal((await generation(page)).id, bamboo.id)
      record('unmatched-text-retains-previous-generation', { code: rejected.error.code })
      await page.getByRole('textbox', { name: '典故或创作描述', exact: true }).fill('四秒清莲叙事')
      await page.getByRole('textbox', { name: '知识实体 ID（以空格分隔）', exact: true }).fill('knowledge-lotus knowledge-bamboo')
      const animated = await clickResponse(page, page.getByRole('button', { name: '按语义生成', exact: true }), '/generations', 'POST', 201)
      assert.ok(animated.ir.durationMs > 0)
      assert.ok(animated.provenance.matchedEntities.length >= 2)
      const slider = page.getByRole('slider', { name: '时间轴', exact: true })
      await slider.fill(String(Math.floor(animated.ir.durationMs / 2)))
      assert.ok(await page.locator('.timeline-caption a').count())
      const link = await page.locator('.timeline-caption a').first().getAttribute('href')
      const knowledge = await owner.context.newPage()
      observe(knowledge, 'knowledge-link')
      await knowledge.goto(link)
      await knowledge.getByRole('heading', { level: 1 }).waitFor()
      await knowledge.close()
      const detail = await saveWork(page, `叙事验收-${runId}`)
      state.animation = detail
      assert.deepEqual(detail.renderJob.manifest.frames.map(f => f.timeMs), [0, Math.floor(animated.ir.durationMs / 2), animated.ir.durationMs])
      for (const frame of detail.renderJob.manifest.frames) {
        const response = await owner.context.request.get(`/api/v1/artifacts/${frame.artifactId}`)
        assert.equal(response.status(), 200)
        const png = await response.body()
        assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a')
        assert.equal(crypto.createHash('sha256').update(png).digest('hex'), frame.sha256)
      }
      record('narrative-knowledge-link-and-three-real-png-frames', { versionId: detail.workVersion.id, timesMs: detail.renderJob.timesMs, qualityReport: detail.renderJob.qualityReport })
      // Change one currently active rule through the actual rule editor.
      const ruleId = animated.provenance.matchedRules[0].ruleId
      const rule = ok(await api(admin.page, '/rules?pageSize=100')).items.find(r => r.id === ruleId)
      assert.ok(rule)
      await admin.page.goto('/admin/ops')
      await admin.page.getByRole('tab', { name: '映射规则', exact: true }).click()
      await admin.page.locator('.manager-row').filter({ hasText: rule.name }).getByRole('button').click()
      await admin.page.getByRole('textbox', { name: '规则说明', exact: true }).fill(`修改后规则-${runId}`)
      const effects = { ...rule.effects, palette: [rule.effects.palette[0] === '#315EA8' ? '#B42D6A' : '#315EA8', '#EAAD37', '#172A3D'] }
      await admin.page.getByRole('textbox', { name: '视觉效果 JSON', exact: true }).fill(JSON.stringify(effects))
      const changedRule = await clickResponse(admin.page, admin.page.getByRole('button', { name: '保存规则', exact: true }), `/rules/${rule.id}`, 'PATCH')
      assert.equal(changedRule.version, rule.version + 1)
      const regenerated = ok(await api(page, '/generations', 'POST', { mode: 'semantic', input: { text: '四秒清莲叙事', entityIds: ['knowledge-lotus', 'knowledge-bamboo'] }, backend: 'canvas2d', seed: 42 }), 201)
      assert.notEqual(regenerated.irHash, animated.irHash)
      await page.getByRole('button', { name: '复现此版本', exact: true }).click()
      await page.getByText('像素一致', { exact: true }).waitFor()
      const refreshed = ok(await api(page, `/work-versions/${detail.workVersion.id}`))
      assert.deepEqual(refreshed.generation.provenance, detail.generation.provenance)
      record('historical-ir-reproduces-after-rule-change', { originalJobId: detail.renderJob.id, changedRule: rule.id, newRuleVersion: changedRule.version, regeneratedId: regenerated.id })
      await shot(page, 'animation-reproduction.png')
      await page.setViewportSize({ width: 390, height: 844 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      await shot(page, 'animation-work-390.png')
      await page.setViewportSize({ width: 1440, height: 1000 })
    })
    await step('four-second-template', page, async () => {
      await page.goto('/?template=tpl-narrative')
      await applied(page)
      const animated = await generation(page)
      assert.equal(animated.ir.durationMs, 4000)
      const slider = page.getByRole('slider', { name: '时间轴', exact: true })
      await slider.fill('2000')
      await page.getByRole('button', { name: '播放', exact: true }).click()
      await page.getByRole('button', { name: '暂停', exact: true }).click()
      const pausedAt = await slider.inputValue()
      await page.waitForTimeout(100)
      assert.equal(await slider.inputValue(), pausedAt)
      const detail = await saveWork(page, `四秒叙事-${runId}`)
      assert.deepEqual(detail.renderJob.timesMs, [0, 2000, 4000])
      assert.equal(detail.renderJob.manifest.frames.length, 3)
      await page.reload()
      await page.getByRole('heading', { name: detail.workVersion.title, exact: true }).waitFor()
      record('four-second-template-play-pause-and-three-frame-save-reopen', { versionId: detail.workVersion.id, timesMs: detail.renderJob.timesMs })
    })
    await step('combined-search', page, async () => {
      await page.goto('/search?view=templates')
      await page.getByRole('textbox', { name: '关键词', exact: true }).fill('莲')
      await page.getByRole('textbox', { name: '时间主题', exact: true }).fill('不存在的时间主题')
      await page.getByRole('button', { name: '检索', exact: true }).click()
      await page.waitForURL(/timeTheme=/)
      const filtered = ok(await api(page, '/templates?q=莲&timeTheme=不存在的时间主题'))
      assert.equal(filtered.total, 0)
      await page.getByRole('textbox', { name: '时间主题', exact: true }).fill('夏日')
      await page.getByRole('button', { name: '检索', exact: true }).click()
      await page.waitForURL(/timeTheme=%E5%A4%8F/)
      const found = ok(await api(page, '/templates?q=莲&timeTheme=夏日'))
      assert.ok(found.items.some(t => t.id === 'tpl-lotus'))
      await page.reload()
      assert.equal(await page.getByRole('textbox', { name: '关键词', exact: true }).inputValue(), '莲')
      assert.equal(await page.getByRole('textbox', { name: '时间主题', exact: true }).inputValue(), '夏日')
      record('combined-search-and-url-reload', { impossibleTotal: filtered.total, matchedTotal: found.total })
    })
    await step('imports-exports-audit', admin.page, async () => {
      const page = admin.page
      await page.goto('/admin/ops')
      const unsupported = await api(page, '/admin/exports', 'POST', { types: ['rules', 'workSnapshots'] })
      if (unsupported.status === 422) result.blockers.push({ id: 'BE-EXPORT-TYPES-001', status: unsupported.status, code: unsupported.body.error.code, requestId: unsupported.requestId, types: ['rules', 'workSnapshots'] })
      else assert.equal(unsupported.status, 200)
      const exported = await clickResponse(page, page.getByRole('button', { name: '生成导出包', exact: true }), '/admin/exports', 'POST')
      for (const [type, items] of Object.entries(exported.data)) assert.equal(exported.manifest.counts[type], items.length)
      const initialCount = ok(await api(page, '/tags?pageSize=100')).total
      const pack = { schemaVersion: 1, exportedAt: new Date().toISOString(), manifest: { includesArtifacts: false, counts: { entities: 0, concepts: 0, tags: 2, templates: 0, rules: 0, workSnapshots: 0 } }, data: { entities: [], concepts: [], tags: [
        { id: `tag-import-parent-${runId}`, name: `导入父-${runId}`, parentId: null, conflictsWith: [], status: 'active' },
        { id: `tag-import-child-${runId}`, name: `导入子-${runId}`, parentId: 'missing-parent', conflictsWith: [], status: 'active' },
      ], templates: [], rules: [], workSnapshots: [] } }
      for (const tag of pack.data.tags) Object.assign(tag, { revision: 1, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), description: '' })
      await page.getByRole('tab', { name: '导入', exact: true }).click()
      const input = page.getByRole('textbox', { name: '或粘贴/编辑导入包 JSON', exact: true })
      await input.fill(JSON.stringify(pack))
      const invalid = await clickResponse(page, page.getByRole('button', { name: '校验导入包', exact: true }), '/admin/imports/validate', 'POST')
      assert.equal(invalid.valid, false)
      assert.ok(invalid.errors.some(e => e.code === 'MISSING_REFERENCE'))
      assert.equal(await page.getByRole('button', { name: '执行导入', exact: true }).isDisabled(), true)
      assert.equal(ok(await api(page, '/tags?pageSize=100')).total, initialCount)
      const bypass = await api(page, '/admin/imports/apply', 'POST', { validationId: invalid.validationId, expectedDataRevision: invalid.expectedDataRevision })
      assert.equal(bypass.status, 409)
      assert.equal(bypass.body.error.code, 'INVALID_STATE')
      assert.equal(ok(await api(page, '/tags?pageSize=100')).total, initialCount)
      pack.data.tags[1].parentId = pack.data.tags[0].id
      await input.fill(JSON.stringify(pack))
      const valid = await clickResponse(page, page.getByRole('button', { name: '校验导入包', exact: true }), '/admin/imports/validate', 'POST')
      assert.equal(valid.valid, true, JSON.stringify(valid.errors))
      const applied = await clickResponse(page, page.getByRole('button', { name: '执行导入', exact: true }), '/admin/imports/apply', 'POST', 201)
      assert.equal(applied.counts.tags, 2)
      assert.equal(ok(await api(page, '/tags?pageSize=100')).total, initialCount + 2)
      await page.getByRole('tab', { name: '审计日志', exact: true }).click()
      await page.locator('.audit-row').first().waitFor()
      const logs = ok(await api(page, '/audit-logs?pageSize=100')).items
      assert.ok(logs.some(log => log.action.includes('import')))
      assert.ok(logs.some(log => log.action.includes('review')))
      record('invalid-import-no-partial-write-valid-import-and-audit', { exportedCounts: exported.manifest.counts, invalidCodes: invalid.errors.map(e => e.code), importId: applied.importId, auditActions: [...new Set(logs.map(l => l.action))] })
      await shot(page, 'audit-log.png')
    })
    assert.deepEqual(result.pageErrors, [])
    result.status = result.failures.length ? 'failed' : result.blockers.length ? 'blocked' : 'passed-selected-paths'
  } catch (error) {
    result.status = 'failed'
    result.failures.push({ phase, message: redact(error.stack || error.message) })
  } finally {
    await browser.close()
    fs.writeFileSync(path.join(out, 'p1-results.json'), redact(JSON.stringify(result, null, 2)) + '\n')
    console.log(JSON.stringify({ status: result.status, checks: result.checks.length, blockers: result.blockers, failures: result.failures, out }, null, 2))
    process.exitCode = result.status === 'passed-selected-paths' ? 0 : result.status === 'blocked' ? 2 : 1
  }
}
main().catch(error => { console.error(redact(error.stack)); process.exitCode = 1 })
