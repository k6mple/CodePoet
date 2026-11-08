const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

const out = __dirname
const base = 'http://127.0.0.1:3000'
const results = { steps: [], console: [], pageErrors: [], screenshots: [], overflow: [] }

async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
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
    }))
    results.overflow.push({ label, ...dims, overflow: dims.scrollWidth > dims.innerWidth })
    if (dims.scrollWidth > dims.innerWidth) throw new Error(`Horizontal overflow at ${label}: ${JSON.stringify(dims)}`)
  }

  async function selectFixture(value) {
    await page.locator('.fixture-controls select').selectOption(value)
    await page.waitForTimeout(250)
  }

  async function timeValue() {
    const text = (await page.locator('.timeline-time').textContent()) ?? '0 / 0 ms'
    return Number(text.split('/')[0].trim().replace(/\D/g, ''))
  }

  async function seek(value) {
    await page.locator('.timeline-controls input[type="range"]').evaluate((element, next) => {
      const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
      descriptor?.set?.call(element, String(next))
      element.dispatchEvent(new Event('input', { bubbles: true }))
      element.dispatchEvent(new Event('change', { bubbles: true }))
    }, value)
    await page.waitForTimeout(120)
  }

  async function canvasPixel(x, y) {
    return page.evaluate(([px, py]) => {
      const canvas = document.querySelector('.timeline-canvas-frame canvas')
      if (!canvas) return null
      const context = canvas.getContext('2d')
      return Array.from(context.getImageData(px, py, 1, 1).data)
    }, [x, y])
  }

  try {
    await page.goto(`${base}/fixture`, { waitUntil: 'networkidle' })
    await page.locator('.timeline-canvas-frame canvas').waitFor({ timeout: 15000 })
    const defaultCanvas = await page.locator('.timeline-canvas-frame canvas').evaluate((canvas) => ({ width: canvas.width, height: canvas.height }))
    const staticProbe = await page.locator('.timeline-static').isVisible()
    step('probe fixture', { canvas: defaultCanvas, staticProbe })
    await screenshot('fixture-probe-1440.png')
    await assertOverflow('fixture-probe-1440')

    await selectFixture('animation')
    await page.locator('.timeline-controls').waitFor({ timeout: 5000 })
    const animationCanvas = await page.locator('.timeline-canvas-frame canvas').evaluate((canvas) => ({ width: canvas.width, height: canvas.height }))
    await page.getByRole('button', { name: '播放', exact: true }).click()
    await page.waitForTimeout(500)
    const playingTime = await timeValue()
    await page.getByRole('button', { name: '暂停', exact: true }).click()
    await seek(1500)
    const middleTime = await timeValue()
    const middlePixel = await canvasPixel(400, 300)
    step('animation timeline', { canvas: animationCanvas, playingTime, middleTime, middlePixel })
    if (playingTime <= 0) throw new Error('Timeline playback did not advance')
    if (middleTime !== 1500) throw new Error(`Expected seek 1500, got ${middleTime}`)
    if (!middlePixel || middlePixel[0] < 150 || middlePixel[3] < 200) throw new Error(`Expected red dot at 1500ms, got ${JSON.stringify(middlePixel)}`)
    await screenshot('fixture-animation-1440.png')
    await assertOverflow('fixture-animation-1440')

    await selectFixture('narrative')
    await page.locator('.timeline-controls').waitFor({ timeout: 5000 })
    const markerCount = await page.locator('.timeline-marker').count()
    const initialCaption = (await page.locator('.timeline-caption p').textContent())?.trim()
    await page.getByRole('button', { name: '1500ms', exact: true }).click()
    await page.waitForTimeout(120)
    const middleCaption = (await page.locator('.timeline-caption p').textContent())?.trim()
    step('narrative markers', { markerCount, initialCaption, middleCaption })
    if (markerCount !== 4) throw new Error(`Expected 4 narrative markers, got ${markerCount}`)
    if (initialCaption !== '水润万物，清而不争。') throw new Error(`Unexpected initial caption: ${initialCaption}`)
    if (middleCaption !== '出淤泥而不染。') throw new Error(`Unexpected middle caption: ${middleCaption}`)
    await screenshot('fixture-narrative-1440.png')
    await assertOverflow('fixture-narrative-1440')

    const downloadPromise = page.waitForEvent('download', { timeout: 10000 }).catch(() => null)
    await page.getByRole('button', { name: '导出结束帧 PNG' }).click()
    const download = await downloadPromise
    let downloadInfo = null
    if (download) {
      const file = path.join(out, 'narrative-export.png')
      await download.saveAs(file)
      const buffer = fs.readFileSync(file)
      downloadInfo = {
        size: buffer.length,
        signature: Array.from(buffer.subarray(0, 8)),
        width: buffer.readUInt32BE(16),
        height: buffer.readUInt32BE(20),
      }
    }
    step('narrative export', { downloadInfo })
    if (!downloadInfo || downloadInfo.width !== 800 || downloadInfo.height !== 600) {
      throw new Error(`Expected 800x600 PNG export, got ${JSON.stringify(downloadInfo)}`)
    }

    await selectFixture('crop')
    await page.locator('.timeline-static').waitFor({ timeout: 5000 })
    const cropCanvas = await page.locator('.timeline-canvas-frame canvas').evaluate((canvas) => ({ width: canvas.width, height: canvas.height }))
    const cropViewport = (await page.locator('.fixture-meta').textContent()) ?? ''
    step('crop fixture', { canvas: cropCanvas, hasViewport: cropViewport.includes('200, 150') })
    if (cropCanvas.width !== 400 || cropCanvas.height !== 300) throw new Error(`Unexpected crop canvas ${JSON.stringify(cropCanvas)}`)
    await screenshot('fixture-crop-1440.png')
    await assertOverflow('fixture-crop-1440')

    await selectFixture('lotus')
    const lotusCanvas = await page.locator('.timeline-canvas-frame canvas').evaluate((canvas) => ({ width: canvas.width, height: canvas.height }))
    step('lotus template fixture', { canvas: lotusCanvas })
    await screenshot('fixture-lotus-1440.png')
    await assertOverflow('fixture-lotus-1440')

    await selectFixture('narrative-template')
    await page.locator('.timeline-controls').waitFor({ timeout: 5000 })
    const templateMarkers = await page.locator('.timeline-marker').count()
    await page.getByRole('button', { name: '1200ms', exact: true }).click()
    await page.waitForTimeout(120)
    const templateCaption = (await page.locator('.timeline-caption p').textContent())?.trim()
    step('narrative template fixture', { markerCount: templateMarkers, caption: templateCaption })
    await screenshot('fixture-narrative-template-1440.png')
    await assertOverflow('fixture-narrative-template-1440')

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${base}/fixture`, { waitUntil: 'networkidle' })
    await page.locator('.timeline-canvas-frame canvas').waitFor({ timeout: 15000 })
    await screenshot('fixture-390.png')
    await assertOverflow('fixture-390')
    await selectFixture('animation')
    await page.locator('.timeline-controls').waitFor({ timeout: 5000 })
    await screenshot('fixture-animation-390.png')
    await assertOverflow('fixture-animation-390')

    results.consoleErrorCount = results.console.length
    fs.writeFileSync(path.join(out, 'timeline-check-results.json'), JSON.stringify(results, null, 2))
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
    fs.writeFileSync(path.join(out, 'timeline-check-results.json'), JSON.stringify(results, null, 2))
    console.log('FAILURE', String(error))
    process.exitCode = 1
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error); process.exit(1) })
