// Real DeepSeek API + Production UI acceptance script for CodePoet X1 LLM Assistant.
// Requires an isolated localhost deployment (migrated to 014) and environment-only secrets.
// Outputs sanitized JSON evidence & screenshots strictly under frontend/local-work-logs/.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'

const { chromium } = createRequire(import.meta.url)('playwright')

const base = process.env.CODEPOET_X1_BASE_URL || 'http://127.0.0.1:3000'
const backendPort = Number(process.env.CODEPOET_X1_BACKEND_PORT || '8000')
const dataDir = process.env.CODEPOET_X1_DATA_DIR
const outputDir = process.env.CODEPOET_X1_OUTPUT_DIR
const password = process.env.CODEPOET_ACCEPTANCE_PASSWORD
const llmApiKey = process.env.CODEPOET_X1_LLM_API_KEY || ''
const llmBaseUrl = process.env.CODEPOET_X1_LLM_BASE_URL || 'https://api.deepseek.com'
const llmModel = process.env.CODEPOET_X1_LLM_MODEL || 'deepseek-chat'
const llmTimeout = process.env.CODEPOET_X1_LLM_TIMEOUT || '30'

if (!['localhost', '127.0.0.1'].includes(new URL(base).hostname)) {
  throw new Error('CODEPOET_X1_BASE_URL must point to localhost or 127.0.0.1')
}
if (!dataDir || !outputDir || !password || !llmApiKey) {
  throw new Error('Requires CODEPOET_X1_DATA_DIR, CODEPOET_X1_OUTPUT_DIR, CODEPOET_ACCEPTANCE_PASSWORD, CODEPOET_X1_LLM_API_KEY')
}
const out = path.resolve(outputDir)
if (!out.toLowerCase().includes(`${path.sep}local-work-logs${path.sep}`)) {
  throw new Error('X1 acceptance output must stay under a local-work-logs directory')
}
await mkdir(out, { recursive: true })

const repoRoot = path.resolve(import.meta.dirname, '..', '..')
const backendPython = path.join(repoRoot, 'backend', '.venv', 'Scripts', 'python.exe')

const redact = (value) => {
  const s = typeof value === 'string' ? value : JSON.stringify(value)
  return s.replaceAll(password, '[redacted]').replaceAll(llmApiKey, '[redacted-key]')
}

const report = {
  status: 'running',
  scope: 'x1-real-deepseek-production-ui',
  startedAt: new Date().toISOString(),
  base,
  samples: [],
  checks: [],
  pageErrors: [],
}

async function waitForHealth(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const r = await fetch(url)
      if (r.ok) return
    } catch {
      // retry
    }
    await new Promise((r) => setTimeout(r, 300))
  }
  throw new Error(`Service at ${url} did not become healthy within ${timeoutMs}ms`)
}

function startBackend({ withKey }) {
  const env = {
    ...process.env,
    CODEPOET_DATA_DIR: dataDir,
    CODEPOET_ENABLED_BACKENDS: 'canvas2d,css,webgl',
    CODEPOET_KNOWLEDGE_GRAPH_SOURCES: 'wikidata',
    CODEPOET_KNOWLEDGE_GRAPH_MAPPING_FILE: 'shared/demo/knowledge-graph-mappings.json',
    CODEPOET_KNOWLEDGE_GRAPH_TIMEOUT_SECONDS: '10',
    CODEPOET_COOKIE_SECURE: 'false',
    CODEPOET_LLM_BASE_URL: llmBaseUrl,
    CODEPOET_LLM_API_KEY: withKey ? llmApiKey : '',
    CODEPOET_LLM_MODEL: llmModel,
    CODEPOET_LLM_TIMEOUT_SECONDS: llmTimeout,
  }
  const proc = spawn(
    backendPython,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(backendPort)],
    { cwd: path.join(repoRoot, 'backend'), env, stdio: ['ignore', 'pipe', 'pipe'] },
  )
  return {
    proc,
    async stop() {
      if (!proc.killed) {
        proc.kill('SIGTERM')
        await new Promise((resolve) => {
          const t = setTimeout(() => {
            try { proc.kill('SIGKILL') } catch {}
            resolve()
          }, 4000)
          proc.once('exit', () => { clearTimeout(t); resolve() })
        })
      }
    },
  }
}

async function loginPage(browser, username) {
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1100 } })
  const p = await context.newPage()
  p.setDefaultTimeout(90000)
  p.setDefaultNavigationTimeout(90000)
  p.on('pageerror', (e) => report.pageErrors.push(redact(e.message)))
  await p.goto('/')
  await p.getByRole('button', { name: '登录', exact: true }).first().click()
  await p.locator('#login-username').fill(username)
  await p.locator('#login-password').fill(password)
  await p.locator('dialog button[type=submit]').click()
  await p.locator('dialog[open]').waitFor({ state: 'hidden' })
  await p.getByText('画面已应用').waitFor({ state: 'visible', timeout: 30000 })
  return { context, page: p }
}

async function rawApi(p, endpoint, method = 'GET', body) {
  return p.evaluate(async ({ endpoint, method, body }) => {
    const headers = {}
    if (method !== 'GET') {
      headers['X-CSRF-Token'] = (await (await fetch('/api/v1/me')).json()).csrfToken
      headers['Content-Type'] = 'application/json'
    }
    const r = await fetch('/api/v1' + endpoint, {
      method,
      headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    const parsed = r.status === 204 ? null : await r.json().catch(() => null)
    return { status: r.status, body: parsed }
  }, { endpoint, method, body })
}

async function saveAndReproduce(page, workTitle, screenshotName) {
  const titleInput = page.locator('.archive-primary input').first()
  await titleInput.fill(workTitle)
  await page.getByRole('button', { name: '保存私有作品', exact: true }).click()
  const savedLink = page.locator('.archive-success a')
  await savedLink.waitFor({ state: 'visible', timeout: 45000 })
  const href = await savedLink.getAttribute('href')
  assert.ok(href, 'Expected saved work version link')
  await savedLink.click()
  await page.waitForURL(/\/works\/versions\//)

  const detailsSummary = page.getByText('生成依据与来源快照', { exact: true }).first()
  await detailsSummary.waitFor({ state: 'visible', timeout: 20000 })
  const assistantSection = page.locator('section[aria-label="AI 智能辅助来源"]').first()
  if (!(await assistantSection.isVisible().catch(() => false))) {
    await detailsSummary.click()
  }
  await assistantSection.waitFor({ state: 'visible', timeout: 15000 })
  const provenanceText = await assistantSection.innerText()
  assert.ok(provenanceText.includes('提案哈希'), 'Work page must display assistant proposalHash')

  await page.getByRole('button', { name: '复现此版本', exact: true }).click()
  await page.getByText('像素一致', { exact: true }).waitFor({ state: 'visible', timeout: 45000 })

  if (screenshotName) {
    await page.screenshot({ path: path.join(out, screenshotName), fullPage: true })
  }
  return { versionUrl: href, exactMatch: true }
}

async function runUiProposal(page, { name, target, rootNodeId, prompt, workTitle, readyShot, workShot }) {
  await page.goto('/')
  await page.getByText('画面已应用').waitFor({ state: 'visible', timeout: 30000 })

  const targetBtnLabel = target === 'ir_fragment'
    ? 'IR 片段改造 (ir_fragment)'
    : '参数空间生成 (parameter_schema)'
  await page.getByRole('button', { name: targetBtnLabel, exact: true }).click()

  let chosenRoot = null
  if (target === 'ir_fragment') {
    const nodeSelect = page.getByLabel('目标 IR 节点')
    if (rootNodeId) {
      await nodeSelect.selectOption(rootNodeId)
      chosenRoot = rootNodeId
    } else {
      chosenRoot = await nodeSelect.inputValue()
    }
  }

  await page.getByLabel('AI 辅助自然语言需求').fill(prompt)

  let finalProposal = null
  const onResponse = async (res) => {
    const url = res.url()
    if (url.includes('/api/v1/assistant/proposals') && !url.endsWith('/apply')) {
      const json = await res.json().catch(() => null)
      if (json && json.id && (json.status === 'ready' || json.status === 'failed')) {
        finalProposal = json
      }
    }
  }
  page.on('response', onResponse)

  await page.getByRole('button', { name: '生成 AI 提案', exact: true }).click()

  const applyBtn = page.getByRole('button', { name: '应用提案', exact: true })
  await applyBtn.waitFor({ state: 'visible', timeout: 75000 })
  page.off('response', onResponse)

  assert.ok(finalProposal && finalProposal.status === 'ready', `Proposal ${name} must be ready`)
  assert.equal(finalProposal.validation?.isolatedValidationPassed, true)

  if (readyShot) {
    await page.screenshot({ path: path.join(out, readyShot), fullPage: true })
  }

  let appliedFromClick = null
  const onApplyResp = async (res) => {
    if (res.url().includes(`/api/v1/assistant/proposals/${finalProposal.id}/apply`)) {
      const json = await res.json().catch(() => null)
      if (json && json.id) appliedFromClick = json.id
    }
  }
  page.on('response', onApplyResp)
  await applyBtn.click()
  await page.locator('.studio-assistant .archive-success').waitFor({ state: 'visible', timeout: 20000 })
  page.off('response', onApplyResp)

  const applyCheck = await rawApi(page, `/assistant/proposals/${finalProposal.id}/apply`, 'POST')
  assert.equal(applyCheck.status, 201)
  const generationId = applyCheck.body.id
  if (appliedFromClick) assert.equal(generationId, appliedFromClick)

  const workInfo = await saveAndReproduce(page, workTitle, workShot)
  const summary = {
    name,
    target,
    rootNodeId: chosenRoot,
    proposalId: finalProposal.id,
    model: finalProposal.model,
    attempts: finalProposal.attempts,
    proposalHash: finalProposal.proposalHash,
    isolatedValidationPassed: finalProposal.validation?.isolatedValidationPassed,
    repairedIssueCount: (finalProposal.validation?.repairedIssues || []).length,
    repairedIssues: (finalProposal.validation?.repairedIssues || []).map((i) => ({
      code: i.code,
      path: i.path,
      message: i.message,
    })),
    appliedGenerationId: generationId,
    savedWorkTitle: workTitle,
    savedVersionUrl: workInfo.versionUrl,
    reproduceExactMatch: workInfo.exactMatch,
  }
  report.samples.push(summary)
  return summary
}

const browser = await chromium.launch({ channel: 'chrome', headless: true })
let backend = null

try {
  // Phase 1: Unconfigured API Key
  backend = startBackend({ withKey: false })
  await waitForHealth(`http://127.0.0.1:${backendPort}/api/v1/health`)
  {
    const { context, page } = await loginPage(browser, 'creator_demo')
    const caps = await rawApi(page, '/engine/capabilities')
    assert.equal(caps.status, 200)
    assert.equal(caps.body.features.llmAssistant, false)

    await page.getByText('当前未配置模型密钥，AI 辅助提案不可用').waitFor({ state: 'visible' })
    const submitBtn = page.getByRole('button', { name: '生成 AI 提案', exact: true })
    assert.equal(await submitBtn.isDisabled(), true)

    const genId = (await page.getByText('生成编号', { exact: true }).locator('..').locator('dd').textContent()).trim()
    const api503 = await rawApi(page, '/assistant/proposals', 'POST', {
      baseGenerationId: genId,
      prompt: '测试未配置密钥返回 503',
      target: 'ir_fragment',
      rootNodeId: await page.getByLabel('目标 IR 节点').inputValue(),
    })
    assert.equal(api503.status, 503)
    assert.equal(api503.body.error.code, 'ASSISTANT_UNAVAILABLE')

    await page.screenshot({ path: path.join(out, '01-disabled-no-key.png'), fullPage: true })
    report.checks.push({
      name: 'unconfigured-api-key',
      llmAssistantFeature: false,
      uiButtonDisabled: true,
      apiStatus: 503,
      errorCode: 'ASSISTANT_UNAVAILABLE',
    })
    await context.close()
  }
  await backend.stop()
  backend = null

  // Phase 2: Real DeepSeek API + Production UI (3 + 1 samples)
  backend = startBackend({ withKey: true })
  await waitForHealth(`http://127.0.0.1:${backendPort}/api/v1/health`)
  {
    const { context, page } = await loginPage(browser, 'creator_demo')
    const caps = await rawApi(page, '/engine/capabilities')
    assert.equal(caps.status, 200)
    assert.equal(caps.body.features.llmAssistant, true)
    report.checks.push({ name: 'configured-api-key', llmAssistantFeature: true })

    const genId = (await page.getByText('生成编号', { exact: true }).locator('..').locator('dd').textContent()).trim()
    const genObj = (await rawApi(page, `/generations/${genId}`)).body
    const leafNodes = genObj.ir.nodes.filter((n) => n.type !== 'group')
    assert.ok(leafNodes.length >= 2, 'Expected at least 2 leaf nodes in default template IR')
    const node1 = leafNodes[0].id
    const node2 = leafNodes[1].id

    await runUiProposal(page, {
      name: 'sample1-ir-fragment-lotus',
      target: 'ir_fragment',
      rootNodeId: node1,
      prompt: `将选中节点改造为清雅对称的双层青莲花瓣与莲心纹样（保持根节点 id 为 "${node1}" 且延续原 parentId），采用宣纸素雅青绿与淡粉配色，确保全部坐标位于 800x600 画布中央内`,
      workTitle: 'X1-AI清莲片段改造',
      readyShot: '02-sample1-ir-fragment-ready.png',
      workShot: '03-sample1-work-reproduced.png',
    })

    await runUiProposal(page, {
      name: 'sample2-ir-fragment-bamboo',
      target: 'ir_fragment',
      rootNodeId: node2,
      prompt: `将选中节点替换为挺拔修长的翠竹竹节与两片清雅竹叶矢量图元（保持根节点 id 为 "${node2}" 且延续原 parentId），采用墨绿与竹青素雅配色，保持全部顶点严格在画布边界内`,
      workTitle: 'X1-AI翠竹片段改造',
      readyShot: null,
      workShot: null,
    })

    await runUiProposal(page, {
      name: 'sample3-parameter-schema',
      target: 'parameter_schema',
      rootNodeId: null,
      prompt: '为当前模板设计 2 到 3 个清晰可控的参数（例如核心尺寸与主体填色），绑定到模板现有合法节点属性，并设置雅致的廉洁青墨默认配色，确保所有参数边界值与默认值均合法且不越界',
      workTitle: 'X1-AI参数空间扩展',
      readyShot: '04-sample3-parameter-schema-ready.png',
      workShot: null,
    })

    const repairSample = await runUiProposal(page, {
      name: 'sample4-real-auto-repair',
      target: 'ir_fragment',
      rootNodeId: node1,
      prompt: `请严格按以下两阶段测试规则执行：在第一次输出（尚未收到校验错误反馈前），请输出一个 group 根节点（id="${node1}"，延续原 parentId）并在其下故意放一个冲突子节点 id="${node2}" 且圆心坐标 cx=950, cy=300, r=60；当你收到平台隔离校验返回的冲突与越界错误反馈后，再把该子节点修复为不冲突的新 id（如 lotus_repaired_core）并将圆心移回画布中央 (400, 300)、颜色 #5B8C75。`,
      workTitle: 'X1-AI自动修复验证',
      readyShot: '05-sample4-real-auto-repair.png',
      workShot: '06-sample4-work-reproduced.png',
    })
    assert.equal(repairSample.attempts, 2, 'Sample 4 must trigger real attempt-2 auto-repair')
    assert.ok(repairSample.repairedIssueCount > 0, 'Sample 4 must record repaired attempt-1 issues')

    await context.close()
  }

  assert.equal(report.pageErrors.length, 0, `Unexpected page errors: ${report.pageErrors.join('; ')}`)
  report.status = 'passed'
  report.finishedAt = new Date().toISOString()
  await writeFile(path.join(out, 'x1-acceptance-result.json'), JSON.stringify(report, null, 2), 'utf8')
  console.log(JSON.stringify(report, null, 2))
} catch (err) {
  report.status = 'failed'
  report.error = redact(err?.stack || String(err))
  report.finishedAt = new Date().toISOString()
  await writeFile(path.join(out, 'x1-acceptance-result.json'), JSON.stringify(report, null, 2), 'utf8')
  console.error(report.error)
  process.exitCode = 1
} finally {
  if (backend) await backend.stop()
  await browser.close()
}
