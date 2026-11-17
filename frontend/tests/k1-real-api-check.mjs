// Real isolated API + production UI acceptance for CodePoet K1 external knowledge graph.
// Secrets are environment-only; output must point at an ignored local work-log directory.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'

const { chromium } = createRequire(import.meta.url)('playwright')
const base = process.env.CODEPOET_K1_BASE_URL
const password = process.env.CODEPOET_ACCEPTANCE_PASSWORD
const output = process.env.CODEPOET_K1_OUTPUT_DIR
if (!base || !['localhost', '127.0.0.1'].includes(new URL(base).hostname) || !password || !output) {
  throw new Error('Requires isolated localhost deployment, seed password and local output directory')
}
const out = path.resolve(output)
if (!out.toLowerCase().includes(`${path.sep}local-work-logs${path.sep}`)) {
  throw new Error('K1 acceptance output must stay under a local-work-logs directory')
}
await mkdir(out, { recursive: true })

const redact = (value) => (typeof value === 'string' ? value : JSON.stringify(value)).replaceAll(password, '[redacted]')
const result = {
  status: 'running',
  scope: 'real-api-production-ui-k1-external-knowledge-graph',
  base,
  startedAt: new Date().toISOString(),
  entities: [],
  checks: [],
  failures: [],
  pageErrors: [],
  unverified: [
    {
      item: '未配置任何来源时的 503（KNOWLEDGE_GRAPH_UNAVAILABLE，details.available=[]）',
      reason: '当前后端已配置 wikidata；关闭来源需要重启后端，本轮约束禁止重启已有服务。',
    },
    {
      item: '来源超时 504 与断网后重开作品的离线复现',
      reason: '未构造可控超时/断网条件；本轮只验证真实正常来源与可测错误路径。',
    },
  ],
}
const record = (name, details = {}) => result.checks.push({ name, ...details })
const fail = (name, message) => result.failures.push({ name, message: redact(message) })
const graphEndpoint = '/knowledge-graph/generations'
const entities = [
  { key: 'lotus', id: 'Q16528', label: '莲' },
  { key: 'bamboo', id: 'Q670887', label: '竹' },
  { key: 'plum', id: 'Q157763', label: '梅' },
]

const browser = await chromium.launch({ channel: 'chrome', headless: true })
let page

async function actor(username) {
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } })
  const p = await context.newPage()
  p.setDefaultTimeout(120000)
  p.setDefaultNavigationTimeout(120000)
  p.on('pageerror', (e) => result.pageErrors.push(redact(e.message)))
  await p.goto('/')
  await p.getByRole('button', { name: '登录', exact: true }).first().click()
  await p.locator('#login-username').fill(username)
  await p.locator('#login-password').fill(password)
  await p.locator('dialog button[type=submit]').click()
  await p.locator('dialog[open]').waitFor({ state: 'hidden' })
  return p
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
    let parsed = null
    try {
      parsed = r.status === 204 ? null : await r.json()
    } catch {
      parsed = null
    }
    return { status: r.status, body: parsed }
  }, { endpoint, method, body })
}

async function api(p, endpoint, method = 'GET', body, expected = 200) {
  const response = await rawApi(p, endpoint, method, body)
  assert.equal(response.status, expected, redact(response.body))
  return response.body
}

async function currentGenerationId(p) {
  return p.getByText('生成编号', { exact: true }).locator('..').locator('dd').textContent()
}

async function ensureEvidence(p, entityId) {
  const section = p.locator('section[aria-label="外部知识图谱来源快照"]').first()
  if (!(await section.isVisible().catch(() => false))) {
    const summary = p.getByText('生成依据与来源快照', { exact: true })
    if (await summary.count()) await summary.first().click()
  }
  await section.waitFor({ state: 'visible' })
  const text = await section.innerText()
  assert.match(text, /来源键/)
  assert.ok(text.includes('wikidata'), 'source key wikidata must be visible in the snapshot')
  assert.ok(text.includes(entityId), `external entity ${entityId} must be visible in the snapshot`)
  return text.replaceAll(password, '[redacted]')
}

async function openSemanticGraph(p) {
  await p.getByRole('button', { name: '语义生成', exact: true }).click()
  await p.getByLabel('来源键', { exact: true }).waitFor()
  await p.waitForFunction(() => !document.querySelector('.graph-form fieldset')?.disabled)
}

async function generateGraphEntity(p, entity) {
  await p.getByLabel('来源键', { exact: true }).fill('wikidata')
  await p.getByLabel('外部实体 ID', { exact: true }).fill(entity.id)
  const responsePromise = p.waitForResponse(
    (r) => new URL(r.url()).pathname === '/api/v1/knowledge-graph/generations' && r.request().method() === 'POST',
    { timeout: 120000 },
  )
  await p.getByRole('button', { name: '从外部图谱生成', exact: true }).click()
  const response = await responsePromise
  const status = response.status()
  const body = await response.json()
  const request = response.request().postDataJSON()
  if (status === 422 && body?.error?.code === 'NO_SEMANTIC_MATCH') {
    result.blocker = {
      code: body.error.code,
      message: body.error.message,
      externalEntityId: entity.id,
      fetchedLabels: body.error.details?.labels ?? null,
      hint: '站内知识库没有可映射条目；确认后端数据目录已执行 scripts/seed_demo.py（或改用已 seed 的隔离库）后重跑。',
    }
  }
  assert.equal(status, 201, redact(body))
  assert.equal(request.sourceId, 'wikidata')
  assert.equal(request.externalEntityId, entity.id)
  assert.equal(request.backend, 'canvas2d')
  assert.ok(body?.provenance?.externalGraph, 'generation.provenance.externalGraph is required')
  const graph = body.provenance.externalGraph
  assert.equal(graph.sourceId, 'wikidata')
  assert.equal(graph.externalEntityId, entity.id)
  assert.ok(Array.isArray(graph.entities) && graph.entities.length > 0, 'external graph entities required')
  assert.ok(Array.isArray(graph.relations), 'external graph relations required')
  assert.ok(Array.isArray(graph.mappingDecisions), 'external graph mappingDecisions required')
  const root = graph.entities.find((item) => item.id === entity.id) ?? graph.entities[0]
  assert.ok(typeof root.label === 'string' && root.label.trim(), 'external entity label must be non-empty')
  assert.ok(graph.relations.length >= 2, `expected >= 2 relations, got ${graph.relations.length}`)
  const affects = new Set(graph.mappingDecisions.map((item) => item.affects))
  assert.ok(affects.has('rule-selection'), 'mappingDecisions must cover rule-selection')
  assert.ok(affects.has('caption'), 'mappingDecisions must cover caption')
  assert.ok(typeof graph.contentHash === 'string' && graph.contentHash.length >= 8, 'contentHash required')

  await p.waitForFunction((id) => {
    const dt = [...document.querySelectorAll('dt')].find((e) => e.textContent === '生成编号')
    return dt?.nextElementSibling?.textContent === id
  }, body.id)
  await p.waitForFunction(() => !document.querySelector('.graph-form fieldset')?.disabled)
  await p.getByRole('button', { name: '从外部图谱生成', exact: true }).waitFor()
  const uiGenerationId = await currentGenerationId(p)
  assert.equal(uiGenerationId, body.id, 'UI must display the backend generation id')
  const evidence = await ensureEvidence(p, entity.id)

  const refetched = await api(p, `/generations/${body.id}`)
  const refetchedGraph = refetched.provenance?.externalGraph
  assert.equal(refetchedGraph?.sourceId, 'wikidata')
  assert.equal(refetchedGraph?.externalEntityId, entity.id)
  assert.equal(refetchedGraph?.contentHash, graph.contentHash)

  const details = {
    generationId: body.id,
    requestId: body.requestId,
    httpStatus: status,
    backend: body.backend,
    mode: body.mode,
    sourceId: graph.sourceId,
    externalEntityId: graph.externalEntityId,
    entityLabel: root.label,
    entityCount: graph.entities.length,
    relationCount: graph.relations.length,
    mappingDecisionCount: graph.mappingDecisions.length,
    mappingAffects: [...affects].sort(),
    contentHash: graph.contentHash,
    fetchedAt: graph.fetchedAt,
    dataVersion: graph.dataVersion ?? null,
    uiGenerationId,
    evidenceVisible: true,
    evidenceExcerpt: evidence.slice(0, 600),
    refetchVerified: true,
  }
  await p.screenshot({ path: path.join(out, `${entity.key}-generation.png`), fullPage: true })
  await p.locator('section[aria-label="外部知识图谱来源快照"]').first()
    .screenshot({ path: path.join(out, `${entity.key}-evidence.png`) })
  result.entities.push({ key: entity.key, ...details })
  record(`${entity.key}-generation`, details)
  return { generation: body, graph, details }
}

async function saveCurrentWork(p, generation, title) {
  assert.equal(await currentGenerationId(p), generation.id)
  await p.getByRole('textbox', { name: '作品名称', exact: true }).fill(title)
  await p.waitForFunction(() => !document.querySelector('.archive-actions button.button--primary')?.disabled)
  const jobResponsePromise = p.waitForResponse(
    (r) => new URL(r.url()).pathname === '/api/v1/render-jobs' && r.request().method() === 'POST',
    { timeout: 120000 },
  )
  await p.locator('.archive-actions button.button--primary').first().click()
  const jobResponse = await jobResponsePromise
  const jobStatus = jobResponse.status()
  const jobPayload = jobResponse.request().postDataJSON()
  assert.equal(jobStatus, 202, `render job creation returned ${jobStatus}`)
  assert.equal(jobPayload.backend, 'canvas2d')
  assert.equal(jobPayload.generationId, generation.id)

  const link = p.getByRole('link', { name: '打开已保存的作品', exact: true })
  await link.waitFor({ timeout: 120000 })
  const href = await link.getAttribute('href')
  assert.match(href ?? '', /^\/works\/versions\/[^/]+$/)
  const versionId = decodeURIComponent(href.split('/').pop())
  const detail = await api(p, `/work-versions/${versionId}`)
  assert.equal(detail.generation.id, generation.id)
  const graph = detail.generation.provenance?.externalGraph
  assert.ok(graph, 'saved work version must keep generation.provenance.externalGraph')
  assert.equal(graph.sourceId, 'wikidata')
  assert.equal(graph.externalEntityId, 'Q16528')
  assert.equal(graph.contentHash, generation.provenance.externalGraph.contentHash)
  assert.ok(Array.isArray(graph.relations) && graph.relations.length >= 2)
  const affects = new Set(graph.mappingDecisions.map((item) => item.affects))
  assert.ok(affects.has('rule-selection') && affects.has('caption'))

  const saved = {
    title,
    href,
    versionId,
    workId: detail.workVersion.workId,
    generationId: detail.generation.id,
    renderJobId: detail.renderJob.id,
    rendererVersion: jobPayload.rendererVersion,
    timesMs: detail.renderJob.timesMs,
    externalGraphStillPresent: true,
    sourceId: graph.sourceId,
    externalEntityId: graph.externalEntityId,
    contentHash: graph.contentHash,
    contentHashMatches: true,
  }
  record('lotus-save-work-version', saved)
  return { ...saved, detail }
}

async function reopenAndReproduce(p, work) {
  await p.goto(work.href)
  await p.reload()
  await p.getByRole('button', { name: '复现此版本', exact: true }).waitFor()
  const evidence = await ensureEvidence(p, 'Q16528')
  const reopenedDetail = await api(p, `/work-versions/${work.versionId}`)
  const graph = reopenedDetail.generation.provenance?.externalGraph
  assert.ok(graph, 'reopened work version must still expose externalGraph')
  assert.equal(graph.sourceId, 'wikidata')
  assert.equal(graph.externalEntityId, 'Q16528')
  assert.equal(graph.contentHash, work.contentHash)
  await p.screenshot({ path: path.join(out, 'lotus-work-reopened.png'), fullPage: true })

  const button = p.getByRole('button', { name: '复现此版本', exact: true })
  assert.equal(await button.isDisabled(), false, 'reproduction button must be enabled')
  const comparisonPromise = p.waitForResponse(
    (r) => new URL(r.url()).pathname.endsWith('/comparison') && r.request().method() === 'GET',
    { timeout: 180000 },
  )
  await button.click()
  const comparisonResponse = await comparisonPromise
  assert.equal(comparisonResponse.status(), 200)
  const comparison = await comparisonResponse.json()
  const section = p.locator('section[aria-labelledby="reproduction-heading"]')
  await section.getByText(/像素一致|检测到像素差异|像素比较未返回结果|当前条件无法进行像素比较/)
    .waitFor({ timeout: 120000 })
  const uiText = await section.innerText()
  const metrics = uiText.match(/平均绝对误差 ([\d.]+)% · 不同像素比例 ([\d.]+)%/)
  assert.ok(metrics, 'reproduction comparison metrics missing from UI')
  assert.equal(comparison.originalJobId, work.renderJobId)
  assert.ok(comparison.jobId)

  const exactMatch = comparison.exactMatch === true
  if (!comparison.compatible || !exactMatch) {
    fail('lotus-reproduction', `server comparison: compatible=${comparison.compatible}, exactMatch=${comparison.exactMatch}, mae=${comparison.mae}, differentPixelRatio=${comparison.differentPixelRatio}`)
  }
  const reproduction = {
    versionId: work.versionId,
    originalJobId: work.renderJobId,
    reproductionJobId: comparison.jobId,
    compatible: comparison.compatible,
    exactMatch: comparison.exactMatch,
    mae: comparison.mae,
    maePercent: comparison.mae == null ? null : Number((comparison.mae * 100).toFixed(4)),
    differentPixelRatio: comparison.differentPixelRatio,
    differentPixelRatioPercent: comparison.differentPixelRatio == null
      ? null : Number((comparison.differentPixelRatio * 100).toFixed(4)),
    uiMetricsPercent: { mae: Number(metrics[1]), differentPixelRatio: Number(metrics[2]) },
    uiText: uiText.slice(0, 800),
    reopenedSnapshotVisible: true,
    reopenedEvidenceExcerpt: evidence.slice(0, 600),
  }
  await p.screenshot({ path: path.join(out, 'lotus-work-reproduction.png'), fullPage: true })
  await section.screenshot({ path: path.join(out, 'lotus-reproduction-comparison.png') })
  record('lotus-reopen-and-reproduce', reproduction)
  return reproduction
}

async function testUiMalformedId(p) {
  await openSemanticGraph(p)
  let posts = 0
  const count = (request) => {
    if (new URL(request.url()).pathname === '/api/v1/knowledge-graph/generations'
      && request.method() === 'POST') posts++
  }
  p.on('request', count)
  await p.getByLabel('外部实体 ID', { exact: true }).fill('Q 16528')
  await p.getByRole('button', { name: '从外部图谱生成', exact: true }).click()
  await p.getByText(/外部实体 ID 只能使用字母、数字、下划线和短横线/).waitFor()
  await p.waitForTimeout(300)
  p.off('request', count)
  assert.equal(posts, 0, 'malformed externalEntityId must be blocked before any network request')
  record('error-malformed-id-ui-prevalidation', {
    input: 'Q 16528',
    blockedBeforeRequest: true,
    uiMessage: '外部实体 ID 只能使用字母、数字、下划线和短横线，例如 Q16528。',
  })
}

async function testUiUnknownSource(p) {
  await p.getByLabel('来源键', { exact: true }).fill('cypher')
  await p.getByLabel('外部实体 ID', { exact: true }).fill('Q16528')
  const responsePromise = p.waitForResponse(
    (r) => new URL(r.url()).pathname === '/api/v1/knowledge-graph/generations' && r.request().method() === 'POST',
    { timeout: 60000 },
  )
  await p.getByRole('button', { name: '从外部图谱生成', exact: true }).click()
  const response = await responsePromise
  const status = response.status()
  const body = await response.json()
  await p.getByText(/HTTP 503 · KNOWLEDGE_GRAPH_UNAVAILABLE/).waitFor()
  assert.equal(status, 503)
  assert.equal(body?.error?.code, 'KNOWLEDGE_GRAPH_UNAVAILABLE')
  await p.screenshot({ path: path.join(out, 'error-unknown-source-ui.png'), fullPage: true })
  record('error-unknown-source-ui', {
    requestedSourceId: 'cypher',
    httpStatus: status,
    code: body.error.code,
    message: body.error.message,
    available: body.error.details?.available ?? null,
    uiVisible: true,
  })
}

async function testDirectApiErrors(p) {
  const unknownSource = await rawApi(p, graphEndpoint, 'POST', {
    sourceId: 'not-configured', externalEntityId: 'Q16528', backend: 'canvas2d', seed: 42,
  })
  assert.equal(unknownSource.status, 503, redact(unknownSource.body))
  assert.equal(unknownSource.body?.error?.code, 'KNOWLEDGE_GRAPH_UNAVAILABLE')
  record('error-unknown-source-api', {
    requestedSourceId: 'not-configured',
    httpStatus: unknownSource.status,
    code: unknownSource.body.error.code,
    message: unknownSource.body.error.message,
    available: unknownSource.body.error.details?.available ?? null,
    generationReturned: false,
  })

  const malformed = await rawApi(p, graphEndpoint, 'POST', {
    sourceId: 'wikidata', externalEntityId: 'Q 16528', backend: 'canvas2d', seed: 42,
  })
  assert.equal(malformed.status, 422, redact(malformed.body))
  assert.equal(malformed.body?.error?.code, 'VALIDATION_ERROR')
  const field = (malformed.body.error.fieldErrors ?? []).find((item) => item.path === 'externalEntityId')
  assert.ok(field, 'externalEntityId field error must be returned')
  record('error-malformed-id-api', {
    requestedExternalEntityId: 'Q 16528',
    httpStatus: malformed.status,
    code: malformed.body.error.code,
    fieldErrors: malformed.body.error.fieldErrors,
    generationReturned: false,
  })

  const unmapped = await rawApi(p, graphEndpoint, 'POST', {
    sourceId: 'wikidata', externalEntityId: 'Q42', backend: 'canvas2d', seed: 42,
  })
  const code = unmapped.body?.error?.code
  record('error-unmapped-entity-api', {
    requestedExternalEntityId: 'Q42',
    httpStatus: unmapped.status,
    code,
    message: unmapped.body?.error?.message,
    details: unmapped.body?.error?.details ?? null,
    generationReturned: false,
  })
  if (unmapped.status !== 422) {
    fail('error-unmapped-entity-api', `expected 422 for unmapped entity Q42, got ${unmapped.status} ${code}`)
  } else if (!['NO_SEMANTIC_MATCH', 'KNOWLEDGE_GRAPH_ENTITY_NOT_FOUND'].includes(code)) {
    fail('error-unmapped-entity-api', `expected NO_SEMANTIC_MATCH or KNOWLEDGE_GRAPH_ENTITY_NOT_FOUND, got ${code}`)
  }
}

try {
  page = await actor('creator_demo')
  const caps = await api(page, '/engine/capabilities')
  assert.equal(caps.features?.knowledgeGraph, true, 'features.knowledgeGraph must be enabled')
  const canvasCapability = (caps.backends ?? caps.capabilities ?? []).find((item) => item.backend === 'canvas2d')
  record('capabilities-knowledge-graph-enabled', {
    profile: caps.profile,
    knowledgeGraph: caps.features.knowledgeGraph,
    canvas2dRendererVersion: canvasCapability?.rendererVersion ?? null,
    canvas2dAvailable: canvasCapability?.available ?? null,
  })

  // Negative paths do not depend on the demo knowledge seed, so run them first:
  // a missing seed must not hide the error-contract evidence.
  await testUiMalformedId(page)
  await testUiUnknownSource(page)
  await testDirectApiErrors(page)

  const lotus = await generateGraphEntity(page, entities[0])
  const work = await saveCurrentWork(page, lotus.generation, `K1-莲-${Date.now()}`)
  const reproduction = await reopenAndReproduce(page, work)
  result.work = {
    title: work.title,
    href: work.href,
    versionId: work.versionId,
    workId: work.workId,
    generationId: work.generationId,
    renderJobId: work.renderJobId,
    rendererVersion: work.rendererVersion,
    timesMs: work.timesMs,
    externalGraphStillPresent: true,
    reopenedSnapshotVisible: true,
    reproduction,
  }

  await page.goto('/')
  await openSemanticGraph(page)
  await generateGraphEntity(page, entities[1])
  await generateGraphEntity(page, entities[2])

  assert.equal(result.entities.length, 3)
  assert.deepEqual(new Set(result.entities.map((item) => item.externalEntityId)),
    new Set(['Q16528', 'Q670887', 'Q157763']))
  assert.deepEqual(result.pageErrors, [])
  if (result.failures.length > 0) throw new Error(`${result.failures.length} acceptance failure(s)`)
} catch (error) {
  result.status = 'failed'
  result.error = redact(String(error))
  if (page) {
    try {
      await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true })
    } catch {
      // A page that is already closed still leaves the failure in result.json.
    }
  }
  throw error
} finally {
  if (result.status === 'running') {
    result.status = result.failures.length || result.pageErrors.length ? 'failed' : 'passed'
  }
  result.finishedAt = new Date().toISOString()
  await writeFile(path.join(out, 'result.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify({
    status: result.status,
    entities: result.entities.map((item) => ({
      id: item.externalEntityId, label: item.entityLabel, relations: item.relationCount,
    })),
    workVersionId: result.work?.versionId ?? null,
    exactMatch: result.work?.reproduction?.exactMatch ?? null,
    failures: result.failures.length,
    pageErrors: result.pageErrors,
  }))
  await browser.close()
}
