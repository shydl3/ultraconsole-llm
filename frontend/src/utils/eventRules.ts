import type { VlmEventDefinition } from './vlm'

export const triggerKinds = ['line.cross', 'zone.enter', 'zone.leave', 'initial_inside'] as const
export type EventRule = Required<VlmEventDefinition> & { name: string; version: number; createdAt?: number; updatedAt?: number }
export type RuleLibrary = { revision: number; rules: EventRule[] }
export type RuleSelection = { id: string; version: number; poiIds: string[] }
export type RuleSnapshot = { libraryRevision: number | null; rules: { id: string; name: string; version: number | null; event: VlmEventDefinition }[] }
export type RulePoi = { id: string; label: string; kind: string }
export const newRuleId = () => `event_${crypto.randomUUID().replaceAll('-', '')}`
export function newRule(description = ''): EventRule {
  return { id: newRuleId(), name: description.slice(0, 160), description, enabled: true, poiIds: [], triggerKinds: ['line.cross', 'zone.enter', 'zone.leave'], preSeconds: 4, postSeconds: 6, criteria: [], uncertainWhen: [], version: 0 }
}
export function rulePayload(rule: EventRule) {
  return { id: rule.id, name: rule.name.trim(), description: rule.description.trim(), enabled: rule.enabled, poiIds: [], triggerKinds: rule.triggerKinds, preSeconds: rule.preSeconds, postSeconds: rule.postSeconds, criteria: rule.criteria, uncertainWhen: rule.uncertainWhen }
}
export const ruleChanged = (rule: EventRule, saved?: EventRule) => !saved || JSON.stringify(rulePayload(rule)) !== JSON.stringify(rulePayload(saved))
export function ruleError(rule: EventRule): string {
  if (!rule.name.trim() || rule.name.length > 160) return '名称不能为空，最多 160 字 / Name: 1–160 characters'
  if (!rule.description.trim() || rule.description.length > 1500) return '事件描述不能为空，最多 1500 字 / Description: 1–1500 characters'
  if (!rule.triggerKinds.length || rule.triggerKinds.some(t => !triggerKinds.includes(t as typeof triggerKinds[number]))) return '请选择有效的触发类型 / Select valid triggers'
  if (![rule.preSeconds, rule.postSeconds].every(n => Number.isFinite(n) && n >= 0 && n <= 60) || rule.preSeconds + rule.postSeconds <= 0) return '前后窗口各为 0–60 秒，合计必须大于 0 / Invalid time window'
  if ([rule.criteria, rule.uncertainWhen].some(list => list.length > 10 || list.some(s => !s.trim() || s.length > 500))) return '高级条件最多 10 条，每条 1–500 字 / Invalid conditions'
  return ''
}
export function bindingError(rule: EventRule, ids: string[], pois: RulePoi[]) {
  if (ids.some(id => !pois.some(p => p.id === id))) return 'POI 绑定已失效，请重新选择 / Missing POI binding'
  const targets = pois.filter(p => !ids.length || ids.includes(p.id))
  if (!targets.some(p => rule.triggerKinds.some(t => p.kind === 'line' ? t === 'line.cross' : t !== 'line.cross'))) return '没有匹配触发类型的 POI / No compatible POI'
  return ''
}
export function parseRuleImport(text: string): EventRule[] {
  const trimmed = text.trim()
  if (!trimmed) throw new Error('请输入规则 / Enter rules')
  if (!trimmed.startsWith('[') && !trimmed.startsWith('{')) return trimmed.split('\n').map(s => s.trim()).filter(Boolean).map(newRule)
  const parsed: unknown = JSON.parse(trimmed)
  const raw = Array.isArray(parsed) ? parsed : (parsed as { rules?: unknown; events?: unknown })?.rules ?? (parsed as { events?: unknown })?.events
  if (!Array.isArray(raw) || !raw.length) throw new Error('JSON 必须包含非空 rules 或 events 数组 / Missing rules array')
  const ids = new Set<string>()
  return raw.map((value: unknown, index) => {
    if (!value || typeof value !== 'object') throw new Error(`Invalid rule ${index + 1}`)
    const item = value as Record<string, unknown>
    if (typeof item.description !== 'string') throw new Error(`Rule ${index + 1}: description required`)
    const rule = newRule(item.description)
    for (const key of ['name', 'id'] as const) {
      if (item[key] !== undefined) { if (typeof item[key] !== 'string') throw new Error(`Invalid ${key}`); rule[key] = item[key] }
    }
    if (!/^[A-Za-z0-9_-]{1,80}$/.test(rule.id) || ids.has(rule.id)) throw new Error(`Invalid or duplicate ID: ${rule.id}`)
    ids.add(rule.id)
    for (const key of ['criteria', 'uncertainWhen', 'triggerKinds', 'poiIds'] as const) {
      if (item[key] !== undefined) {
        if (!Array.isArray(item[key]) || item[key].some(s => typeof s !== 'string')) throw new Error(`Invalid ${key}`)
        rule[key] = item[key] as string[]
      }
    }
    for (const key of ['preSeconds', 'postSeconds'] as const) {
      if (item[key] !== undefined) { if (typeof item[key] !== 'number') throw new Error(`Invalid ${key}`); rule[key] = item[key] }
    }
    if (item.enabled !== undefined) { if (typeof item.enabled !== 'boolean') throw new Error('Invalid enabled'); rule.enabled = item.enabled }
    const error = ruleError(rule)
    if (error) throw new Error(`${index + 1}: ${error}`)
    return rule
  })
}
