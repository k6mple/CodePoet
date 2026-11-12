// Verify a small rehearsal catalogue from actual saved versions; never persist sessions.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { chromium } = require('playwright')
const base = process.env.CODEPOET_P1_BASE_URL || 'http://127.0.0.1:13160'
const password = process.env.CODEPOET_ACCEPTANCE_PASSWORD
if (!password || !['localhost', '127.0.0.1'].includes(new URL(base).hostname)) throw new Error('Local isolated service and credential required')
const root = path.resolve(process.env.CODEPOET_CATALOG_ROOT || 'frontend/work-logs/2026-11-12-p1-integration-delivery')
const read = name => JSON.parse(fs.readFileSync(path.join(root, name), 'utf8'))
async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } })
  const api = context.request
  try {
    const csrf = await (await api.get('/api/v1/auth/csrf')).json()
    const login = await api.post('/api/v1/auth/login', { headers: { 'X-CSRF-Token': csrf.csrfToken }, data: { username: 'creator_demo', password } })
    assert.equal(login.status(), 200)
    const p1 = read('p1/p1-results.json')
    const delivery = read('delivery/delivery-results.json')
    const templates = read('templates/template-review-results.json')
    assert.equal(p1.status, 'passed-selected-paths')
    assert.equal(delivery.status, 'passed')
    assert.equal(templates.templateReviewPassed, true)
    const checks = p1.checks
    const selected = [
      { label: '莲参数作品', versionId: checks.find(c => c.name === 'work-two-versions-history-unchanged-and-anonymous-pending-404').firstVersion },
      { label: '竹子树与裁剪', versionId: delivery.checks.find(c => c.name === 'cropped-real-png-save-reopen-and-quality').versionId },
      { label: '四秒叙事', versionId: checks.find(c => c.name === 'four-second-template-play-pause-and-three-frame-save-reopen').versionId },
    ]
    const page = await context.newPage()
    const pageErrors = []
    page.on('pageerror', e => pageErrors.push(e.message))
    for (const work of selected) {
      const response = await api.get(`/api/v1/work-versions/${work.versionId}`)
      assert.equal(response.status(), 200)
      const detail = await response.json()
      Object.assign(work, { title: detail.workVersion.title, status: detail.workVersion.status, workId: detail.workVersion.workId, path: `/works/versions/${work.versionId}`, frameCount: detail.renderJob.manifest.frames.length, timesMs: detail.renderJob.timesMs })
      for (const frame of detail.renderJob.manifest.frames) {
        const png = await api.get(`/api/v1/artifacts/${frame.artifactId}`)
        assert.equal(png.status(), 200)
        assert.equal(crypto.createHash('sha256').update(await png.body()).digest('hex'), frame.sha256)
      }
      await page.goto(work.path)
      await page.getByRole('heading', { name: work.title, exact: true }).waitFor()
      await page.getByRole('img', { name: /^服务器保存的 PNG/ }).waitFor()
      await page.waitForFunction(() => {
        const image = document.querySelector('img[alt^="服务器保存的 PNG"]')
        return image?.complete && image.naturalWidth > 0
      })
    }
    assert.deepEqual(pageErrors, [])
    const template = templates.checks.find(c => c.name === 'ui-archive-fallback-remove-from-public-and-keep-history')
    const catalogue = { status: 'passed', scope: 'local-integration-rehearsal-data', base, accounts: ['creator_demo', 'developer_demo', 'expert_demo', 'admin_demo'], works: selected, templateReviewHistory: { templateId: template.templateId, path: `/manage/templates/${template.templateId}` }, allFramesSha256Verified: true, browserOpenedThreeWorks: true, pageErrors, credentialsIncluded: false }
    fs.writeFileSync(path.join(root, 'demo-catalog.json'), JSON.stringify(catalogue, null, 2) + '\n')
    console.log(JSON.stringify({ status: catalogue.status, works: catalogue.works.length, roles: catalogue.accounts.length, credentialsIncluded: false }))
  } finally { await browser.close() }
}
main().catch(error => { console.error(String(error.stack).split(password).join('[redacted]')); process.exitCode = 1 })
