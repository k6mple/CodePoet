'use client'

import { useState } from 'react'
import type { Generation, QualityLocation, QualityReport, QualityStatus } from '@/types/core'
import { IssueList } from './GenerationEvidence'

const names = { parameterValidity:'参数有效性', layoutConflict:'主体交叠', colorSemantic:'色彩依据', signalNoise:'信号与杂散' }
const statuses: Record<QualityStatus,string> = { pass:'通过', warn:'需检查', fail:'未通过', not_applicable:'不适用', unavailable:'不可用' }
const units = { parameterValidity:'布尔指标', layoutConflict:'交叠比例', colorSemantic:'归一化距离', signalNoise:'dB（代理）' }
const explanations = {
  parameterValidity:'检查生成时的参数和结构；通过不代表像素质量通过。',
  layoutConflict:'检查不同顶层主体的交叠；同一分组内的设计交叠不计入。',
  colorSemantic:'比较可见填充与生成时的历史色板，描边和边缘不纳入。',
  signalNoise:'比较实际 PNG 前景与预期几何；空白画面单独判为未通过。',
}
const number = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : Number(value.toFixed(4)).toString()

export default function QualityEvidence({ generation, report, onLocate, onImprove }: {
  generation: Generation
  report?: QualityReport | null
  onLocate?: (location: QualityLocation) => void
  onImprove?: (location: QualityLocation) => void
}) {
  const [selected, setSelected] = useState<(QualityLocation & { message:string }) | null>(null)
  const validation = generation.validationReport
  const assessment = report?.assessment
  function locate(location: QualityLocation) {
    const nodeIds = location.nodeIds.filter(id => generation.ir.nodes.some(node => node.id === id))
    const timeMs = location.timeMs == null ? null : Math.max(0,Math.min(generation.ir.durationMs,location.timeMs))
    onLocate?.({ nodeIds, timeMs })
  }
  return <section className="quality-evidence" aria-label="质量检查">
    <div className="quality-stage">
      <h3>生成校验</h3>
      <p>{validation
        ? `参数校验${validation.parameterValid?'通过':'未通过'} · ${validation.nodeCount}个节点 · ${validation.outOfBoundsNodeCount}个节点完全出界`
        : '该生成记录未提供结构校验报告。'}</p>
      <p className="quality-note">参数与结构合法不代表像素质量或审美通过。</p>
      <IssueList issues={[...(validation?.issues??[]),...(generation.warnings??[])]} />
    </div>
    <div className="quality-stage">
      <h3>保存后的像素质量</h3>
      {!report ? <p role="status">尚未像素评估。保存并收齐真实 PNG 帧后，由服务器返回报告。</p> : <>
        <p>非背景像素比例 {(report.nonBackgroundPixelRatio*100).toFixed(2)}% · {report.emptyOutput?'存在空白帧':'基础空白检查未发现空白帧'}
          {report.pixelDifferenceThreshold!=null && <> · 像素差阈值 {report.pixelDifferenceThreshold}</>}</p>
        <IssueList issues={report.issues??[]} />
        {!assessment ? <p role="status">此报告只有基础像素检查；四项版本化质量指标尚未评估。</p> : <>
          <p className="quality-version">算法 <code>{assessment.algorithmVersion}</code> · 实测时刻 {assessment.evaluatedTimesMs.map(time=>`${time}ms`).join(' / ')||'无'}
            {assessment.status && <> · {statuses[assessment.status]??assessment.status}</>}</p>
          <p className="quality-note">以下为项目内部质量指标，分数未校准；没有综合分，也不代表赛题得分。</p>
          <div className="quality-metrics">{assessment.metrics.map(metric=><article className="quality-metric" key={metric.name} data-status={metric.status}>
            <header><h4>{names[metric.name]??metric.name}</h4><span>{statuses[metric.status]??metric.status}</span></header>
            <p className="mono">测量值 {number(metric.value)} {units[metric.name]} · 分数 {metric.score==null?'未校准':number(metric.score)}</p>
            <p className="quality-note">{explanations[metric.name]}</p>
            <details><summary>方法与依据（{metric.evidence.length}）</summary><p className="quality-note">{metric.method}</p><ul className="issue-list">
              {metric.evidence.map((evidence,index)=><li key={index}>
                <p>{evidence.message} {evidence.value!=null && <>· {number(evidence.value)}</>}</p>
                {evidence.details && <dl className="tech-list">{Object.entries(evidence.details).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value??'—'}</dd></div>)}</dl>}
                {(evidence.nodeIds.length>0 || evidence.timeMs!=null) && <p className="quality-target">节点 {evidence.nodeIds.join('、')||'整帧'} · {evidence.timeMs??0}ms</p>}
                {onLocate && (evidence.nodeIds.length>0 || evidence.timeMs!=null) && <button type="button" className="button button--secondary button--small" onClick={()=>locate(evidence)}>定位此依据</button>}
              </li>)}
            </ul></details>
          </article>)}</div>
          {assessment.recommendations.length>0 && <div className="quality-recommendations"><h4>检查建议</h4><ul className="issue-list">
            {assessment.recommendations.map((recommendation,index)=><li key={`${recommendation.code}:${index}`}>
              <p>{recommendation.message}</p>
              <p className="quality-target">节点 {recommendation.nodeIds.join('、')||'整帧'} · {recommendation.timeMs??0}ms</p>
              {onLocate && <button type="button" className="button button--secondary button--small" onClick={()=>locate(recommendation)}>定位问题</button>}
              {onImprove && <button type="button" className="button button--link button--small" onClick={()=>setSelected(recommendation)}>准备修改</button>}
            </li>)}
          </ul></div>}
          {selected && onImprove && <div className="quality-confirm" role="group" aria-label="确认质量改进">
            <p>{selected.message}</p><p>确认后仅定位并打开 IR 编辑。请自行检查和修改，再校验生成、保存新版本；当前生成记录和旧作品保持不变。</p>
            <button type="button" className="button button--primary button--small" onClick={()=>{locate(selected);onImprove(selected);setSelected(null)}}>确认并打开编辑</button>
            <button type="button" className="button button--secondary button--small" onClick={()=>setSelected(null)}>取消修改</button>
          </div>}
          <details className="tech-details"><summary>阈值与适用范围</summary>
            <dl className="tech-list">{Object.entries(assessment.thresholds).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
            <ul className="issue-list">{assessment.limitations?.map((text,index)=><li key={index}>{text}</li>)}</ul>
          </details>
        </>}
      </>}
    </div>
  </section>
}
