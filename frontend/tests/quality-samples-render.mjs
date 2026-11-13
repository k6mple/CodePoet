// Actual Chrome renderer output. This prepares labelled PNG data, not API reports.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { createRequire } from 'node:module'
import path from 'node:path'
import { browserModule } from './browser-modules.mjs'
const {chromium}=createRequire(import.meta.url)('playwright')
if(!process.argv[2])throw new Error('Pass a local output directory')
const out=path.resolve(process.argv[2]);await mkdir(out,{recursive:true})
const dataset=JSON.parse(await readFile(new URL('../../shared/demo/quality/cases.json',import.meta.url),'utf8'))
const exporter=await browserModule(new URL('../src/renderers/export.ts',import.meta.url))
const browser=await chromium.launch({channel:'chrome',headless:true})
const result={scope:'real-Chrome-calibration-PNG-not-API-acceptance',rendererVersion:'codepoet-canvas-1.0.0',frames:[]}
try{
 const page=await browser.newPage();await page.goto('about:blank')
 for(const sample of dataset.cases)for(const stage of ['before',...(sample.afterIR?['after']:[])]){
  const ir=stage==='before'?sample.ir:sample.afterIR
  const times=ir.durationMs?[0,Math.floor(ir.durationMs/2),ir.durationMs]:[0]
  for(const time of times){
   const bytes=await page.evaluate(async({exporter,ir,time,mutation})=>{
    const {exportBackendFrame}=(0,eval)(exporter)
    let blob=await exportBackendFrame('canvas2d',ir,time)
    if(mutation!=='clean'){
     const bitmap=await createImageBitmap(blob)
     const canvas=Object.assign(document.createElement('canvas'),{width:bitmap.width,height:bitmap.height})
     const ctx=canvas.getContext('2d');ctx.drawImage(bitmap,0,0);bitmap.close()
     if(mutation==='blank'){ctx.fillStyle=ir.canvas.background;ctx.fillRect(0,0,canvas.width,canvas.height)}
     if(mutation==='noise'){
      ctx.fillStyle='#000000'
      for(let index=0;index<1500;index++)ctx.fillRect(100+(index%50)*12,480+Math.floor(index/50)*3,2,2)
     }
     blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'))
    }
    return [...new Uint8Array(await blob.arrayBuffer())]
   },{exporter,ir,time,mutation:stage==='before'?sample.pngMutation:'clean'})
   const buffer=Buffer.from(bytes),file=`${sample.id}-${stage}-${time}.png`
   assert.equal(buffer.subarray(1,4).toString(),'PNG')
   await writeFile(path.join(out,file),buffer)
   result.frames.push({caseId:sample.id,stage,timeMs:time,file,sha256:createHash('sha256').update(buffer).digest('hex')})
  }
 }
 await writeFile(path.join(out,'manifest.json'),JSON.stringify(result,null,2)+'\n')
 console.log(JSON.stringify({cases:dataset.cases.length,bad:dataset.cases.filter(s=>s.bad).length,frames:result.frames.length,scope:result.scope}))
}finally{await browser.close()}
