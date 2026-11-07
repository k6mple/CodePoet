const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

const out = __dirname
const base = 'http://127.0.0.1:3000'

async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const results = { pages: [], checks: [], console: [], pageErrors: [] }
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
  const page = await context.newPage()
  page.on('console', (msg) => {
    if (msg.type() === 'error') results.console.push({ url: page.url(), text: msg.text() })
  })
  page.on('pageerror', (error) => results.pageErrors.push({ url: page.url(), text: String(error) }))

  async function capture(name, url, width, height, wait = 2200) {
    await page.setViewportSize({ width, height })
    await page.goto(`${base}${url}`, { waitUntil: 'networkidle' }).catch(() => {})
    await page.waitForTimeout(wait)
    const dims = await page.evaluate(() => ({
      innerWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
    }))
    await page.screenshot({ path: path.join(out, name), fullPage: true })
    results.pages.push({ name, url, viewport: width, dims, overflow: dims.scrollWidth > dims.innerWidth })
  }

  const viewports = [
    { name: '1440', width: 1440, height: 1000 },
    { name: '1024', width: 1024, height: 900 },
    { name: '768', width: 768, height: 900 },
    { name: '390', width: 390, height: 844 },
  ]
  for (const viewport of viewports) {
    await capture(`home-${viewport.name}.png`, '/', viewport.width, viewport.height)
    await capture(`works-${viewport.name}.png`, '/works', viewport.width, viewport.height)
    await capture(`fixture-${viewport.name}.png`, '/fixture', viewport.width, viewport.height, 1200)
    await capture(`work-detail-error-${viewport.name}.png`, '/works/versions/does-not-exist', viewport.width, viewport.height, 2200)
  }

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`${base}/`, { waitUntil: 'networkidle' }).catch(() => {})
  await page.waitForTimeout(1000)
  const loginButton = page.getByRole('button', { name: '登录', exact: true }).first()
  await loginButton.focus()
  await loginButton.click()
  await page.waitForTimeout(300)
  const dialogVisible = await page.locator('dialog.login-dialog[open]').isVisible()
  const focusedAfterOpen = await page.evaluate(() => document.activeElement?.id || document.activeElement?.tagName)
  let focusTrapped = true
  for (let index = 0; index < 12; index += 1) {
    await page.keyboard.press('Tab')
    const inside = await page.evaluate(() => Boolean(document.activeElement?.closest('dialog.login-dialog')))
    if (!inside) focusTrapped = false
  }
  await page.screenshot({ path: path.join(out, 'login-dialog-1440.png'), fullPage: false })
  await page.keyboard.press('Escape')
  await page.waitForTimeout(300)
  const dialogClosed = (await page.locator('dialog.login-dialog[open]').count()) === 0
  const focusRestored = await page.evaluate(() => document.activeElement?.textContent?.trim() === '登录')
  results.checks.push({ name: 'login dialog', dialogVisible, focusedAfterOpen, focusTrapped, dialogClosed, focusRestored })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${base}/`, { waitUntil: 'networkidle' }).catch(() => {})
  await page.waitForTimeout(1000)
  await page.getByRole('button', { name: '登录', exact: true }).first().click()
  await page.waitForTimeout(300)
  const dialogDims = await page.evaluate(() => {
    const dialog = document.querySelector('dialog.login-dialog')
    if (!dialog) return null
    const rect = dialog.getBoundingClientRect()
    return { width: rect.width, height: rect.height, innerWidth: window.innerWidth, scrollWidth: document.documentElement.scrollWidth }
  })
  await page.screenshot({ path: path.join(out, 'login-dialog-390.png'), fullPage: false })
  results.checks.push({ name: 'login dialog mobile', dialogDims, overflow: dialogDims ? dialogDims.scrollWidth > dialogDims.innerWidth : null })
  await page.keyboard.press('Escape')

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`${base}/fixture`, { waitUntil: 'networkidle' }).catch(() => {})
  await page.waitForTimeout(1000)
  const canvas = await page.evaluate(() => {
    const element = document.querySelector('.stage-surface canvas')
    if (!element) return null
    const context2d = element.getContext('2d')
    const pixel = (x, y) => Array.from(context2d.getImageData(x, y, 1, 1).data)
    return { width: element.width, height: element.height, tl: pixel(10, 10), br: pixel(760, 560), center: pixel(410, 320) }
  })
  const downloadPromise = page.waitForEvent('download', { timeout: 10000 }).catch(() => null)
  await page.getByRole('button', { name: '导出验证 PNG' }).click()
  const download = await downloadPromise
  let downloadInfo = null
  if (download) {
    const file = path.join(out, 'fixture-export.png')
    await download.saveAs(file)
    const buffer = fs.readFileSync(file)
    downloadInfo = {
      size: buffer.length,
      signature: Array.from(buffer.subarray(0, 8)),
      width: buffer.readUInt32BE(16),
      height: buffer.readUInt32BE(20),
    }
  }
  results.checks.push({ name: 'fixture canvas', canvas, downloadInfo })

  await page.goto(`${base}/`, { waitUntil: 'networkidle' }).catch(() => {})
  await page.waitForTimeout(700)
  await page.keyboard.press('Tab')
  const focusVisible = await page.evaluate(() => {
    const element = document.activeElement
    if (!element) return null
    const style = getComputedStyle(element)
    return { tag: element.tagName, text: element.textContent?.trim().slice(0, 20), outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth }
  })
  results.checks.push({ name: 'focus-visible', focusVisible })

  fs.writeFileSync(path.join(out, 'browser-check-results.json'), JSON.stringify(results, null, 2))
  console.log(JSON.stringify(results, null, 2))
  await browser.close()
}

main().catch((error) => { console.error(error); process.exit(1) })
