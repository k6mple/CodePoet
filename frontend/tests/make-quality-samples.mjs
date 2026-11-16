// Deterministic, manually labelled calibration examples, not API responses.
import { mkdir, writeFile } from 'node:fs/promises'
const green='#3B6D11'
const base=()=>({schemaVersion:'1.0',seed:42,canvas:{width:800,height:600,background:'#FFFFFF'},
  viewport:{x:0,y:0,width:800,height:600},durationMs:0,nodes:[],animations:[],narrative:[]})
const circle=(id,cx,cy,r=60,extra={})=>({id,parentId:null,type:'circle',geometry:{cx,cy,r},
  style:{fill:green,stroke:'#151515',strokeWidth:2,opacity:1},...extra})
const scene=(nodes,extra={})=>({...base(),nodes,...extra})
const copy=value=>structuredClone(value)
const cases=[]
function add(id,label,bad,ir,expected,reason,extra={}) {
  cases.push({id,label,bad,ir,pngMutation:'clean',palette:['#D4537E','#EF9F27',green,'#F7F6F0'],
    expected:{parameterValidity:'pass',layoutConflict:'pass',colorSemantic:'pass',signalNoise:'pass',...expected},reason,...extra})
}
add('normal-separated','独立主体分离',false,scene([circle('a',200,300),circle('b',600,300)]),{},'两个独立主体没有交叠，填充属于历史色板。')
const group={id:'flower',parentId:null,type:'group',style:{opacity:1}}
add('normal-grouped','同组设计交叠',false,scene([group,circle('petal-a',370,300,70,{parentId:'flower'}),circle('petal-b',430,300,70,{parentId:'flower'})]),{},'同一顶层子树内的设计交叠不计主体冲突；不能据此证明组内绝无缺陷。')
add('normal-transformed-crop','父子变换和裁剪',false,scene([{...group,transform:{x:400,y:300,rotation:35,scaleX:1.3,scaleY:0.8}},circle('leaf',0,0,80,{parentId:'flower'})],
  {viewport:{x:100,y:100,width:600,height:400}}),{},'世界采样后映射viewport；一组旋转缩放图元仍只算一个主体。')
add('normal-thin-stroke','细线和描边排除',false,scene([{id:'line',parentId:null,type:'polyline',geometry:{points:[[100,180],[400,300],[700,180]]},
  style:{fill:null,stroke:'#151515',strokeWidth:0.5,opacity:1}}]),{colorSemantic:'not_applicable'},'无填充可评估，描边不按色板判违规；几何mask至少覆盖一格。')
add('normal-transparent','透明填充',false,scene([circle('a',400,300,80,{style:{fill:green,stroke:null,strokeWidth:0,opacity:0.45}})]),{},'透明填充按当前有效透明度在背景上合成允许色后比较。')
const motion=scene([circle('a',200,260)],{durationMs:4000,animations:[{nodeId:'a',property:'x',from:0,to:400,startMs:0,endMs:4000,easing:'linear'}]})
add('normal-motion','四秒无冲突动画',false,motion,{},'0/2000/4000ms主体都在视口内，没有其他主体产生交叠。')
let ir=scene([circle('a',380,300,90),circle('b',420,300,90)])
let after=copy(ir);after.nodes[0].geometry.cx=220;after.nodes[1].geometry.cx=580
add('bad-overlap','独立主体重叠',true,ir,{layoutConflict:'warn'},'两个独立顶层圆的交叠明显超过0.12；修正为分离。',{afterIR:after})
ir=scene([circle('a',400,300,100)]);ir.nodes[0].style.fill='#0000FF';after=copy(ir);after.nodes[0].style.fill=green
add('bad-palette','历史色板外的蓝色',true,ir,{colorSemantic:'warn'},'蓝色与固定历史色板距离较大；只改填充色，不把深色描边判成违规。',{afterIR:after})
ir=scene([circle('a',400,280,80)])
add('bad-noise','几何外的PNG杂点',true,ir,{signalNoise:'warn'},'上传PNG在预期几何外加入固定杂点；IR本身合法。',{pngMutation:'noise',afterIR:copy(ir)})
add('bad-blank','合法IR但上传空白PNG',true,copy(ir),{signalNoise:'fail'},'实际PNG为空白，即使N=0也必须失败，不能靠SNR高分冒充好图。',{pngMutation:'blank',afterIR:copy(ir),expectedAfter: {signalNoise:'pass'}})
ir=scene([circle('outside',1800,300,80)]);after=copy(ir);after.nodes[0].geometry.cx=400
add('bad-outside','图元完全出界',true,ir,{parameterValidity:'warn',signalNoise:'fail',colorSemantic:'not_applicable'},'出界允许生成但产生警告，PNG无内容；修正节点位置。',{afterIR:after})
ir=scene([circle('moving',160,300,50),circle('fixed',360,300,50)],{durationMs:4000,
  animations:[{nodeId:'moving',property:'x',from:0,to:400,startMs:0,endMs:4000,easing:'linear'}]})
after=copy(ir);after.animations[0]={...after.animations[0],property:'y',to:150}
add('bad-midpoint-overlap','仅中间时刻冲突',true,ir,{layoutConflict:'warn'},'开头和结尾不重叠，2000ms两个独立主体重合；建议必须定位中间时刻。',{afterIR:after,expectedEvidenceTimeMs:2000})
await mkdir(new URL('../../shared/demo/quality/',import.meta.url),{recursive:true})
await writeFile(new URL('../../shared/demo/quality/cases.json',import.meta.url),JSON.stringify({
  schemaVersion:'1.0',purpose:'manually-labelled-calibration-not-benchmark',
  thresholds:{overlapRatio:0.12,paletteDistance:0.12,noiseRatio:0.02},cases},null,2)+'\n')
console.log(`Wrote ${cases.length} samples (${cases.filter(c=>c.bad).length} labelled bad)`)
