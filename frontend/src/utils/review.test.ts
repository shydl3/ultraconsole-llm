import { describe, expect, it } from 'vitest'
import {
  decisionKey, decisionTone, EMPTY_FILTER, isFlagged, markerOffset, nearestMarker, reviewQuery,
  savedPageSize, savedPreRoll, seekTarget, type ReviewMarker,
} from './review'

describe('review window helpers', () => {
  it('omits empty filters from the results query', () => {
    expect(reviewQuery(EMPTY_FILTER, 0, 50)).toBe('offset=0&limit=50&sort=time&order=asc')
    expect(reviewQuery({ ...EMPTY_FILTER, decision: 'match', trackId: 7, q: ' 书包 ' }, 50, 50))
      .toBe('offset=50&limit=50&sort=time&order=asc&decision=match&trackId=7&q=%E4%B9%A6%E5%8C%85')
    expect(reviewQuery({ ...EMPTY_FILTER, sort: 'track', order: 'desc', flagged: true }, 0, 20))
      .toBe('offset=0&limit=20&sort=track&order=desc&flagged=true')
  })
  it('jumps slightly before the trigger and never below zero', () => {
    expect(seekTarget(12.5, 2)).toBe(10.5)
    expect(seekTarget(1, 2)).toBe(0)
    expect(seekTarget(3, -5)).toBe(3)
  })
  it('labels failed and dry-run judgments by status first', () => {
    expect(decisionKey({ decision: 'uncertain', inferenceStatus: 'failed' })).toBe('vlm.failed')
    expect(decisionKey({ decision: 'uncertain', inferenceStatus: 'dry_run' })).toBe('vlm.dryRun')
    expect(decisionKey({ decision: 'match', inferenceStatus: 'succeeded' })).toBe('vlm.match')
    expect(decisionTone({ decision: 'uncertain', inferenceStatus: 'failed' })).toBe('failed')
    expect(decisionTone({ decision: 'no_match', inferenceStatus: 'succeeded' })).toBe('no_match')
  })
  it('flags results that carry review reasons or quality warnings', () => {
    expect(isFlagged({ downgradeReasons: ['x'] })).toBe(true)
    expect(isFlagged({ qualityWarnings: ['y'] })).toBe(true)
    expect(isFlagged({ downgradeReasons: [], qualityWarnings: [] })).toBe(false)
  })
  it('finds the last event at or before the playhead', () => {
    const markers: ReviewMarker[] = [
      { resultId: 'a', triggerSec: 1, decision: 'match', trackId: 1 },
      { resultId: 'b', triggerSec: 4, decision: 'no_match', trackId: 1 },
      { resultId: 'c', triggerSec: 9, decision: 'uncertain', trackId: 2 },
    ]
    expect(nearestMarker(markers, 0.5)).toBeUndefined()
    expect(nearestMarker(markers, 4)).toMatchObject({ resultId: 'b' })
    expect(nearestMarker(markers, 8.9)).toMatchObject({ resultId: 'b' })
    expect(nearestMarker(markers, 20)).toMatchObject({ resultId: 'c' })
  })
  it('maps a marker index onto its page offset', () => {
    expect(markerOffset(0, 50)).toBe(0)
    expect(markerOffset(49, 50)).toBe(0)
    expect(markerOffset(50, 50)).toBe(50)
    expect(markerOffset(120, 50)).toBe(100)
  })
  it('falls back to defaults for invalid stored preferences', () => {
    expect(savedPreRoll(null)).toBe(2)
    expect(savedPreRoll('abc')).toBe(2)
    expect(savedPreRoll('5')).toBe(5)
    expect(savedPageSize('50')).toBe(50)
    expect(savedPageSize('7')).toBe(50)
  })
})
