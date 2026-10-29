import type { EventsConfig } from '../api/contracts'
import type { VlmParameters } from './vlmParameters'

export type VlmEventDefinition = {
  id: string; description: string; enabled?: boolean; poiIds?: string[]; triggerKinds?: string[]
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
  modelDecision?: string
  previewPath?: string; downgradeReasons?: string[]; qualityWarnings?: string[]; postprocessVersion?: string
  segments?: { previewPath?: string; reason?: string; decision?: string; rawOutputPath?: string; sampleManifest?: string; window: { startSec: number; endSec: number } }[]
}

export const activeVlm = (state: string) => ['queued', 'tracking', 'understanding', 'cancelling'].includes(state)

export function vlmPois(config: EventsConfig) {
  return [
    ...config.lines.map(l => ({ id: l.id, label: l.name || l.id, kind: 'line', points: [[l.a.x, l.a.y], [l.b.x, l.b.y]], anchor: 'bbox_center' })),
    ...config.zones.map(z => ({ id: z.id, label: z.name || z.id, kind: 'zone', points: z.polygon.map(p => [p.x, p.y]), anchor: 'bbox_center' })),
  ]
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
