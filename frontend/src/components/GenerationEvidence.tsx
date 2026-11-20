import Link from 'next/link'
import type { Generation, Issue } from '@/types/core'
import { formatDateTime, symbolLabel } from '@/utils/format'

export function IssueList({ issues }: { issues: Issue[] }) {
  return issues.length > 0 && <ul className="issue-list">
    {issues.map((issue, index) => <li key={index}>
      {issue.message}{issue.path && <> · <code>{issue.path}</code></>}
      {issue.nodeIds.length > 0 && <> · 节点 {issue.nodeIds.join('、')}</>}
    </li>)}
  </ul>
}

function externalEntityHref(sourceId: string, entityId: string): string | null {
  // Only a controlled, known source gets a link. Other deployment source keys
  // stay plain text; never turn an external id into a site knowledge link.
  return sourceId === 'wikidata' && /^Q\d+$/.test(entityId)
    ? `https://www.wikidata.org/wiki/${entityId}`
    : null
}

export function GenerationEvidence({ generation }: { generation: Generation }) {
  const source = generation.provenance
  if (!source) return null
  const external = source.externalGraph
  const assistant = source.assistant
  // externalGraph is stored as a JSON dict on the backend, so old/malformed
  // snapshots must not crash the evidence panel.
  const externalEntities = external?.entities ?? []
  const externalRelations = external?.relations ?? []
  const externalDecisions = external?.mappingDecisions ?? []
  const decisions = [
    ['色板', source.paletteDecision],
    ['构图', source.compositionDecision],
    ['时间轴', source.timelineDecision],
  ] as const

  return <details className="tech-details" open={generation.mode === 'semantic' || Boolean(assistant)}>
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
    {external && <section className="external-source" aria-label="外部知识图谱来源快照">
      <h4>外部知识图谱来源快照</h4>
      <dl className="tech-list">
        <div><dt>来源键</dt><dd>{external.sourceId}</dd></div>
        <div><dt>外部实体</dt><dd>
          {externalEntityHref(external.sourceId, external.externalEntityId)
            ? <a href={externalEntityHref(external.sourceId, external.externalEntityId)!} target="_blank" rel="noreferrer">{external.externalEntityId}</a>
            : <code>{external.externalEntityId}</code>}
        </dd></div>
        <div><dt>抓取时间</dt><dd>{formatDateTime(external.fetchedAt)}</dd></div>
        <div><dt>数据版本</dt><dd>{external.dataVersion ?? '来源未提供'}</dd></div>
        <div><dt>内容哈希</dt><dd className="mono">{external.contentHash}</dd></div>
      </dl>
      {externalEntities.length > 0 && <ul className="issue-list">
        {externalEntities.slice(0, 12).map((entity) => <li key={entity.id}>
          {externalEntityHref(external.sourceId, entity.id)
            ? <a href={externalEntityHref(external.sourceId, entity.id)!} target="_blank" rel="noreferrer">{entity.label}</a>
            : entity.label}
          {' · '}<code>{entity.id}</code>
          {entity.description && <> · {entity.description}</>}
          {Object.keys(entity.attributes ?? {}).length > 0 && <> · {Object.entries(entity.attributes ?? {}).slice(0, 6).map(([key, value]) => `${key}=${value}`).join('；')}</>}
        </li>)}
      </ul>}
      {externalRelations.length > 0 && <ul className="issue-list">
        {externalRelations.slice(0, 12).map((relation) => <li key={relation.id}>
          <code>{relation.fromId}</code> --{relation.type}--&gt; <code>{relation.toId}</code>
          {relation.label && <> · {relation.label}</>}
        </li>)}
      </ul>}
      {externalDecisions.length > 0 && <div>
        <h5>实际采纳的外部事实</h5>
        <ul className="issue-list">
          {externalDecisions.slice(0, 20).map((decision, index) => <li key={`${decision.externalId}:${decision.affects}:${index}`}>
            {decision.kind} · <code>{decision.externalId}</code> · 影响 {decision.affects}（{decision.target}{decision.targetId ? ` ${decision.targetId}` : ''}） · {decision.reason}
          </li>)}
        </ul>
      </div>}
      <p className="quality-note">外部 ID 只保留在来源快照中，不是站内知识链接；属性/关系影响叙事、规则或符号，不承诺改变 PNG 像素。</p>
    </section>}
    {assistant && <section className="assistant-source" aria-label="AI 智能辅助来源">
      <h4>AI 智能辅助溯源</h4>
      <dl className="tech-list">
        <div><dt>模型</dt><dd className="mono">{assistant.model}</dd></div>
        <div><dt>提案编号</dt><dd className="mono">{assistant.proposalId}</dd></div>
        <div><dt>校验/修复次数</dt><dd className="mono">{assistant.attempts} 次{assistant.attempts > 1 ? '（含自动修复）' : ''}</dd></div>
        <div><dt>策略版本</dt><dd className="mono">rev {assistant.policyRevision}</dd></div>
        <div><dt>提案哈希</dt><dd className="mono">{assistant.proposalHash}</dd></div>
      </dl>
    </section>}
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
