'use client'

import { SYMBOL_OPTIONS, type MetadataDraft } from '@/utils/template'

export default function TemplateMetadataForm({ value, onChange, disabled = false }: {
  value: MetadataDraft
  onChange: (next: MetadataDraft) => void
  disabled?: boolean
}) {
  function update(patch: Partial<MetadataDraft>) {
    onChange({ ...value, ...patch })
  }

  function toggleSymbol(symbol: string) {
    update({
      symbols: value.symbols.includes(symbol)
        ? value.symbols.filter((item) => item !== symbol)
        : [...value.symbols, symbol],
    })
  }

  return <>
    <label className="field">
      <span>模板名称</span>
      <input required maxLength={80} value={value.name} disabled={disabled} onChange={(event) => update({ name: event.target.value })} />
    </label>
    <label className="field">
      <span>说明（最多 1000 字）</span>
      <textarea rows={3} maxLength={1000} value={value.description} disabled={disabled} onChange={(event) => update({ description: event.target.value })} />
    </label>
    <fieldset className="editor-fieldset">
      <legend>文化符号</legend>
      <div className="checklist checklist--inline">
        {SYMBOL_OPTIONS.map((option) => <label className="checklist-item" key={option.value}>
          <input
            type="checkbox"
            checked={value.symbols.includes(option.value)}
            disabled={disabled}
            onChange={() => toggleSymbol(option.value)}
          />
          <span>{option.label}</span>
        </label>)}
      </div>
      <p className="field-note">符号用于模板库筛选；只选择模板真实包含的主题，不要用装饰性标签冒充。</p>
    </fieldset>
    <div className="editor-grid">
      <label className="field">
        <span>时间主题（可选，最多 40 字）</span>
        <input maxLength={40} value={value.timeTheme} disabled={disabled} onChange={(event) => update({ timeTheme: event.target.value })} />
      </label>
      <label className="field">
        <span>关联实体 ID（逗号或空格分隔）</span>
        <input value={value.entityIdsText} disabled={disabled} onChange={(event) => update({ entityIdsText: event.target.value })} />
      </label>
      <label className="field">
        <span>关联标签 ID（逗号或空格分隔）</span>
        <input value={value.tagIdsText} disabled={disabled} onChange={(event) => update({ tagIdsText: event.target.value })} />
      </label>
    </div>
    <p className="field-note">实体/标签 ID 可留空；服务器会校验引用。元数据不会自动生成作品内容。</p>
  </>
}
