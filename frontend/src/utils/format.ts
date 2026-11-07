import type { KnowledgeType, RelationPredicate, Role, Value } from '@/types/core'

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
})

export function formatDateTime(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateTimeFormatter.format(date)
}

export function shortId(value?: string | null, length = 8) {
  if (!value) return '—'
  return value.length > length ? `${value.slice(0, length)}…` : value
}

export function paddedIndex(value: number) {
  return String(value).padStart(2, '0')
}

const roleLabels: Record<string, string> = {
  creator: '普通创作者', developer: '模板开发者', expert: '领域专家', admin: '管理员',
}
export function roleLabel(role: string) {
  return roleLabels[role] ?? role
}

export const ROLE_OPTIONS: { value: Role; label: string }[] = [
  { value: 'creator', label: '普通创作者' },
  { value: 'developer', label: '模板开发者' },
  { value: 'expert', label: '领域专家' },
  { value: 'admin', label: '管理员' },
]

const applicationStatusLabels: Record<string, string> = {
  pending: '待审批', approved: '已批准', rejected: '已拒绝',
}
export function applicationStatusLabel(status?: string | null) {
  if (!status) return '—'
  return applicationStatusLabels[status] ?? status
}

export function accountStatusLabel(status?: string | null) {
  return status === 'active' ? '正常' : status === 'disabled' ? '已停用' : (status ?? '—')
}

const knowledgeTypeLabels: Record<string, string> = {
  person: '人物', story: '典故', concept: '理念', symbol: '象征',
}
export function knowledgeTypeLabel(type: string) {
  return knowledgeTypeLabels[type] ?? type
}

export const KNOWLEDGE_TYPE_OPTIONS: { value: KnowledgeType; label: string }[] = [
  { value: 'person', label: '人物' },
  { value: 'story', label: '典故' },
  { value: 'concept', label: '理念' },
  { value: 'symbol', label: '象征' },
]

const relationPredicateLabels: Record<string, string> = {
  embodies: '体现', associated_with: '关联', symbolizes: '象征', derived_from: '源自',
}
export function relationPredicateLabel(predicate: string) {
  return relationPredicateLabels[predicate] ?? predicate
}

export const RELATION_PREDICATE_OPTIONS: { value: RelationPredicate; label: string }[] = [
  { value: 'embodies', label: '体现' },
  { value: 'associated_with', label: '关联' },
  { value: 'symbolizes', label: '象征' },
  { value: 'derived_from', label: '源自' },
]

export function entityStatusLabel(status?: string | null) {
  return status === 'active' ? '启用' : status === 'inactive' ? '停用' : (status ?? '—')
}

const workStatusLabels: Record<string, string> = {
  private: '私有', pending_review: '待审核', published: '已发布', rejected: '已退回',
}
export function workStatusLabel(status?: string | null) {
  if (!status) return '—'
  return workStatusLabels[status] ?? status
}

const symbolLabels: Record<string, string> = {
  lotus: '莲', bamboo: '竹', water: '水', narrative: '叙事',
}
export function symbolLabel(symbol: string) {
  return symbolLabels[symbol] ?? symbol
}

export function parameterValueLabel(value: Value | undefined) {
  if (value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '开启' : '关闭'
  return String(value)
}
