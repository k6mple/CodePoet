'use client'

import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  applyAssistantProposal,
  createAssistantProposal,
  getAssistantProposal,
} from '@/api/assistant'
import { errorMessage } from '@/api/client'
import type { AssistantProposal, AssistantTarget, Generation, User } from '@/types/core'
import { shortId } from '@/utils/format'
import { InlineNotice } from './Feedback'
import { IssueList } from './GenerationEvidence'

const IR_FRAGMENT_PRESETS = [
  {
    label: '青莲双层花瓣',
    prompt: '将选中节点改造为清雅对称的双层青莲花瓣与莲心纹样，采用宣纸素雅青绿与淡粉配色，确保位于画布中央内',
  },
  {
    label: '竹节守正枝叶',
    prompt: '将选中节点替换为挺拔修长的翠竹竹节与两片清雅竹叶矢量图元，配色以墨绿与竹青为主，保持在画布边界内',
  },
  {
    label: '墨梅寒枝点蕊',
    prompt: '将选中节点改造为苍劲墨梅枝干与三朵素雅朱红梅花图元，层次清晰且留白得宜',
  },
] as const

const PARAMETER_SCHEMA_PRESETS = [
  {
    label: '清莲比例与配色参数',
    prompt: '为当前模板设计 2 到 3 个清晰可控的参数（如核心尺寸半径、主体填充色与描边色），并给出素雅廉洁青墨默认配色',
  },
  {
    label: '构图位移与缩放参数',
    prompt: '设计包含水平位置、缩放比例与图层透明度的参数空间，合理限定取值边界并设置均衡默认值',
  },
  {
    label: '线条粗细与色板参数',
    prompt: '设计主体色、轮廓描边色与描边线宽的参数空间，默认采用宣纸青绿与黛墨雅致配色',
  },
] as const

interface AssistantPanelProps {
  enabled: boolean
  user: User | null
  generation: Generation | null
  selectedNodeId: string | null
  disabled: boolean
  onRequireLogin: (reason: string) => void
  onApplied: (generation: Generation, proposal: AssistantProposal) => void
}

function statusLabel(status: AssistantProposal['status']): string {
  switch (status) {
    case 'queued':
      return '排队中'
    case 'running':
      return '生成与隔离校验中…'
    case 'ready':
      return '隔离校验通过 · 可应用'
    case 'failed':
      return '校验或生成未通过'
  }
}

export default function AssistantPanel({
  enabled,
  user,
  generation,
  selectedNodeId,
  disabled,
  onRequireLogin,
  onApplied,
}: AssistantPanelProps) {
  const [open, setOpen] = useState(true)
  const [target, setTarget] = useState<AssistantTarget>('ir_fragment')
  const [rootNodeId, setRootNodeId] = useState('')
  const [prompt, setPrompt] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [applying, setApplying] = useState(false)
  const [proposal, setProposal] = useState<AssistantProposal | null>(null)
  const [error, setError] = useState('')
  const [appliedNotice, setAppliedNotice] = useState('')
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const activeRunToken = useRef(0)

  const canUseBase = Boolean(user && generation && generation.ownerId === user.id)
  const supportsParameterSchema = Boolean(
    canUseBase
    && generation
    && generation.mode === 'template'
    && generation.templateVersionId
    && generation.provenance?.kind === 'template'
    && !generation.provenance?.assistant,
  )

  const nodeOptions = useMemo(
    () => (generation?.ir.nodes ?? []).map((node) => ({ id: node.id, type: node.type })),
    [generation],
  )

  // 当切换 generation 或在 IR 树里点选节点时，同步默认目标节点
  useEffect(() => {
    if (!generation) {
      setRootNodeId('')
      return
    }
    const ids = generation.ir.nodes.map((n) => n.id)
    if (selectedNodeId && ids.includes(selectedNodeId)) {
      setRootNodeId(selectedNodeId)
      return
    }
    setRootNodeId((prev) => {
      if (prev && ids.includes(prev)) return prev
      const leaf = generation.ir.nodes.find((n) => n.type !== 'group')
      return leaf?.id ?? ids[0] ?? ''
    })
  }, [generation, selectedNodeId])

  // 若当前基底不支持 parameter_schema，自动切回 ir_fragment
  useEffect(() => {
    if (target === 'parameter_schema' && !supportsParameterSchema) {
      setTarget('ir_fragment')
    }
  }, [target, supportsParameterSchema])

  // 用户登出或切换账号时，立即中止轮询并清空旧提案状态，避免串号或 401 误报
  useEffect(() => {
    stopPolling()
    setSubmitting(false)
    setApplying(false)
    setProposal(null)
    setError('')
    setAppliedNotice('')
  }, [user?.id])

  // 当画布切换到与当前提案无关的快照（如换模板/换后端）时清理陈旧提案，防止误应用错后端
  useEffect(() => {
    if (!proposal) return
    const currentId = generation?.id
    if (currentId && (currentId === proposal.baseGenerationId || currentId === proposal.appliedGenerationId)) {
      return
    }
    stopPolling()
    setSubmitting(false)
    setProposal(null)
    setError('')
    setAppliedNotice('')
  }, [generation?.id, proposal])

  // 卸载时清理轮询定时器
  useEffect(() => () => {
    activeRunToken.current++
    if (pollTimer.current) clearTimeout(pollTimer.current)
  }, [])

  function stopPolling() {
    activeRunToken.current++
    if (pollTimer.current) {
      clearTimeout(pollTimer.current)
      pollTimer.current = null
    }
  }

  async function pollProposal(id: string, token: number) {
    try {
      const latest = await getAssistantProposal(id)
      if (activeRunToken.current !== token) return
      setProposal(latest)
      if (latest.status === 'ready' || latest.status === 'failed') {
        setSubmitting(false)
        return
      }
      pollTimer.current = setTimeout(() => {
        void pollProposal(id, token)
      }, 600)
    } catch (cause) {
      if (activeRunToken.current !== token) return
      setError(errorMessage(cause))
      setSubmitting(false)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!enabled) return
    if (!user) {
      onRequireLogin('登录后即可使用 AI 智能辅助生成提案。')
      return
    }
    if (!generation || !canUseBase) {
      setError('请先生成一幅属于你的作品画卷，再发起 AI 辅助提案。')
      return
    }
    const cleanPrompt = prompt.trim()
    if (!cleanPrompt) {
      setError('请输入自然语言创作或改造需求。')
      return
    }
    if (target === 'ir_fragment' && !rootNodeId) {
      setError('请选择需要局部改造的目标 IR 节点。')
      return
    }

    stopPolling()
    const token = ++activeRunToken.current
    setSubmitting(true)
    setError('')
    setAppliedNotice('')
    setProposal(null)

    try {
      const created = await createAssistantProposal({
        baseGenerationId: generation.id,
        prompt: cleanPrompt,
        target,
        rootNodeId: target === 'ir_fragment' ? rootNodeId : undefined,
      })
      if (activeRunToken.current !== token) return
      setProposal(created)
      if (created.status === 'ready' || created.status === 'failed') {
        setSubmitting(false)
      } else {
        pollTimer.current = setTimeout(() => {
          void pollProposal(created.id, token)
        }, 500)
      }
    } catch (cause) {
      if (activeRunToken.current !== token) return
      setError(errorMessage(cause))
      setSubmitting(false)
    }
  }

  async function handleApply() {
    if (!proposal || proposal.status !== 'ready' || applying || disabled) return
    setApplying(true)
    setError('')
    setAppliedNotice('')
    try {
      const applied = await applyAssistantProposal(proposal.id)
      const refreshed: AssistantProposal = {
        ...proposal,
        appliedGenerationId: applied.id,
      }
      setProposal(refreshed)
      onApplied(applied, refreshed)
      setAppliedNotice(
        `已应用 AI 提案（生成快照 ${shortId(applied.id, 12)}）。画布已切换至新快照，当前尚未像素评估，可直接命名保存为私有作品。`,
      )
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setApplying(false)
    }
  }

  const presets = target === 'ir_fragment' ? IR_FRAGMENT_PRESETS : PARAMETER_SCHEMA_PRESETS
  const isBusy = submitting || applying || disabled
  const repairedIssues = proposal?.validation?.repairedIssues ?? []
  const validationIssues = proposal?.validation?.issues ?? []

  return <section className="studio-assistant" aria-labelledby="assistant-heading">
    <div className="assistant-head">
      <div>
        <div className="assistant-head__title">
          <h3 id="assistant-heading">AI 智能辅助</h3>
          <span className={`assistant-badge ${enabled ? 'is-on' : 'is-off'}`}>
            {enabled ? '模型服务已就绪' : '未配置密钥'}
          </span>
        </div>
        <p className="assistant-lead">
          输入自然语言需求，由服务端模型产出合法 IR 片段或参数空间提案，经沙盒隔离校验与自动修复后再显式应用（不输出原始绘图代码）。
        </p>
      </div>
      <button
        type="button"
        className="button button--ghost button--small"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        {open ? '收起面板' : '展开面板'}
      </button>
    </div>

    {open && <div className="assistant-body">
      {!enabled && <InlineNotice tone="info" title="AI 智能辅助不可用">
        当前未配置模型密钥，AI 辅助提案不可用。管理员在部署环境变量配置 <code>CODEPOET_LLM_API_KEY</code> 后将自动启用。
      </InlineNotice>}

      {enabled && !user && <InlineNotice tone="info" title="登录后使用 AI 智能辅助">
        需要登录后基于当前作品画卷生成并应用 AI 辅助提案。
        <div className="notice__actions">
          <button
            type="button"
            className="button button--secondary button--small"
            onClick={() => onRequireLogin('登录后即可使用 AI 智能辅助。')}
          >
            立即登录
          </button>
        </div>
      </InlineNotice>}

      {enabled && user && !canUseBase && <p className="rail-lead">
        先生成一幅属于你的作品作为基础画卷，再在此发起 IR 片段改造或参数空间扩展提案。
      </p>}

      <form className="assistant-form" onSubmit={(event) => void handleSubmit(event)}>
        <div className="assistant-controls">
          <div className="assistant-target" role="group" aria-label="AI 辅助目标模式">
            <button
              type="button"
              className={`button button--small button--${target === 'ir_fragment' ? 'primary' : 'secondary'}`}
              aria-pressed={target === 'ir_fragment'}
              disabled={!enabled || isBusy}
              onClick={() => setTarget('ir_fragment')}
            >
              IR 片段改造 (ir_fragment)
            </button>
            <button
              type="button"
              className={`button button--small button--${target === 'parameter_schema' ? 'primary' : 'secondary'}`}
              aria-pressed={target === 'parameter_schema'}
              disabled={!enabled || isBusy || !supportsParameterSchema}
              title={supportsParameterSchema ? '基于当前模板生成扩展参数空间提案' : '仅未手改的模板基底快照支持参数空间生成'}
              onClick={() => setTarget('parameter_schema')}
            >
              参数空间生成 (parameter_schema)
            </button>
          </div>

          {target === 'ir_fragment' && <label className="field assistant-node-field">
            <span>目标 IR 节点 (rootNodeId)</span>
            <select
              aria-label="目标 IR 节点"
              value={rootNodeId}
              disabled={!enabled || !canUseBase || isBusy || nodeOptions.length === 0}
              onChange={(event) => setRootNodeId(event.target.value)}
            >
              {nodeOptions.length === 0
                ? <option value="">暂无可选节点</option>
                : nodeOptions.map((item) => <option key={item.id} value={item.id}>
                  {item.id}（{item.type}）
                </option>)}
            </select>
          </label>}
        </div>

        {!supportsParameterSchema && canUseBase && <p className="assistant-hint">
          提示：当前画卷已非原始模板基底，已自动限定为 <code>ir_fragment</code> 局部子树改造模式；切回模板默认生成后可使用 <code>parameter_schema</code>。
        </p>}

        <div className="preset-block">
          <span className="preset-block__label">廉洁美学快捷提示词</span>
          <div className="preset-chips">
            {presets.map((item) => <button
              key={item.label}
              type="button"
              className={`preset-chip${prompt === item.prompt ? ' is-active' : ''}`}
              disabled={!enabled || isBusy}
              onClick={() => setPrompt(item.prompt)}
            >
              {item.label}
            </button>)}
          </div>
        </div>

        <label className="field">
          <span>自然语言创作需求（1～2000 字）</span>
          <textarea
            rows={3}
            maxLength={2000}
            aria-label="AI 辅助自然语言需求"
            placeholder={target === 'ir_fragment'
              ? '描述希望如何改造选中 IR 子树，例如：将莲心节点改造为双层素雅青莲花瓣与金色花蕊…'
              : '描述希望为当前模板扩展哪些可控参数与廉洁美学默认配色…'}
            value={prompt}
            disabled={!enabled || isBusy}
            onChange={(event) => setPrompt(event.target.value)}
          />
        </label>

        <div className="assistant-actions">
          <button
            type="submit"
            className="button button--primary button--small"
            disabled={!enabled || !canUseBase || isBusy || !prompt.trim() || (target === 'ir_fragment' && !rootNodeId)}
          >
            {submitting ? '正在生成并隔离校验…' : '生成 AI 提案'}
          </button>
          {proposal?.status === 'ready' && <button
            type="button"
            className="button button--secondary button--small"
            disabled={applying || disabled}
            onClick={() => void handleApply()}
          >
            {applying ? '正在应用提案…' : proposal.appliedGenerationId ? '再次应用提案（幂等）' : '应用提案'}
          </button>}
        </div>
      </form>

      {error && <InlineNotice tone="error" title="AI 提案操作失败">{error}</InlineNotice>}
      {appliedNotice && <p className="archive-success" role="status">{appliedNotice}</p>}

      {proposal && <div className="assistant-result" aria-live="polite">
        <div className="assistant-result__head">
          <span className={`assistant-status is-${proposal.status}`}>{statusLabel(proposal.status)}</span>
          <div className="assistant-meta">
            <span className="diag-pill">模型：<strong className="mono">{proposal.model}</strong></span>
            <span className="diag-pill">校验/修复次数：<strong className="mono">{proposal.attempts} / 2</strong></span>
            {proposal.validation && <span className="diag-pill">
              隔离校验：{proposal.validation.isolatedValidationPassed ? '已通过' : '未通过'} · policy rev {proposal.validation.policyRevision}
            </span>}
            {proposal.proposalHash && <span className="diag-pill">
              提案哈希：<code className="mono">{shortId(proposal.proposalHash, 14)}</code>
            </span>}
          </div>
        </div>

        {repairedIssues.length > 0 && <div className="assistant-repair-box">
          <p className="assistant-repair-box__title">
            自动检测与修复记录：第 1 次输出检出 {repairedIssues.length} 项结构/约束问题，已反馈模型在第 {proposal.attempts} 次尝试中修复
          </p>
          <IssueList issues={repairedIssues} />
        </div>}

        {proposal.error && <InlineNotice tone="error" title={`提案失败 · ${proposal.error.code}`}>
          {proposal.error.message}
        </InlineNotice>}

        {validationIssues.length > 0 && <div className="assistant-issues">
          <p className="assistant-issues__title">
            {proposal.status === 'ready' ? '非阻断校验提示' : '最终校验问题清单'}
          </p>
          <IssueList issues={validationIssues} />
        </div>}

        {proposal.proposal && <details className="tech-details" open>
          <summary>提案 JSON 预览（{proposal.target}）</summary>
          <pre className="assistant-json">{JSON.stringify(proposal.proposal, null, 2)}</pre>
        </details>}
      </div>}
    </div>}
  </section>
}
