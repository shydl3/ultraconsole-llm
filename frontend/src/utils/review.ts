import type { VlmResult } from './vlm'

export type ReviewSort = 'time' | 'track'
export type ReviewOrder = 'asc' | 'desc'

export type ReviewFilter = {
  decision?: string
  inferenceStatus?: string
  flagged?: boolean
  eventId?: string
  trackId?: number
  q?: string
  sort: ReviewSort
  order: ReviewOrder
}

export type ReviewMarker = {
  resultId: string
  triggerSec: number
  decision: string
  trackId: number
  inferenceStatus?: string
}

export type ReviewFacets = {
  decision: Record<string, number>
  inferenceStatus: Record<string, number>
  eventDefinitionId: Record<string, number>
  trackId: Record<string, number>
  flagged: number
  total: number
}

export type ReviewPage = {
  items: VlmResult[]
  total: number
  offset: number
  limit: number
  facets: ReviewFacets
  markers: ReviewMarker[]
}

export const REVIEW_DECISIONS = ['match', 'no_match', 'uncertain'] as const
export const PRE_ROLL_OPTIONS = [0, 1, 2, 3, 5] as const
export const REVIEW_PAGE_SIZE = 50

export const EMPTY_FILTER: ReviewFilter = { sort: 'time', order: 'asc' }
export const EMPTY_FACETS: ReviewFacets = {
  decision: {}, inferenceStatus: {}, eventDefinitionId: {}, trackId: {}, flagged: 0, total: 0,
}

/** Serialize the review filters; empty values are omitted so the URL stays readable. */
export function reviewQuery(filter: ReviewFilter, offset: number, limit: number): string {
  const params = new URLSearchParams({
    offset: String(offset), limit: String(limit), sort: filter.sort, order: filter.order,
  })
  if (filter.decision) params.set('decision', filter.decision)
  if (filter.inferenceStatus) params.set('inferenceStatus', filter.inferenceStatus)
  if (filter.flagged) params.set('flagged', 'true')
  if (filter.eventId) params.set('eventId', filter.eventId)
  if (filter.trackId != null) params.set('trackId', String(filter.trackId))
  const query = filter.q?.trim()
  if (query) params.set('q', query)
  return params.toString()
}

/** Jump a little before the trigger time so the event is visible on arrival. */
export function seekTarget(triggerSec: number, preRoll: number): number {
  return Math.max(0, triggerSec - Math.max(0, preRoll))
}

/** Failed/dry-run judgments keep a fallback decision, so label them by status first. */
export function decisionKey(result: { decision: string; inferenceStatus?: string }): string {
  return result.inferenceStatus === 'failed' ? 'vlm.failed'
    : result.inferenceStatus === 'dry_run' ? 'vlm.dryRun'
      : `vlm.${result.decision}`
}

export function decisionTone(result: { decision: string; inferenceStatus?: string }): string {
  return result.inferenceStatus === 'failed' ? 'failed'
    : result.inferenceStatus === 'dry_run' ? 'dry_run'
      : result.decision
}

export function isFlagged(result: Pick<VlmResult, 'downgradeReasons' | 'qualityWarnings'>): boolean {
  return Boolean(result.downgradeReasons?.length || result.qualityWarnings?.length)
}

/** Last event at or before the playhead; used by the follow-playback highlight. */
export function nearestMarker(markers: ReviewMarker[], time: number): ReviewMarker | undefined {
  let found: ReviewMarker | undefined
  for (const marker of markers) {
    if (marker.triggerSec <= time + 0.001) found = marker
  }
  return found
}

export function markerOffset(index: number, size: number): number {
  return Math.max(0, Math.floor(index / Math.max(1, size)) * Math.max(1, size))
}

/** Stable localStorage preference parsing; invalid values fall back silently. */
export function savedPreRoll(value: string | null): number {
  if (!value) return 2
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 && parsed <= 30 ? parsed : 2
}

export function savedPageSize(value: string | null): number {
  const parsed = Number(value)
  return [20, 50, 100].includes(parsed) ? parsed : REVIEW_PAGE_SIZE
}
