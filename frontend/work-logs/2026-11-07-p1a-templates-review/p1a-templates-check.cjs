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

  async function waitForHeading(name) {
    await page.getByRole('heading', { name }).waitFor({ timeout: 15000 })
  }

  try {
    await login('developer_demo')
    const devNav = page.locator('.site-nav')
    step('developer nav', {
      templateLibrary: await devNav.getByRole('link', { name: '模板库' }).count(),
      templateManage: await devNav.getByRole('link', { name: '模板管理' }).count(),
      knowledgeManage: await devNav.getByRole('link', { name: '知识管理' }).count(),
      reviews: await devNav.getByRole('link', { name: '审核台' }).count(),
      users: await devNav.getByRole('link', { name: '用户管理' }).count(),
    })
    await screenshot('nav-developer-1440.png')

    await page.goto(`${base}/templates`, { waitUntil: 'networkidle' })
    await waitForHeading('模板库')
    await page.locator('.template-library .notice--error').first().waitFor({ timeout: 15000 })
    step('public template library', { realApiError: true })
    await screenshot('templates-library-error-1440.png')
    await assertOverflow('templates-library-error-1440')

    await page.goto(`${base}/manage/templates`, { waitUntil: 'networkidle' })
    await waitForHeading('模板管理')
    await page.locator('.template-manager .notice--error').first().waitFor({ timeout: 15000 })
    await page.getByRole('button', { name: '新建模板' }).click()
    await page.getByLabel('模板名称').waitFor({ timeout: 5000 })
    await page.getByLabel('模板名称').fill('验证模板')
    await page.getByLabel('说明（最多 1000 字）').fill('P1A-03 前端验证：模板元数据表单与真实 API 错误。')
    await page.locator('.checklist--inline').getByText('莲').click()
    await page.getByRole('button', { name: '创建模板容器' }).click()
    await page.locator('.editor-panel .notice--error').first().waitFor({ timeout: 15000 })
    const createError = (await page.locator('.editor-panel .notice--error').first().textContent()) ?? ''
    step('template create form', { realApiError: createError.includes('HTTP'), errorShown: createError.includes('创建失败') })
    await screenshot('template-manager-create-error-1440.png')
    await assertOverflow('template-manager-create-error-1440')

    await page.goto(`${base}/manage/templates/test-template`, { waitUntil: 'networkidle' })
    await waitForHeading('模板加载失败')
    await page.locator('.template-editor-page .notice--error').first().waitFor({ timeout: 15000 })
    await screenshot('template-editor-error-1440.png')
    await assertOverflow('template-editor-error-1440')

    await page.goto(`${base}/templates/test-template`, { waitUntil: 'networkidle' })
    await waitForHeading('模板加载失败')
    await page.locator('.template-viewer .notice--error').first().waitFor({ timeout: 15000 })
    await screenshot('template-detail-error-1440.png')
    await assertOverflow('template-detail-error-1440')

    await page.goto(`${base}/reviews`, { waitUntil: 'networkidle' })
    await page.getByText('无权访问', { exact: true }).waitFor({ timeout: 15000 })
    step('developer review gate', { forbidden: true })
    await screenshot('reviews-forbidden-developer-1440.png')
    await assertOverflow('reviews-forbidden-developer-1440')

    await page.goto(`${base}/account`, { waitUntil: 'networkidle' })
    await page.locator('.account-form').waitFor({ timeout: 15000 })
    const accountLinks = page.locator('.account-links')
    step('developer account links', {
      templateManage: await accountLinks.getByRole('link', { name: '模板管理' }).count(),
      reviews: await accountLinks.getByRole('link', { name: '审核台' }).count(),
      users: await accountLinks.getByRole('link', { name: '用户与角色审批' }).count(),
    })
    await screenshot('account-developer-1440.png')

    await logout()
    await login('expert_demo')
    const expertNav = page.locator('.site-nav')
    step('expert nav', {
      templateLibrary: await expertNav.getByRole('link', { name: '模板库' }).count(),
      templateManage: await expertNav.getByRole('link', { name: '模板管理' }).count(),
      knowledgeManage: await expertNav.getByRole('link', { name: '知识管理' }).count(),
      reviews: await expertNav.getByRole('link', { name: '审核台' }).count(),
      users: await expertNav.getByRole('link', { name: '用户管理' }).count(),
    })

    await page.goto(`${base}/reviews`, { waitUntil: 'networkidle' })
    await waitForHeading('审核台')
    await page.locator('.review-page .notice--error').first().waitFor({ timeout: 15000 })
    const reviewTabs = await page.locator('.manager-tabs--inline').getByRole('button').allTextContents()
    step('review desk', { realApiError: true, tabs: reviewTabs })
    await screenshot('reviews-error-1440.png')
    await assertOverflow('reviews-error-1440')

    await page.setViewportSize({ width: 1024, height: 900 })
    await page.goto(`${base}/reviews`, { waitUntil: 'networkidle' })
    await page.locator('.review-page .notice--error').first().waitFor({ timeout: 15000 })
    await screenshot('reviews-error-1024.png')
    await assertOverflow('reviews-error-1024')

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${base}/reviews`, { waitUntil: 'networkidle' })
    await page.locator('.review-page .notice--error').first().waitFor({ timeout: 15000 })
    await screenshot('reviews-error-390.png')
    await assertOverflow('reviews-error-390')

    await page.goto(`${base}/account`, { waitUntil: 'networkidle' })
    await page.locator('.account-form').waitFor({ timeout: 15000 })
    const expertAccountLinks = page.locator('.account-links')
    step('expert account links', {
      templateManage: await expertAccountLinks.getByRole('link', { name: '模板管理' }).count(),
      reviews: await expertAccountLinks.getByRole('link', { name: '审核台' }).count(),
      users: await expertAccountLinks.getByRole('link', { name: '用户与角色审批' }).count(),
    })
    await screenshot('account-expert-390.png')
    await assertOverflow('account-expert-390')

    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`${base}/templates`, { waitUntil: 'networkidle' })
    await page.locator('.template-library .notice--error').first().waitFor({ timeout: 15000 })
    await screenshot('templates-library-error-expert-1440.png')
    await assertOverflow('templates-library-error-expert-1440')

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${base}/manage/templates`, { waitUntil: 'networkidle' })
    await page.locator('.template-manager .notice--error').first().waitFor({ timeout: 15000 })
    await page.getByRole('button', { name: '新建模板' }).click()
    await page.getByLabel('模板名称').waitFor({ timeout: 5000 })
    await page.getByLabel('模板名称').fill('移动端模板')
    await page.locator('.checklist--inline').getByText('竹').click()
    await screenshot('template-manager-create-390.png')
    await assertOverflow('template-manager-create-390')

    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`${base}/manage/templates/test-template`, { waitUntil: 'networkidle' })
    await waitForHeading('模板加载失败')
    await page.locator('.notice--error').first().waitFor({ timeout: 15000 })
    await screenshot('template-editor-error-expert-1440.png')

    results.consoleErrorCount = results.console.length
    fs.writeFileSync(path.join(out, 'p1a-templates-check-results.json'), JSON.stringify(results, null, 2))
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
    fs.writeFileSync(path.join(out, 'p1a-templates-check-results.json'), JSON.stringify(results, null, 2))
    console.log('FAILURE', String(error))
    process.exitCode = 1
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error); process.exit(1) })
