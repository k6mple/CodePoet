const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

const out = __dirname
const base = 'http://127.0.0.1:3000'
const seedSource = fs.readFileSync(path.resolve(out, '../../../scripts/seed.py'), 'utf8')
const demoPassword = process.env.CODEPOET_DEMO_PASSWORD || seedSource.match(/DEMO_PASSWORD = "([^"]+)"/)?.[1]
if (!demoPassword) throw new Error('Cannot read the local demo password from scripts/seed.py')

const results = { steps: [], console: [], pageErrors: [], screenshots: [], overflow: [] }

async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  page.on('console', (message) => {
    if (message.type() === 'error') results.console.push({ url: page.url(), text: message.text() })
  })
  page.on('pageerror', (error) => results.pageErrors.push({ url: page.url(), text: String(error) }))

  const step = (name, data = {}) => {
    results.steps.push({ name, ...data })
    console.log(name, JSON.stringify(data))
  }

  async function screenshot(name, fullPage = true) {
    if (fullPage) {
      await page.evaluate(() => {
        window.scrollTo(0, 0)
        if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
      })
      await page.waitForTimeout(150)
    }
    await page.screenshot({ path: path.join(out, name), fullPage })
    results.screenshots.push(name)
  }

  async function assertOverflow(label) {
    const dims = await page.evaluate(() => ({
      innerWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }))
    results.overflow.push({ label, ...dims, overflow: dims.scrollWidth > dims.innerWidth })
    if (dims.scrollWidth > dims.innerWidth) throw new Error(`Horizontal overflow at ${label}: ${JSON.stringify(dims)}`)
  }

  async function login(username) {
    await page.goto(`${base}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(600)
    await page.getByRole('button', { name: '登录', exact: true }).first().click()
    await page.locator('dialog.login-dialog[open]').waitFor()
    await page.locator('#login-username').fill(username)
    await page.locator('#login-password').fill(demoPassword)
    await page.locator('.login-actions button[type=submit]').click()
    await page.waitForFunction(() => !document.querySelector('dialog.login-dialog[open]'), { timeout: 15000 })
    await page.getByRole('link', { name: '账号与资料' }).waitFor({ timeout: 15000 })
  }

  async function logout() {
    await page.getByRole('button', { name: '退出', exact: true }).click()
    await page.getByRole('button', { name: '登录', exact: true }).first().waitFor({ timeout: 15000 })
  }

  async function waitHeading(name) {
    await page.getByRole('heading', { name }).waitFor({ timeout: 15000 })
  }

  try {
    await login('developer_demo')
    const devNav = page.locator('.site-nav')
    step('developer nav', {
      space: await devNav.getByRole('link', { name: '个人空间' }).count(),
      search: await devNav.getByRole('link', { name: '检索' }).count(),
      review: await devNav.getByRole('link', { name: '审核台' }).count(),
      users: await devNav.getByRole('link', { name: '用户管理' }).count(),
      ops: await devNav.getByRole('link', { name: '运维' }).count(),
    })

    await page.goto(`${base}/space`, { waitUntil: 'networkidle' })
    await waitHeading('个人空间')
    await page.locator('.space-page .notice--error').first().waitFor({ timeout: 15000 })
    await screenshot('space-works-error-1440.png')
    await assertOverflow('space-works-error-1440')

    await page.getByRole('tab', { name: '创作会话' }).click()
    await page.getByText('会话列表加载失败', { exact: false }).waitFor({ timeout: 15000 })
    await screenshot('space-sessions-error-1440.png')
    await assertOverflow('space-sessions-error-1440')

    await page.getByRole('tab', { name: '个人资料' }).click()
    await page.getByRole('link', { name: '打开账号与资料' }).waitFor({ timeout: 5000 })
    await screenshot('space-profile-1440.png')
    await assertOverflow('space-profile-1440')

    await page.goto(`${base}/search`, { waitUntil: 'networkidle' })
    await waitHeading('组合检索')
    await page.locator('.search-page .notice--error').first().waitFor({ timeout: 15000 })
    const filterCount = await page.locator('.search-filters .field').count()
    step('search templates', { realApiError: true, filterCount })
    await screenshot('search-templates-error-1440.png')
    await assertOverflow('search-templates-error-1440')

    await page.getByRole('tab', { name: '知识' }).click()
    await page.waitForURL(/view=knowledge/, { timeout: 15000 })
    await page.waitForFunction(() => {
      const tab = document.querySelector('[role="tab"][aria-selected="true"]')
      return Boolean(tab?.textContent?.includes('知识'))
    }, { timeout: 15000 })
    await page.locator('.search-page .notice--error').first().waitFor({ timeout: 15000 })
    const knowledgeActive = await page.getByRole('tab', { name: '知识' }).getAttribute('aria-selected')
    step('search knowledge', { realApiError: true, knowledgeActive })
    await screenshot('search-knowledge-error-1440.png')
    await assertOverflow('search-knowledge-error-1440')

    await page.goto(`${base}/knowledge/test-knowledge`, { waitUntil: 'networkidle' })
    await waitHeading('知识加载失败')
    await page.locator('.knowledge-viewer .notice--error').first().waitFor({ timeout: 15000 })
    await screenshot('knowledge-detail-error-1440.png')
    await assertOverflow('knowledge-detail-error-1440')

    await page.goto(`${base}/admin/ops`, { waitUntil: 'networkidle' })
    await page.getByText('无权访问', { exact: true }).waitFor({ timeout: 15000 })
    step('developer ops gate', { forbidden: true })
    await screenshot('admin-ops-forbidden-developer-1440.png')
    await assertOverflow('admin-ops-forbidden-developer-1440')

    await page.goto(`${base}/account`, { waitUntil: 'networkidle' })
    await page.locator('.account-form').waitFor({ timeout: 15000 })
    const devLinks = page.locator('.account-links')
    step('developer account links', {
      space: await devLinks.getByRole('link', { name: '个人空间' }).count(),
      ops: await devLinks.getByRole('link', { name: '运维与审计' }).count(),
    })

    await logout()
    await login('expert_demo')
    const expertNav = page.locator('.site-nav')
    step('expert nav', {
      review: await expertNav.getByRole('link', { name: '审核台' }).count(),
      ops: await expertNav.getByRole('link', { name: '运维' }).count(),
      users: await expertNav.getByRole('link', { name: '用户管理' }).count(),
    })

    await page.goto(`${base}/admin/ops`, { waitUntil: 'networkidle' })
    await waitHeading('运维与审计')
    const expertTabs = await page.getByRole('tab').allTextContents()
    await page.getByRole('button', { name: '生成导出包' }).click()
    await page.locator('.admin-ops-page .notice--error').first().waitFor({ timeout: 15000 })
    const exportError = (await page.locator('.admin-ops-page .notice--error').first().textContent()) ?? ''
    step('expert ops/export', { tabs: expertTabs, realApiError: exportError.includes('HTTP') })
    await screenshot('admin-ops-export-error-expert-1440.png')
    await assertOverflow('admin-ops-export-error-expert-1440')

    await logout()
    await login('admin_demo')
    const adminNav = page.locator('.site-nav')
    step('admin nav', {
      ops: await adminNav.getByRole('link', { name: '运维' }).count(),
      users: await adminNav.getByRole('link', { name: '用户管理' }).count(),
    })

    await page.goto(`${base}/admin/ops`, { waitUntil: 'networkidle' })
    await waitHeading('运维与审计')
    const adminTabs = await page.getByRole('tab').allTextContents()
    step('admin ops tabs', { tabs: adminTabs })
    await screenshot('admin-ops-tabs-1440.png')
    await assertOverflow('admin-ops-tabs-1440')

    await page.getByRole('tab', { name: '导入' }).click()
    await page.locator('.admin-ops-page .json-editor').waitFor({ timeout: 5000 })
    await page.locator('.admin-ops-page .json-editor').fill('not-json')
    await page.getByRole('button', { name: '校验导入包' }).click()
    await page.getByText('JSON 语法错误', { exact: false }).waitFor({ timeout: 5000 })
    await screenshot('admin-import-parse-error-1440.png')

    await page.locator('.admin-ops-page .json-editor').fill('{}')
    await page.getByRole('button', { name: '校验导入包' }).click()
    await page.locator('.admin-ops-page .notice--error').first().waitFor({ timeout: 15000 })
    const importError = (await page.locator('.admin-ops-page .notice--error').first().textContent()) ?? ''
    step('admin import api', { realApiError: importError.includes('HTTP') })
    await screenshot('admin-import-api-error-1440.png')

    await page.getByRole('tab', { name: '审计日志' }).click()
    await page.getByText('审计日志加载失败', { exact: false }).waitFor({ timeout: 15000 })
    await screenshot('admin-audit-error-1440.png')
    await assertOverflow('admin-audit-error-1440')

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${base}/admin/ops`, { waitUntil: 'networkidle' })
    await waitHeading('运维与审计')
    await screenshot('admin-ops-390.png')
    await assertOverflow('admin-ops-390')

    await page.goto(`${base}/space`, { waitUntil: 'networkidle' })
    await waitHeading('个人空间')
    await page.locator('.space-page .notice--error').first().waitFor({ timeout: 15000 })
    await screenshot('space-390.png')
    await assertOverflow('space-390')

    await page.goto(`${base}/search`, { waitUntil: 'networkidle' })
    await waitHeading('组合检索')
    await page.locator('.search-page .notice--error').first().waitFor({ timeout: 15000 })
    await screenshot('search-390.png')
    await assertOverflow('search-390')

    results.consoleErrorCount = results.console.length
    fs.writeFileSync(path.join(out, 'p1a-space-search-ops-check-results.json'), JSON.stringify(results, null, 2))
    console.log('RESULT', JSON.stringify({
      steps: results.steps.length,
      consoleErrors: results.console.length,
      pageErrors: results.pageErrors.length,
      screenshots: results.screenshots.length,
      overflow: results.overflow.every((item) => !item.overflow),
    }))
  } catch (error) {
    results.failure = String(error)
    await screenshot('failure-1440.png').catch(() => {})
    fs.writeFileSync(path.join(out, 'p1a-space-search-ops-check-results.json'), JSON.stringify(results, null, 2))
    console.log('FAILURE', String(error))
    process.exitCode = 1
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error); process.exit(1) })
