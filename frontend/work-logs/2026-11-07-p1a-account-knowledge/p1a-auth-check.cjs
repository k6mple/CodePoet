const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')

const out = __dirname
const base = 'http://127.0.0.1:3000'
const seedSource = fs.readFileSync(path.resolve(out, '../../../scripts/seed.py'), 'utf8')
const demoPassword = process.env.CODEPOET_DEMO_PASSWORD || seedSource.match(/DEMO_PASSWORD = "([^"]+)"/)?.[1]
if (!demoPassword) throw new Error('Cannot read the local demo password from scripts/seed.py')
const testPassword = crypto.randomBytes(18).toString('base64url')
const username = `p1a_${Date.now().toString(36)}`
const nickname = 'P1A 检查用户'
const updatedNickname = 'P1A 已验证'
const bio = '资料保存验证：昵称与简介写入服务器后刷新页面仍保留。'

const results = { username, steps: [], console: [], pageErrors: [], screenshots: [], overflow: [] }
const browserPath = process.env.PLAYWRIGHT_CHROME_PATH || 'chrome'

async function main() {
  const browser = await chromium.launch({ channel: browserPath, headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
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
      bodyScrollWidth: document.body.scrollWidth,
    }))
    results.overflow.push({ label, ...dims, overflow: dims.scrollWidth > dims.innerWidth })
    if (dims.scrollWidth > dims.innerWidth) throw new Error(`Horizontal overflow at ${label}: ${JSON.stringify(dims)}`)
  }

  async function waitForLoginClosed() {
    await page.waitForFunction(() => !document.querySelector('dialog.login-dialog[open]'), { timeout: 15000 })
  }

  try {
    await page.goto(`${base}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(800)

    // Register a temporary creator with a pending developer application.
    await page.getByRole('button', { name: '登录', exact: true }).first().click()
    await page.locator('dialog.login-dialog[open]').waitFor()
    await page.getByRole('button', { name: '注册新账号' }).click()
    await page.locator('#register-role').waitFor()
    await page.locator('#login-username').fill(username)
    await page.locator('#login-password').fill(testPassword)
    await page.locator('#register-nickname').fill(nickname)
    await page.locator('#register-role').selectOption('developer')
    await screenshot('register-dialog-1440.png', false)
    await page.locator('.login-actions button[type=submit]').click()
    await page.locator('.login-success').waitFor({ timeout: 20000 })
    const successText = (await page.locator('.login-success').textContent())?.trim() ?? ''
    step('register', { successText, hasPendingRole: successText.includes('角色申请已提交') })
    await screenshot('register-success-1440.png', false)

    // Log in with the new account.
    await page.locator('#login-password').fill(testPassword)
    await page.locator('.login-actions button[type=submit]').click()
    await waitForLoginClosed()
    await page.getByRole('link', { name: '账号与资料' }).waitFor({ timeout: 15000 })
    step('login test user', { username, headerVisible: true })

    // Account page: pending role application and profile edit.
    await page.goto(`${base}/account`, { waitUntil: 'networkidle' })
    await page.locator('.account-form').waitFor({ timeout: 15000 })
    const accountText = (await page.locator('.account-page').textContent()) ?? ''
    step('account pending application', {
      pending: accountText.includes('待审批'),
      requestedRole: accountText.includes('模板开发者'),
      currentRole: accountText.includes('普通创作者'),
    })
    if (!accountText.includes('待审批') || !accountText.includes('模板开发者')) {
      throw new Error('Pending developer application is not visible on the account page')
    }
    await screenshot('account-pending-1440.png')
    await assertOverflow('account-pending-1440')

    await page.locator('.account-form input').fill(updatedNickname)
    await page.locator('.account-form textarea').fill(bio)
    await page.getByRole('button', { name: '保存资料' }).click()
    await page.locator('.account-page .notice--success').waitFor({ timeout: 15000 })
    const savedText = (await page.locator('.account-page .notice--success').textContent())?.trim() ?? ''
    step('profile save', { savedText })
    await screenshot('account-saved-1440.png')

    await page.reload({ waitUntil: 'networkidle' })
    await page.locator('.account-form').waitFor({ timeout: 15000 })
    const persistedNickname = await page.locator('.account-form input').inputValue()
    const persistedBio = await page.locator('.account-form textarea').inputValue()
    step('profile persisted', {
      nicknamePersisted: persistedNickname === updatedNickname,
      bioPersisted: persistedBio === bio,
      persistedNickname,
    })
    if (persistedNickname !== updatedNickname || persistedBio !== bio) {
      throw new Error('Profile values did not persist after reload')
    }

    // A creator must not see management entries or be able to use direct URLs.
    await page.goto(`${base}/manage`, { waitUntil: 'networkidle' })
    await page.getByText('无权访问', { exact: true }).waitFor({ timeout: 15000 })
    const creatorNavHasManage = await page.getByRole('link', { name: '知识管理' }).count()
    step('creator manage gate', { forbidden: true, navHasManage: creatorNavHasManage })
    await screenshot('manage-forbidden-creator-1440.png')
    await assertOverflow('manage-forbidden-creator-1440')
    await page.goto(`${base}/admin/users`, { waitUntil: 'networkidle' })
    await page.getByText('无权访问', { exact: true }).waitFor({ timeout: 15000 })
    const creatorNavHasAdmin = await page.getByRole('link', { name: '用户管理' }).count()
    step('creator admin gate', { forbidden: true, navHasAdmin: creatorNavHasAdmin })
    await screenshot('admin-forbidden-creator-1440.png')
    await assertOverflow('admin-forbidden-creator-1440')

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${base}/account`, { waitUntil: 'networkidle' })
    await page.locator('.account-form').waitFor({ timeout: 15000 })
    await screenshot('account-390.png')
    await assertOverflow('account-390')

    // Logout, capture the registration dialog at 390, then log in as the seeded admin.
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.getByRole('button', { name: '退出', exact: true }).click()
    await page.getByRole('button', { name: '登录', exact: true }).first().waitFor({ timeout: 15000 })
    step('logout', { anonymous: true })

    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('button', { name: '登录', exact: true }).first().click()
    await page.getByRole('button', { name: '注册新账号' }).click()
    await page.locator('#register-role').waitFor()
    const registerDialog = await page.evaluate(() => {
      const dialog = document.querySelector('dialog.login-dialog')
      if (!dialog) return null
      const rect = dialog.getBoundingClientRect()
      return { width: rect.width, height: rect.height, innerWidth: window.innerWidth, scrollWidth: document.documentElement.scrollWidth }
    })
    step('register dialog mobile', { registerDialog })
    await screenshot('register-dialog-390.png', false)
    await assertOverflow('register-dialog-390')
    await page.keyboard.press('Escape')

    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.getByRole('button', { name: '登录', exact: true }).first().click()
    await page.locator('dialog.login-dialog[open]').waitFor()
    await page.locator('#login-username').fill('admin_demo')
    await page.locator('#login-password').fill(demoPassword)
    await page.locator('.login-actions button[type=submit]').click()
    await waitForLoginClosed()
    await page.getByRole('link', { name: '用户管理' }).waitFor({ timeout: 15000 })
    step('login admin', { adminHeaderVisible: true })

    await page.goto(`${base}/account`, { waitUntil: 'networkidle' })
    await page.locator('.account-form').waitFor({ timeout: 15000 })
    const adminAccountText = (await page.locator('.account-page').textContent()) ?? ''
    step('admin account', {
      roleAdmin: adminAccountText.includes('管理员'),
      permissionsVisible: adminAccountText.includes('users.approveRole'),
    })
    await screenshot('account-admin-1440.png')
    await assertOverflow('account-admin-1440')

    // The admin list endpoint is not implemented; the real error state must be shown.
    await page.goto(`${base}/admin/users`, { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: '用户与角色审批' }).waitFor({ timeout: 15000 })
    await page.locator('.admin-page .notice--error').waitFor({ timeout: 15000 })
    const adminError = (await page.locator('.admin-page .notice--error').textContent())?.trim() ?? ''
    step('admin users endpoint', {
      errorShown: adminError.includes('用户列表加载失败'),
      realApiError: adminError.includes('HTTP'),
    })
    await screenshot('admin-users-error-1440.png')
    await assertOverflow('admin-users-error-1440')
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${base}/admin/users`, { waitUntil: 'networkidle' })
    await page.locator('.admin-page .notice--error').waitFor({ timeout: 15000 })
    await screenshot('admin-users-error-390.png')
    await assertOverflow('admin-users-error-390')

    // Knowledge endpoints are not implemented; tabs and real error states must still render.
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`${base}/manage`, { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: '知识管理' }).waitFor({ timeout: 15000 })
    const tabCount = await page.getByRole('tab').count()
    await page.locator('.manager-page .notice--error').first().waitFor({ timeout: 15000 })
    const manageErrors = await page.locator('.manager-page .notice--error').allTextContents()
    step('knowledge manager', {
      tabCount,
      errorShown: manageErrors.length > 0,
      realApiError: manageErrors.some((text) => text.includes('HTTP')),
    })
    if (tabCount !== 3) throw new Error(`Expected 3 knowledge tabs for admin, got ${tabCount}`)
    await screenshot('manage-knowledge-error-1440.png')
    await assertOverflow('manage-knowledge-error-1440')

    await page.getByRole('tab', { name: '概念' }).click()
    await page.locator('#knowledge-panel-concepts').waitFor({ state: 'visible', timeout: 5000 })
    await screenshot('manage-concepts-1440.png')
    const conceptPanelVisible = await page.locator('#knowledge-panel-concepts').isVisible()

    await page.getByRole('tab', { name: '标签' }).click()
    await page.locator('#knowledge-panel-tags').waitFor({ state: 'visible', timeout: 5000 })
    await screenshot('manage-tags-1440.png')
    const tagPanelVisible = await page.locator('#knowledge-panel-tags').isVisible()
    step('knowledge tabs', { conceptPanelVisible, tagPanelVisible })

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${base}/manage`, { waitUntil: 'networkidle' })
    await page.locator('.manager-page .notice--error').first().waitFor({ timeout: 15000 })
    await screenshot('manage-error-390.png')
    await assertOverflow('manage-error-390')

    // Exercise the editor surfaces and their real error handling while the P1A-02 endpoints are absent.
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`${base}/manage`, { waitUntil: 'networkidle' })
    await page.locator('.manager-page .notice--error').first().waitFor({ timeout: 15000 })
    await page.getByRole('tab', { name: '知识' }).click()
    await page.locator('#knowledge-panel-knowledge').waitFor({ state: 'visible', timeout: 5000 })
    await page.getByRole('button', { name: '新增知识' }).click()
    await page.locator('.knowledge-editor input[required]').first().waitFor({ timeout: 10000 })
    await page.locator('.knowledge-editor input[required]').first().fill('验证知识')
    await page.locator('.knowledge-editor textarea').nth(0).fill('别名一\n别名二')
    await page.locator('.knowledge-editor textarea').nth(1).fill('客户端表单验证用摘要。')
    await page.locator('.knowledge-editor textarea').nth(2).fill('客户端表单验证用正文。')
    await page.getByRole('button', { name: '添加来源' }).click()
    await page.getByLabel('来源标题').fill('测试来源')
    await page.getByLabel('来源 URL').fill('not-a-url')
    await page.getByRole('button', { name: '创建知识' }).click()
    const validationNotice = page.locator('.knowledge-editor .notice--error')
    await validationNotice.waitFor({ timeout: 5000 })
    const validationText = (await validationNotice.textContent()) ?? ''
    step('knowledge form validation', { urlRejected: validationText.includes('URL 必须以') })
    await screenshot('knowledge-editor-validation-1440.png')
    await page.getByLabel('来源 URL').fill('https://example.com/source')
    await page.getByRole('button', { name: '创建知识' }).click()
    await page.waitForFunction(() => document.querySelector('.knowledge-editor .notice--error')?.textContent?.includes('HTTP'), { timeout: 15000 })
    step('knowledge form api error', { realApiError: true })
    await screenshot('knowledge-editor-api-error-1440.png')
    await page.getByRole('button', { name: '关闭编辑' }).click()

    await page.getByRole('tab', { name: '概念' }).click()
    await page.locator('#knowledge-panel-concepts').waitFor({ state: 'visible', timeout: 5000 })
    await page.getByRole('button', { name: '新增概念' }).click()
    await page.locator('.taxonomy-editor input[required]').first().waitFor({ timeout: 10000 })
    await page.locator('.taxonomy-editor input[required]').first().fill('验证概念')
    await page.getByRole('button', { name: '创建概念' }).click()
    await page.locator('.taxonomy-editor .notice--error').waitFor({ timeout: 15000 })
    step('concept form api error', { realApiError: true })
    await screenshot('concept-editor-api-error-1440.png')
    await page.getByRole('button', { name: '关闭编辑' }).click()

    await page.getByRole('tab', { name: '标签' }).click()
    await page.locator('#knowledge-panel-tags').waitFor({ state: 'visible', timeout: 5000 })
    await page.getByRole('button', { name: '新增标签' }).click()
    await page.locator('.taxonomy-editor input[required]').first().waitFor({ timeout: 10000 })
    await page.locator('.taxonomy-editor input[required]').first().fill('验证标签')
    await page.getByRole('button', { name: '创建标签' }).click()
    await page.locator('.taxonomy-editor .notice--error').waitFor({ timeout: 15000 })
    step('tag form api error', { realApiError: true })
    await screenshot('tag-editor-api-error-1440.png')
    await page.getByRole('button', { name: '关闭编辑' }).click()

    results.pageErrors = results.pageErrors
    results.consoleErrorCount = results.console.length
    fs.writeFileSync(path.join(out, 'p1a-auth-check-results.json'), JSON.stringify(results, null, 2))
    console.log('RESULT', JSON.stringify({
      username,
      steps: results.steps.length,
      consoleErrors: results.console.length,
      pageErrors: results.pageErrors.length,
      screenshots: results.screenshots.length,
      overflow: results.overflow.every((item) => !item.overflow),
    }))
  } catch (error) {
    results.failure = String(error)
    await screenshot('failure-1440.png').catch(() => {})
    fs.writeFileSync(path.join(out, 'p1a-auth-check-results.json'), JSON.stringify(results, null, 2))
    console.log('FAILURE', String(error))
    process.exitCode = 1
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error); process.exit(1) })
