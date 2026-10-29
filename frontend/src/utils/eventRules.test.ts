import { describe, expect, it } from 'vitest'
import { bindingError, newRule, parseRuleImport, ruleChanged, rulePayload } from './eventRules'

describe('event rule imports and per-run bindings', () => {
  it('assigns stable independent IDs to text entries and preserves exported definitions', () => {
    const rows = parseRuleImport('事件一\n事件二')
    expect(rows).toHaveLength(2)
    expect(rows[0].id).not.toBe(rows[1].id)
    const exported = JSON.stringify({ rules: rows.map(rulePayload) })
    const imported = parseRuleImport(exported)
    expect(imported.map(rulePayload)).toEqual(rows.map(rulePayload))
    expect(ruleChanged({ ...rows[0], name: '修改名称' }, rows[0])).toBe(true)
    expect(rulePayload({ ...rows[0], version: 3 })).not.toHaveProperty('version')
  })
  it('reads existing advanced JSON without losing criteria or accepting invalid types', () => {
    const raw = { id: 'legacy', description: '检查', criteria: ['条件'], uncertainWhen: ['遮挡'], preSeconds: 2, postSeconds: 8, poiIds: ['old-zone'] }
    const [row] = parseRuleImport(JSON.stringify({ events: [raw] }))
    expect(row.criteria).toEqual(['条件'])
    expect(row.poiIds).toEqual(['old-zone'])
    expect(rulePayload(row).poiIds).toEqual([])
    expect(() => parseRuleImport(JSON.stringify([{ ...raw, enabled: 'false' }]))).toThrow()
    expect(() => parseRuleImport(JSON.stringify([raw, raw]))).toThrow()
  })
  it('does not silently broaden missing POIs or incompatible trigger types', () => {
    const row = newRule('检查')
    const pois = [{ id: 'gate', kind: 'line', label: '门' }]
    expect(bindingError(row, [], pois)).toBe('')
    expect(bindingError(row, ['removed'], pois)).not.toBe('')
    expect(bindingError({ ...row, triggerKinds: ['zone.enter'] }, [], pois)).not.toBe('')
  })
})
