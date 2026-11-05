import { describe, expect, it } from 'vitest'
import { activeVlm, CUE_DEFAULTS, parseVlmEvents, vlmCues, vlmPois, type VlmCondition, type VlmResult } from './vlm'

describe('VLM request construction', () => {
  it('preserves independent descriptions and explicit JSON criteria', () => {
    expect(parseVlmEvents(' event one \n\nevent two')).toEqual([
      { id: 'event_1', description: 'event one' }, { id: 'event_2', description: 'event two' },
    ])
    expect(parseVlmEvents('{"events":[{"id":"a","description":"test","criteria":["visible"]}]}')[0].criteria).toEqual(['visible'])
    expect(() => parseVlmEvents('{"events":[]}')).toThrow()
    expect(() => parseVlmEvents(' ')).toThrow()
  })
  it('converts displayed geometry without changing normalized coordinates', () => {
    expect(vlmPois({ lines: [{ id: 'l', a: { x: .2, y: .3 }, b: { x: .8, y: .9 } }], zones: [] })[0]).toMatchObject({
      id: 'l', kind: 'line', points: [[.2, .3], [.8, .9]], anchor: 'bbox_center',
    })
    expect(activeVlm('cancelling')).toBe(true)
    expect(activeVlm('partial')).toBe(false)
  })
  it('maps cue thresholds and line entry direction', () => {
    const line = vlmPois({ lines: [{ id: 'l', a: { x: .1, y: .1 }, b: { x: .9, y: .9 }, entryDirection: 'positive_to_negative' }], zones: [] })[0]
    expect(line.entryDirection).toBe('positive_to_negative')
    const cue = vlmCues({ lines: [], zones: [], cues: [{ id: 'c', name: 'C1', polygon: [{ x: .2, y: .2 }, { x: .8, y: .2 }, { x: .8, y: .8 }] }] })[0]
    expect(cue).toMatchObject({ id: 'c', label: 'C1', points: [[.2, .2], [.8, .2], [.8, .8]], minOnRatio: CUE_DEFAULTS.minOnRatio, minR: CUE_DEFAULTS.minR })
  })
  it('carries per-condition evidence and criteria for index mapping', () => {
    const conditions: VlmCondition[] = [
      { index: 0, value: 'yes', evidenceTimeSec: 2.4, frameIndex: 12 },
      { index: 1, value: 'unknown', evidenceTimeSec: null, frameIndex: null },
    ]
    const result: VlmResult = {
      resultId: 'r', trackId: 1, trackSegmentId: 's', poiId: 'gate', triggerSec: 2,
      triggerKind: 'line.cross', eventDefinitionId: 'e', description: 'd', decision: 'uncertain',
      inferenceStatus: 'succeeded', modelDecisionConfidence: null, reason: 'r',
      eventCriteria: ['跨过入口线', '门框红灯亮起'], conditions,
      segments: [{ window: { startSec: 1, endSec: 3 }, conditions }],
    }
    // Criteria text is looked up by condition index for display.
    expect(result.eventCriteria?.[1]).toBe('门框红灯亮起')
    // An unknown verdict carries no evidence time, so the seek button stays hidden.
    expect(result.conditions?.[1].evidenceTimeSec == null).toBe(true)
    // A segment replays its own per-condition verdicts.
    expect(result.segments?.[0].conditions).toHaveLength(2)
  })
})
