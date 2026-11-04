import type { EventsConfig } from '../api/contracts'
import type { VlmParameters } from './vlmParameters'

export type VlmEventDefinition = {
  id: string; description: string; enabled?: boolean; poiIds?: string[]; cueIds?: string[]; triggerKinds?: string[]
  preSeconds?: number; postSeconds?: number; criteria?: string[]; uncertainWhen?: string[]
}
export type VlmRun = {
  runId: string; videoId: string; fileName?: string; state: string; error?: string
  progress?: { sampledFrames?: number; candidateCount?: number; trackingFraction?: number; completedRequests?: number; totalRequests?: number; failedRequests?: number }
  summary?: { resultCount: number; reason?: string; dryRun?: boolean }
  parameters?: VlmParameters
}
export type VlmResult = {
  resultId: string; trackId: number; trackSegmentId: string; poiId: string; triggerSec: number
  triggerKind: string; eventDefinitionId: string; description: string; decision: string
  inferenceStatus: string; modelDecisionConfidence: number | null; reason: string; error?: string
  modelDecision?: string; videoDescription?: string | null; deterministicDecision?: string
  cueEvidence?: { cueIds: string[]; state: string; onsetSec: number | null }
  previewPath?: string; downgradeReasons?: string[]; qualityWarnings?: string[]; postprocessVersion?: string
  segments?: { previewPath?: string; reason?: string; videoDescription?: string | null; decision?: string; rawOutputPath?: string; sampleManifest?: string; window: { startSec: number; endSec: number } }[]
}

export const activeVlm = (state: string) => ['queued', 'tracking', 'cues', 'understanding', 'cancelling'].includes(state)

export const CUE_DEFAULTS = { minOnRatio: 0.06, minR: 120, minRG: 50, minRB: 50, minOnSec: 0.2, minOffSec: 0.2, sampleFps: 10 }

export type VlmPoi = {
  id: string; label: string; kind: string; points: number[][]; anchor: string
  entryDirection?: 'positive_to_negative' | 'negative_to_positive' | null
}

export function vlmPois(config: EventsConfig): VlmPoi[] {
  return [
    ...config.lines.map(l => ({ id: l.id, label: l.name || l.id, kind: 'line', points: [[l.a.x, l.a.y], [l.b.x, l.b.y]], anchor: 'bbox_center', entryDirection: l.entryDirection ?? null })),
    ...config.zones.map(z => ({ id: z.id, label: z.name || z.id, kind: 'zone', points: z.polygon.map(p => [p.x, p.y]), anchor: 'bbox_center' })),
  ]
}

export function vlmCues(config: EventsConfig) {
  return (config.cues || []).map(c => ({
    id: c.id, label: c.name || c.id, points: c.polygon.map(p => [p.x, p.y]),
    minOnRatio: c.minOnRatio ?? CUE_DEFAULTS.minOnRatio,
    minR: c.minR ?? CUE_DEFAULTS.minR,
    minRG: c.minRG ?? CUE_DEFAULTS.minRG,
    minRB: c.minRB ?? CUE_DEFAULTS.minRB,
    minOnSec: c.minOnSec ?? CUE_DEFAULTS.minOnSec,
    minOffSec: c.minOffSec ?? CUE_DEFAULTS.minOffSec,
    sampleFps: c.sampleFps ?? CUE_DEFAULTS.sampleFps,
  }))
}

export function parseVlmEvents(text: string): VlmEventDefinition[] {
  const trimmed = text.trim()
  if (!trimmed) throw new Error('请输入至少一条事件描述 / Enter an event description')
  if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
    const raw = JSON.parse(trimmed)
    const events: unknown = Array.isArray(raw) ? raw : raw.events
    if (!Array.isArray(events) || !events.length) throw new Error('JSON must contain a nonempty events array')
    for (const event of events) {
      if (!event || typeof event.id !== 'string' || typeof event.description !== 'string' || !event.description.trim()) {
        throw new Error('Each event requires id and description')
      }
    }
    return events as VlmEventDefinition[]
  }
  return trimmed.split('\n').map(s => s.trim()).filter(Boolean).map((description, index) => ({ id: `event_${index + 1}`, description }))
}
