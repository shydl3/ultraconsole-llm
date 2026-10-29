import { describe, expect, it } from 'vitest'
import { activeVlm, parseVlmEvents, vlmPois } from './vlm'

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
})
