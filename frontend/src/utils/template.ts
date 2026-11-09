import type { DrawingIR, Parameter, TemplateMetadata, TemplateVersion } from '@/types/core'

export const SYMBOL_OPTIONS = [
  { value: 'lotus', label: '莲' },
  { value: 'bamboo', label: '竹' },
  { value: 'water', label: '水' },
  { value: 'narrative', label: '叙事' },
]

export interface MetadataDraft {
  name: string
  description: string
  symbols: string[]
  timeTheme: string
  entityIdsText: string
  tagIdsText: string
}

export function metadataToDraft(metadata: TemplateMetadata): MetadataDraft {
  return {
    name: metadata.name,
    description: metadata.description,
    symbols: [...(metadata.symbols ?? [])],
    timeTheme: metadata.timeTheme ?? '',
    entityIdsText: (metadata.entityIds ?? []).join(', '),
    tagIdsText: (metadata.tagIds ?? []).join(', '),
  }
}

export function parseIdList(text: string) {
  return Array.from(new Set(text.split(/[\s,]+/).map((value) => value.trim()).filter(Boolean)))
}

export function draftToMetadata(draft: MetadataDraft): TemplateMetadata {
  return {
    name: draft.name.trim(),
    description: draft.description,
    symbols: draft.symbols,
    timeTheme: draft.timeTheme.trim() || null,
    entityIds: parseIdList(draft.entityIdsText),
    tagIds: parseIdList(draft.tagIdsText),
  }
}

export const STARTER_IR: DrawingIR = {
  schemaVersion: '1.0',
  seed: 42,
  canvas: { width: 800, height: 600, background: '#F7F6F0' },
  viewport: { x: 0, y: 0, width: 800, height: 600 },
  durationMs: 0,
  nodes: [
    {
      id: 'petal',
      parentId: null,
      type: 'circle',
      geometry: { cx: 400, cy: 300, r: 80 },
      style: { fill: '#A83E32', stroke: '#202421', strokeWidth: 2, opacity: 1 },
      material: { shaderRef: 'flat' },
    },
  ],
  animations: [],
  narrative: [],
}

export const STARTER_PARAMETERS: Parameter[] = [
  {
    name: 'petalSize',
    label: '中心图形半径',
    description: '绑定到模板中心圆的 geometry.r。',
    type: 'integer',
    default: 80,
    min: 40,
    max: 140,
    step: 2,
  },
]

export const STARTER_BINDINGS = [
  { parameter: 'petalSize', nodeId: 'petal', property: 'geometry.r' },
]

export function prettyJson(value: unknown) {
  return JSON.stringify(value, null, 2)
}

export function validateParameterSchema(value: unknown): string[] {
  if (!Array.isArray(value)) return ['参数 schema 必须是 JSON 数组。']
  if (value.length > 16) return ['参数最多 16 项。']
  const errors: string[] = []
  const names = new Set<string>()
  value.forEach((entry, index) => {
    const prefix = `参数 #${index + 1}`
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) {
      errors.push(`${prefix} 必须是对象。`)
      return
    }
    const item = entry as Record<string, unknown>
    const name = item.name
    if (typeof name !== 'string' || !/^[a-zA-Z][a-zA-Z0-9_]{0,39}$/.test(name)) {
      errors.push(`${prefix} 的 name 必须以字母开头，只含字母数字下划线。`)
    } else if (names.has(name)) {
      errors.push(`${prefix} 的 name 重复：${name}。`)
    } else {
      names.add(name)
    }
    if (typeof item.label !== 'string' || !item.label.trim() || item.label.length > 80) {
      errors.push(`${prefix} 的 label 必须为 1–80 字。`)
    }
    if (typeof item.description !== 'string' || item.description.length > 300) {
      errors.push(`${prefix} 的 description 必须为不超过 300 字的字符串。`)
    }
    if (!['number', 'integer', 'enum', 'boolean', 'color'].includes(String(item.type))) {
      errors.push(`${prefix} 的 type 不在允许范围内。`)
      return
    }
    if (item.type === 'number' || item.type === 'integer') {
      const numeric = ['default', 'min', 'max', 'step'] as const
      for (const key of numeric) {
        if (typeof item[key] !== 'number' || !Number.isFinite(item[key] as number)) {
          errors.push(`${prefix} 的 ${key} 必须是数值。`)
        }
      }
      if (item.type === 'integer') {
        for (const key of numeric) {
          if (typeof item[key] === 'number' && !Number.isInteger(item[key] as number)) errors.push(`${prefix} 的 ${key} 必须是整数。`)
        }
        if (typeof item.step === 'number' && item.step < 1) errors.push(`${prefix} 的 step 必须 ≥ 1。`)
      } else if (typeof item.step === 'number' && item.step <= 0) {
        errors.push(`${prefix} 的 step 必须大于 0。`)
      }
      if (typeof item.min === 'number' && typeof item.max === 'number' && item.min > item.max) {
        errors.push(`${prefix} 的 min 不能大于 max。`)
      }
      if (typeof item.default === 'number' && typeof item.min === 'number' && typeof item.max === 'number'
        && (item.default < item.min || item.default > item.max)) {
        errors.push(`${prefix} 的 default 必须在 min–max 之间。`)
      }
    }
    if (item.type === 'enum') {
      if (!Array.isArray(item.options) || item.options.length < 1 || item.options.length > 20) {
        errors.push(`${prefix} 的 options 必须为 1–20 项数组。`)
      } else {
        const options = item.options.filter((option): option is string => typeof option === 'string')
        if (options.length !== item.options.length) errors.push(`${prefix} 的 options 只能包含字符串。`)
        if (new Set(options).size !== options.length) errors.push(`${prefix} 的 options 不能重复。`)
        if (typeof item.default !== 'string' || !options.includes(item.default)) {
          errors.push(`${prefix} 的 default 必须属于 options。`)
        }
      }
    }
    if (item.type === 'boolean' && typeof item.default !== 'boolean') errors.push(`${prefix} 的 default 必须是布尔值。`)
    if (item.type === 'color' && (typeof item.default !== 'string' || !/^#[0-9A-Fa-f]{6}$/.test(item.default))) {
      errors.push(`${prefix} 的 default 必须是 #RRGGBB。`)
    }
  })
  return errors
}

export function versionStatusLabel(status: string) {
  const labels: Record<string, string> = {
    draft: '草稿', submitted: '待审核', published: '已发布', rejected: '已退回', archived: '已归档',
  }
  return labels[status] ?? status
}

export function reviewQueueStatusLabel(status: string) {
  const labels: Record<string, string> = {
    submitted: '待审核', pending_review: '待审核', published: '已发布', rejected: '已退回', archived: '已归档',
  }
  return labels[status] ?? status
}

export function reviewDecisionLabel(decision: string) {
  return decision === 'approve' ? '通过' : decision === 'reject' ? '退回' : decision
}

export function reviewTargetLabel(targetType: string) {
  return targetType === 'template_version' ? '模板版本' : targetType === 'work_version' ? '作品版本' : targetType
}

export function isEditableVersion(status: string) {
  return status === 'draft' || status === 'rejected'
}

export function diffTemplateVersions(left: TemplateVersion, right: TemplateVersion) {
  const fields: { key: keyof TemplateVersion; label: string }[] = [
    { key: 'metadata', label: '元数据' },
    { key: 'irTemplate', label: 'IR 模板' },
    { key: 'parameterSchema', label: '参数 schema' },
    { key: 'bindings', label: '参数绑定' },
    { key: 'constraints', label: '约束' },
    { key: 'supportedBackends', label: '支持后端' },
    { key: 'changeNote', label: '变更说明' },
  ]
  const changed: string[] = []
  for (const field of fields) {
    if (JSON.stringify(left[field.key]) !== JSON.stringify(right[field.key])) changed.push(field.label)
  }
  return changed
}
