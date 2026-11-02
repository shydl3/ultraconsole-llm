import type { RuleSnapshot } from './eventRules'
import type { VlmResult } from './vlm'

export type LibraryKind = 'vlm' | 'video'
export type LibraryItem = {
  kind: LibraryKind; id: string; name: string; originalName: string; state: string
  createdAt: number; sizeBytes: number | null; resultCount: number | null; active: boolean
}
export type LibraryList = {
  items: LibraryItem[]; total: number; counts: Record<LibraryKind, number>; videoDirectory: string
}
export type LibraryDetail = {
  ruleSnapshot?: RuleSnapshot
  item: LibraryItem; metadata: Record<string, unknown>; results: VlmResult[]; total: number
  videos: { id: string; name: string; url: string }[]
}
export const libraryKey = (item: LibraryItem) => `${item.kind}/${item.id}`
export const libraryPath = (item: LibraryItem) => `/${item.kind}/${encodeURIComponent(item.id)}`
export function formatBytes(value: number | null) {
  if (value == null) return '—'
  const index = Math.min(3, Math.max(0, Math.floor(Math.log(Math.max(1, value)) / Math.log(1024))))
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${['B', 'KiB', 'MiB', 'GiB'][index]}`
}
