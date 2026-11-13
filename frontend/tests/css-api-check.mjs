// Real isolated API + production UI acceptance. Secrets are environment-only.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { browserModule } from './browser-modules.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const base = process.env.CODEPOET_CSS_BASE_URL
const password = process.env.CODEPOET_ACCEPTANCE_PASSWORD
const output = process.env.CODEPOET_CSS_OUTPUT_DIR
if (!base || !['localhost','127.0.0.1'].includes(new URL(base).hostname) || !password || !output) throw new Error('Requires isolated localhost deployment, seed password and local output directory')
const out = path.resolve(output)
await mkdir(out, { recursive:true })
const exporter = await browserModule(new URL('../src/renderers/export.ts', import.meta.url))
const result = { status:'running', scope:'real-api-production-ui', checks:[], pageErrors:[] }
const record = (name, details={}) => result.checks.push({name,...details})
const browser = await chromium.launch({channel:'chrome',headless:true})
let page
async function actor(username) {
  const context = await browser.newContext({baseURL:base,viewport:{width:1440,height:1000}})
  const p = await context.newPage()
  p.on('pageerror', e => result.pageErrors.push(e.message.replaceAll(password,'[redacted]')))
  await p.goto('/')
  await p.getByRole('button',{name:'登录',exact:true}).first().click()
  await p.locator('#login-username').fill(username)
  await p.locator('#login-password').fill(password)
  await p.locator('dialog button[type=submit]').click()
  await p.locator('dialog[open]').waitFor({state:'hidden'})
  return p
}
async function api(p, endpoint, method='GET', body, expected=200) {
  const response = await p.evaluate(async ({endpoint,method,body}) => {
    const headers = {}
    if (method !== 'GET') {
      headers['X-CSRF-Token']=(await (await fetch('/api/v1/me')).json()).csrfToken
      headers['Content-Type']='application/json'
    }
    const r=await fetch('/api/v1'+endpoint,{method,headers,...(body===undefined?{}:{body:JSON.stringify(body)})})
    return {status:r.status,body:r.status===204?null:await r.json()}
  }, {endpoint,method,body})
  assert.equal(response.status,expected,JSON.stringify(response.body).replaceAll(password,'[redacted]'))
  return response.body
}
async function applied() { await page.getByText('已应用',{exact:true}).waitFor() }
async function generation() {
  const id=await page.getByText('生成编号',{exact:true}).locator('..').locator('dd').textContent()
  return api(page,`/generations/${id}`)
}
async function waitGeneration(backend, priorId) {
  await page.waitForFunction(({backend,priorId}) => {
    const svg=document.querySelector('.canvas-stage svg, .stage svg, svg[aria-label="CSS 作品预览"]')
    const id=[...document.querySelectorAll('dt')].find(e=>e.textContent==='生成编号')?.nextElementSibling?.textContent
    return id && id!==priorId && (backend!=='css'||svg)
  }, {backend,priorId})
  await page.waitForFunction(() => !document.querySelector('.archive-actions button.button--primary')?.disabled)
  const g=await generation(); assert.equal(g.backend,backend); return g
}
async function saveAndReproduce(label,times) {
  const g=await generation()
  let createdCount=0
  const count=request=>{ if(new URL(request.url()).pathname==='/api/v1/render-jobs'&&request.method()==='POST')createdCount++ }
  page.on('request',count)
  if(label==='static') {
    await page.route('**/api/v1/render-jobs/*/frames',async route=>{
      await route.abort('failed')
      await page.unroute('**/api/v1/render-jobs/*/frames')
    })
  }
  await page.getByRole('textbox',{name:'作品名称',exact:true}).fill(`CSS-${label}-${Date.now()}`)
  const created=page.waitForResponse(r=>r.url().endsWith('/api/v1/render-jobs')&&r.request().method()==='POST')
  await page.locator('.archive-actions button.button--primary').first().click()
  const response=await created
  assert.equal(response.status(),202)
  assert.equal(response.request().postDataJSON().backend,'css')
  assert.equal(response.request().postDataJSON().rendererVersion,'codepoet-css-1.0.0')
  assert.equal(await page.getByRole('combobox',{name:'渲染后端',exact:true}).isDisabled(),true)
  if(label==='static') {
    await page.getByText('保存未确认',{exact:true}).waitFor()
    await page.getByRole('button',{name:'重试原任务并保存',exact:true}).click()
  }
  const link=page.getByRole('link',{name:'打开已保存的作品',exact:true})
  await link.waitFor()
  assert.equal(createdCount,1,'upload retry must GET and reuse original job')
  page.off('request',count)
  const href=await link.getAttribute('href')
  let detail=await api(page,`/work-versions/${href.split('/').pop()}`)
  assert.equal(detail.generation.id,g.id)
  assert.equal(detail.renderJob.backend,'css')
  assert.deepEqual(detail.renderJob.timesMs,times)
  assert.equal(detail.renderJob.manifest.rendererVersion,'codepoet-css-1.0.0')
  for (const frame of detail.renderJob.manifest.frames) {
    const download=await page.request.get(`/api/v1/artifacts/${frame.artifactId}`)
    assert.equal(download.status(),200)
    const bytes=await download.body()
    assert.equal(createHash('sha256').update(bytes).digest('hex'),frame.sha256)
    const rendered=await page.evaluate(async ({exporter,ir,time}) => {
      const {exportBackendFrame}=(0,eval)(exporter)
      return [...new Uint8Array(await (await exportBackendFrame('css',ir,time)).arrayBuffer())]
    }, {exporter,ir:g.ir,time:frame.timeMs})
    assert.equal(createHash('sha256').update(Buffer.from(rendered)).digest('hex'),frame.sha256)
    await writeFile(path.join(out,`${label}-${frame.timeMs}.png`),bytes)
  }
  await page.goto(href); await page.reload()
  await page.locator('svg[aria-label="CSS 作品预览"]').first().waitFor()
  await page.getByRole('button',{name:'复现此版本',exact:true}).click()
  await page.getByText('像素一致',{exact:true}).waitFor()
  detail=await api(page,`/work-versions/${detail.workVersion.id}`)
  record(`${label}-save-reopen-reproduce`,{versionId:detail.workVersion.id,jobId:detail.renderJob.id,times,hashesVerified:true,pixelsExact:true})
  await page.screenshot({path:path.join(out,`${label}-reproduction.png`),fullPage:true})
  return detail
}
try {
  page=await actor('creator_demo')
  const expert=await actor('expert_demo')
  const admin=await actor('admin_demo')
  const original=await api(admin,'/templates/tpl-lotus')
  const historical=await api(admin,`/template-versions/${original.publishedVersionId}`)
  const cssDraft=await api(admin,'/templates/tpl-lotus/versions','POST',{
    fromVersionId:historical.id,supportedBackends:['css'],changeNote:'CSS-only private draft regression',
  },201)
  const creatorPage=page
  page=admin
  for(const suffix of ['', '&backend=css']) {
    await page.goto(`/?draft=${cssDraft.id}${suffix}`)
    await applied()
    const restored=await generation()
    assert.equal(restored.backend,'css')
    assert.equal(restored.templateVersionId,cssDraft.id)
  }
  record('css-only-draft-new-and-legacy-entry',{versionId:cssDraft.id})
  page=creatorPage
  const clone=await api(admin,'/templates/tpl-lotus/versions','POST',{
    fromVersionId:historical.id,supportedBackends:['canvas2d','css'],changeNote:'S0-A2 isolated CSS acceptance',
  },201)
  const submitted=await api(admin,`/template-versions/${clone.id}/submit`,'POST',{expectedRevision:clone.revision})
  await api(expert,'/reviews','POST',{targetType:'template_version',targetId:clone.id,expectedRevision:submitted.revision,decision:'approve',reason:'Isolated CSS acceptance'},201)
  assert.deepEqual((await api(admin,`/template-versions/${historical.id}`)).supportedBackends,historical.supportedBackends)
  assert.ok((await api(page,'/templates?backend=css')).items.some(item=>item.id==='tpl-lotus'))
  record('clone-review-publish-without-rewriting-old-version',{oldVersionId:historical.id,newVersionId:clone.id})
  await page.goto('/?template=tpl-lotus&backend=css'); await applied()
  const initial=await generation(); assert.equal(initial.backend,'css')
  await page.locator('svg[aria-label="CSS 作品预览"]').waitFor()
  await page.locator('#param-centerSize').fill('50'); await applied()
  assert.notEqual((await generation()).irHash,initial.irHash)
  await saveAndReproduce('static',[0])
  await page.goto('/?backend=css&template=tpl-lotus'); await applied()
  await page.getByRole('button',{name:'JSON IR',exact:true}).click()
  const g=await generation()
  const ir=structuredClone(g.ir)
  ir.durationMs=4000
  const target=ir.nodes.find(n=>n.type!=='group')
  ir.animations=[{nodeId:target.id,property:'rotation',from:0,to:90,startMs:0,endMs:4000,easing:'linear'},
    {nodeId:target.id,property:'opacity',from:0.2,to:1,startMs:0,endMs:4000,easing:'linear'}]
  await page.getByRole('textbox',{name:'完整 IR JSON',exact:true}).fill(JSON.stringify(ir))
  await page.getByRole('button',{name:'校验并应用 IR',exact:true}).click()
  await waitGeneration('css',g.id)
  for (const time of [0,2000,4000]) {
    const slider=page.getByRole('slider')
    await slider.fill(String(time))
    await page.waitForFunction(time=>document.querySelector('svg[aria-label="CSS 作品预览"]')?.dataset.timeMs===String(time),time)
  }
  await saveAndReproduce('animated',[0,2000,4000])
  await page.goto('/?backend=css&template=tpl-lotus'); await applied()
  const sessionResponse=page.waitForResponse(r=>r.url().endsWith('/api/v1/sessions')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'保存创作进度',exact:true}).click()
  const session=await (await sessionResponse).json()
  assert.equal(session.state.backend,'css')
  await page.goto(`/?session=${session.id}`); await applied()
  assert.equal(await page.getByRole('combobox',{name:'渲染后端',exact:true}).inputValue(),'css')
  assert.equal((await generation()).id,session.state.generationId)
  record('CSS-session-restores-backend-and-generation',{sessionId:session.id})
  await page.getByRole('button',{name:'语义生成',exact:true}).click()
  await page.getByRole('textbox',{name:'典故或创作描述',exact:true}).fill('莲花清正')
  await page.getByRole('button',{name:'按语义生成',exact:true}).click()
  const semantic=await waitGeneration('css',session.state.generationId)
  await page.getByRole('combobox',{name:'渲染后端',exact:true}).selectOption('canvas2d')
  const canvas=await waitGeneration('canvas2d',semantic.id)
  assert.equal((await api(page,`/generations/${semantic.id}`)).backend,'css')
  await page.getByRole('combobox',{name:'渲染后端',exact:true}).selectOption('css')
  await waitGeneration('css',canvas.id)
  record('semantic-backend-switch-creates-new-immutable-generation')
  let release, reached
  const held=new Promise(resolve=>{release=resolve})
  const fetched=new Promise(resolve=>{reached=resolve})
  let delayed=false
  await page.route('**/api/v1/generations',async route=>{
    const payload=route.request().postDataJSON()
    if(!delayed && payload.backend==='css' && payload.mode==='semantic') {
      delayed=true
      const real=await route.fetch();reached()
      await held
      await route.fulfill({response:real}).catch(()=>{})
    } else await route.continue()
  })
  const beforeRace=await generation()
  await page.getByRole('textbox',{name:'典故或创作描述',exact:true}).fill('竹节守正')
  await page.getByRole('button',{name:'按语义生成',exact:true}).click()
  await fetched
  await page.getByRole('combobox',{name:'渲染后端',exact:true}).selectOption('canvas2d')
  const newest=await waitGeneration('canvas2d',beforeRace.id)
  release()
  await page.unroute('**/api/v1/generations')
  await page.waitForTimeout(150)
  assert.equal((await generation()).id,newest.id)
  record('late-real-generation-response-cannot-overwrite-new-backend')
  await page.getByRole('button',{name:'JSON IR',exact:true}).click()
  await page.getByRole('combobox',{name:'渲染后端',exact:true}).selectOption('css')
  const switchedIR=await waitGeneration('css',newest.id)
  assert.equal(switchedIR.mode,'ir')
  assert.equal((await api(page,`/generations/${newest.id}`)).backend,'canvas2d')
  record('IR-backend-switch-creates-new-generation')
  assert.deepEqual(result.pageErrors,[])
  result.status='passed'
} catch (error) {
  result.status='failed'; result.error=String(error).replaceAll(password,'[redacted]')
  if (page && await page.locator('input[type=password]:visible').count()===0) await page.screenshot({path:path.join(out,'failure.png'),fullPage:true})
  throw error
} finally {
  await writeFile(path.join(out,'result.json'),JSON.stringify(result,null,2))
  console.log(JSON.stringify({status:result.status,checks:result.checks.length,pageErrors:result.pageErrors}))
  await browser.close()
}
