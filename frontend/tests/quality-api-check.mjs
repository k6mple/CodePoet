// Real isolated production UI/API; labels are assertions, never server responses.
import assert from 'node:assert/strict'
import {createHash} from 'node:crypto'
import {mkdir,readFile,writeFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import path from 'node:path'
const {chromium}=createRequire(import.meta.url)('playwright')
const base=process.env.CODEPOET_Q1_BASE_URL,password=process.env.CODEPOET_ACCEPTANCE_PASSWORD,output=process.env.CODEPOET_Q1_OUTPUT_DIR
if(!base||!password||!output||!['localhost','127.0.0.1'].includes(new URL(base).hostname))throw new Error('Requires isolated localhost, environment credential and local output directory')
const out=path.resolve(output);await mkdir(out,{recursive:true})
await mkdir(path.join(out,'reports'),{recursive:true})
const dataset=JSON.parse(await readFile(new URL('../../shared/demo/quality/cases.json',import.meta.url),'utf8'))
const result={status:'running',scope:'real-production-UI-and-HTTP-upload',cases:[],checks:[],componentCompatibility:[],pageErrors:[],falsePositives:0,falseNegatives:0}
const browser=await chromium.launch({channel:'chrome',headless:true})
let page
async function api(endpoint,method='GET',body,expected=200){
 const r=await page.evaluate(async({endpoint,method,body})=>{
  const headers={}
  if(method!=='GET'){headers['X-CSRF-Token']=(await(await fetch('/api/v1/me')).json()).csrfToken;headers['Content-Type']='application/json'}
  const response=await fetch('/api/v1'+endpoint,{method,headers,...(body===undefined?{}:{body:JSON.stringify(body)})})
  return{status:response.status,body:await response.json()}
 },{endpoint,method,body})
 assert.equal(r.status,expected,JSON.stringify(r.body).replaceAll(password,'[redacted]'));return r.body
}
async function renderAndSave(sample,stage,baseGeneration,workId){
 const ir=stage==='before'?sample.ir:sample.afterIR
 const g=await api('/generations','POST',{mode:'ir',baseGenerationId:baseGeneration.id,ir,backend:'canvas2d'},201)
 const times=ir.durationMs?[0,ir.durationMs/2,ir.durationMs]:[0]
 let job=await api('/render-jobs','POST',{generationId:g.id,backend:'canvas2d',executionMode:'browser_upload',timesMs:times,
  rendererVersion:'codepoet-canvas-1.0.0',clientEnvironment:{userAgent:await page.evaluate(()=>navigator.userAgent),deviceScaleFactor:1}},202)
 for(const time of times){
  const bytes=[...await readFile(new URL(`../../shared/demo/quality/png/${sample.id}-${stage}-${time}.png`,import.meta.url))]
  const uploaded=await page.evaluate(async({jobId,time,hash,bytes})=>{
   const csrf=(await(await fetch('/api/v1/me')).json()).csrfToken
   const body=new FormData();body.set('timeMs',String(time));body.set('irHash',hash)
   body.set('file',new Blob([new Uint8Array(bytes)],{type:'image/png'}),'frame.png')
   const r=await fetch(`/api/v1/render-jobs/${jobId}/frames`,{method:'POST',headers:{'X-CSRF-Token':csrf},body})
   return{status:r.status,body:await r.json()}
  },{jobId:job.id,time,hash:g.irHash,bytes})
  assert.equal(uploaded.status,200,JSON.stringify(uploaded.body));job=uploaded.body
 }
 assert.equal(job.status,'succeeded')
 await writeFile(path.join(out,'reports',`${sample.id}-${stage}.json`),JSON.stringify({generationId:g.id,jobId:job.id,irHash:g.irHash,qualityReport:job.qualityReport},null,2)+'\n')
 const assessment=job.qualityReport.assessment
 const states=Object.fromEntries(assessment.metrics.map(metric=>[metric.name,metric.status]))
 const expected=stage==='before'?sample.expected:Object.fromEntries(Object.keys(sample.expected).map(name=>[name,'pass']))
 const match=JSON.stringify(states)===JSON.stringify(expected)
 if(!match){
  const found=Object.values(states).some(status=>status==='warn'||status==='fail')
  if(sample.bad&&!found)result.falseNegatives++;if(!sample.bad&&found)result.falsePositives++
 }
 assert.deepEqual(states,expected,sample.id+' '+stage)
 assert.ok(assessment.metrics.every(metric=>metric.score===null))
 assert.equal(assessment.algorithmVersion,'codepoet-quality-1.0.0')
 for(const frame of job.manifest.frames){
  const r=await page.request.get(`/api/v1/artifacts/${frame.artifactId}`)
  assert.equal(createHash('sha256').update(await r.body()).digest('hex'),frame.sha256)
 }
 const revision=workId?(await api(`/works/${workId}`)).revision:null
 const saved=await api(workId?`/works/${workId}/versions`:'/works','POST',{
  generationId:g.id,renderJobId:job.id,...(workId?{expectedRevision:revision,changeNote:'Q1 corrected calibration sample'}:{title:`Q1-${sample.id}-${Date.now()}`})},201)
 const detail=await api(`/work-versions/${saved.version.id}`)
 assert.deepEqual(detail.renderJob.qualityReport,job.qualityReport)
 return {sample,stage,g,job,detail,assessment,states}
}
try{
 const context=await browser.newContext({baseURL:base,viewport:{width:1440,height:1000}});page=await context.newPage()
 page.setDefaultTimeout(20000)
 page.on('pageerror',error=>result.pageErrors.push(error.message.replaceAll(password,'[redacted]')))
 await page.goto('/')
 await page.getByRole('button',{name:'登录',exact:true}).first().click()
 await page.locator('#login-username').fill('creator_demo');await page.locator('#login-password').fill(password)
 await page.locator('dialog button[type=submit]').click();await page.locator('dialog[open]').waitFor({state:'hidden'})
 await page.getByText('已应用',{exact:true}).waitFor()
 await page.getByText('尚未像素评估。保存并收齐真实 PNG 帧后，由服务器返回报告。',{exact:true}).waitFor()
 result.checks.push('generation-validation-separated-from-not-yet-evaluated-pixels')
 const semantic=await api('/generations','POST',{mode:'semantic',input:{text:'莲花清正',entityIds:[]},backend:'canvas2d',seed:42},201)
 assert.ok(semantic.provenance.paletteDecision.value.includes('#3B6D11'))
 let midpoint
 for(const sample of dataset.cases){
  const before=await renderAndSave(sample,'before',semantic)
  result.cases.push({id:sample.id,bad:sample.bad,expected:sample.expected,actual:before.states,workVersionId:before.detail.workVersion.id})
  if(sample.afterIR){
   const after=await renderAndSave(sample,'after',semantic,before.detail.workVersion.workId)
   assert.notEqual(before.g.id,after.g.id)
   assert.notEqual(before.job.id,after.job.id)
   assert.deepEqual((await api(`/work-versions/${before.detail.workVersion.id}`)).renderJob.qualityReport,before.job.qualityReport)
   result.cases.at(-1).after={actual:after.states,workVersionId:after.detail.workVersion.id}
  }
  if(sample.id==='bad-midpoint-overlap')midpoint=before
 }
 result.checks.push('12-real-upload-labels-and-6-improvements-with-immutable-history')
 await page.goto(`/works/versions/${midpoint.detail.workVersion.id}`);await page.reload()
 await page.getByText('codepoet-quality-1.0.0',{exact:true}).waitFor()
 const recommendation=page.locator('.quality-recommendations li').filter({hasText:'2000ms'}).first()
 await recommendation.getByRole('button',{name:'定位问题',exact:true}).click()
 assert.equal(await page.getByRole('slider',{name:'时间轴',exact:true}).inputValue(),'2000')
 await page.getByRole('img',{name:'质量问题定位',exact:true}).waitFor()
 await page.locator('.quality-evidence').screenshot({path:path.join(out,'quality-panel.png')})
 await page.screenshot({path:path.join(out,'historical-quality-focus.png'),fullPage:true})
 await page.setViewportSize({width:390,height:844})
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'mobile horizontal overflow')
 await page.locator('.quality-evidence').screenshot({path:path.join(out,'quality-panel-mobile.png')})
 await page.setViewportSize({width:1440,height:1000})
 let generationWrites=0
 page.on('request',request=>{if(new URL(request.url()).pathname==='/api/v1/generations'&&request.method()==='POST')generationWrites++})
 await recommendation.getByRole('button',{name:'准备修改',exact:true}).click()
 await page.getByRole('button',{name:'取消修改',exact:true}).click()
 assert.equal(generationWrites,0)
 await recommendation.getByRole('button',{name:'准备修改',exact:true}).click()
 await page.getByRole('button',{name:'确认并打开编辑',exact:true}).click()
 await page.getByRole('textbox',{name:'完整 IR JSON',exact:true}).waitFor()
 assert.equal(generationWrites,0,'confirmation opens editor without creating a generation')
 assert.equal(await page.getByRole('slider',{name:'时间轴',exact:true}).inputValue(),'2000')
 await page.getByRole('textbox',{name:'完整 IR JSON',exact:true}).fill(JSON.stringify(midpoint.sample.afterIR))
 await page.getByRole('button',{name:'校验并应用 IR',exact:true}).click()
 await page.waitForFunction(old=>[...document.querySelectorAll('dt')].find(e=>e.textContent==='生成编号')?.nextElementSibling?.textContent!==old,midpoint.g.id)
 await page.getByText('尚未像素评估。保存并收齐真实 PNG 帧后，由服务器返回报告。',{exact:true}).waitFor()
 await page.getByRole('button',{name:'保存为新版本',exact:true}).click()
 const savedLink=page.getByRole('link',{name:'打开已保存的作品',exact:true});await savedLink.waitFor()
 const href=await savedLink.getAttribute('href')
 const improved=await api(`/work-versions/${href.split('/').pop()}`)
 assert.equal(improved.renderJob.qualityReport.assessment.metrics.find(m=>m.name==='layoutConflict').status,'pass')
 assert.notEqual(improved.generation.id,midpoint.g.id)
 for(const frame of improved.renderJob.manifest.frames){
  const expected=await readFile(new URL(`../../shared/demo/quality/png/bad-midpoint-overlap-after-${frame.timeMs}.png`,import.meta.url))
  assert.equal(frame.sha256,createHash('sha256').update(expected).digest('hex'),'focus overlay must not enter new version PNG')
 }
 assert.equal(generationWrites,1)
 assert.deepEqual((await api(`/work-versions/${midpoint.detail.workVersion.id}`)).renderJob.qualityReport,midpoint.job.qualityReport)
 await page.goto(href)
 await page.screenshot({path:path.join(out,'improved-version.png'),fullPage:true})
 result.checks.push('historical-2000ms-node-focus-confirm-edit-generate-save-new-version')
 result.checks.push('focus-overlay-does-not-change-renderer-PNG-or-old-history')
 // The following two checks adapt HTTP responses solely to test old/failure UI.
 // They do not count as real backend algorithm/failure acceptance.
 const endpoint=`**/api/v1/work-versions/${improved.workVersion.id}`
 for(const mode of ['legacy','unavailable']){
  await page.route(endpoint,async route=>{
   const response=await route.fetch(),data=await response.json()
   if(mode==='legacy')delete data.renderJob.qualityReport.assessment
   else{
    data.renderJob.qualityReport.assessment.status='unavailable'
    for(const metric of data.renderJob.qualityReport.assessment.metrics){metric.status='unavailable';metric.value=null;metric.score=null;metric.evidence=[]}
    data.renderJob.qualityReport.assessment.recommendations=[]
   }
   await route.fulfill({response,json:data})
  })
  await page.goto(href)
  if(mode==='legacy')await page.getByText('此报告只有基础像素检查；四项版本化质量指标尚未评估。',{exact:true}).waitFor()
  else{await page.locator('.quality-metric[data-status="unavailable"]').first().waitFor();assert.equal(await page.locator('.quality-metric[data-status="unavailable"]').count(),4)}
  await page.locator('img').first().waitFor()
  await page.unroute(endpoint)
  result.componentCompatibility.push({mode,scope:'adapted-response-component-test-only',passed:true})
 }
 assert.deepEqual(result.pageErrors,[])
 result.status='passed'
}catch(error){
 result.status='failed';result.error=String(error).replaceAll(password,'[redacted]')
 if(page && await page.locator('input[type=password]:visible').count()===0)await page.screenshot({path:path.join(out,'failure.png'),fullPage:true})
 throw error
}finally{
 await writeFile(path.join(out,'result.json'),JSON.stringify(result,null,2)+'\n')
 console.log(JSON.stringify({status:result.status,cases:result.cases.length,checks:result.checks.length,falsePositives:result.falsePositives,falseNegatives:result.falseNegatives,pageErrors:result.pageErrors}))
 await browser.close()
}
