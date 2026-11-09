import Link from 'next/link'
import type { Generation, Issue, RenderJob } from '@/types/core'
import { symbolLabel } from '@/utils/format'
import { InlineNotice } from './Feedback'

export function IssueList({ issues }: { issues: Issue[] }) {
  return issues.length > 0 && <ul className="issue-list">
    {issues.map((issue, index) => <li key={index}>
      {issue.message}{issue.path && <> · <code>{issue.path}</code></>}
      {issue.nodeIds.length > 0 && <> · 节点 {issue.nodeIds.join('、')}</>}
    </li>)}
  </ul>
}

export function GenerationEvidence({ generation }: { generation: Generation }) {
  const source = generation.provenance
  if (!source) return null
  const decisions = [
    ['色板', source.paletteDecision],
    ['构图', source.compositionDecision],
    ['时间轴', source.timelineDecision],
  ] as const

  return <details className="tech-details" open={generation.mode === 'semantic'}>
    <summary>生成依据与来源快照</summary>
    <dl className="tech-list">
      <div><dt>来源类型</dt><dd>{source.kind}</dd></div>
      <div><dt>视觉符号</dt><dd>{source.selectedSymbols.map(symbolLabel).join('、') || '—'}</dd></div>
      <div><dt>引擎与配置</dt><dd>{source.engineVersion} · 本体 {source.ontologyVersion} · 策略 {source.policyRevision}</dd></div>
      {source.templateSelection && <div><dt>模板选择</dt><dd>
        {source.templateSelection.templateVersionId} · {source.templateSelection.reason}
      </dd></div>}
      {decisions.map(([label, decision]) => decision && <div key={label}>
        <dt>{label}</dt><dd>{decision.reason} · {JSON.stringify(decision.value)}
          {decision.ruleIds.length > 0 && <> · 规则 {decision.ruleIds.join('、')}</>}
        </dd>
      </div>)}
    </dl>
    {source.matchedEntities.length > 0 && <ul className="issue-list">
      {source.matchedEntities.map((entity) => <li key={entity.entityId}>
        <Link href={`/knowledge/${encodeURIComponent(entity.entityId)}`}>
          {typeof entity.content.name === 'string' ? entity.content.name : entity.entityId}
        </Link> · 知识快照 rev {entity.revision}
      </li>)}
    </ul>}
    {source.matchedRules.length > 0 && <ul className="issue-list">
      {source.matchedRules.map((rule) => <li key={rule.ruleId}>
        {typeof rule.content.name === 'string' ? rule.content.name : rule.ruleId} · 规则快照 v{rule.version}
      </li>)}
    </ul>}
    <IssueList issues={[...source.conflicts, ...source.warnings]} />
    <details className="tech-details">
      <summary>查看完整历史快照 JSON</summary>
      <pre>{JSON.stringify(source, null, 2)}</pre>
    </details>
  </details>
}

export function QualityEvidence({ generation, report }: {
  generation: Generation
  report?: RenderJob['qualityReport']
}) {
  const validation = generation.validationReport
  if (!validation && !report && !generation.warnings?.length) return null
  return <InlineNotice title="生成与输出检查">
    {validation && <p>参数校验 {validation.parameterValid ? '通过' : '未通过'} · {validation.nodeCount} 个节点 · {validation.outOfBoundsNodeCount} 个节点完全出界</p>}
    {report && <p>非背景像素比例 {(report.nonBackgroundPixelRatio * 100).toFixed(2)}%{report.emptyOutput && ' · 输出为空白，请检查画布'}</p>}
    <IssueList issues={[...(validation?.issues ?? []), ...(generation.warnings ?? []), ...(report?.issues ?? [])]} />
  </InlineNotice>
}
