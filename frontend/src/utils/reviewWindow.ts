import type { LibraryKind } from './library'

export type ReviewWindowTarget = { kind: LibraryKind; id: string }

const IDENTIFIER = /^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}$/

/** Work-area size: the closest a script-opened window gets to "maximized". */
export function maximizeFeatures() {
  const screenArea = window.screen as Screen & { availLeft?: number; availTop?: number }
  const width = Math.max(1024, screenArea.availWidth || 1500)
  const height = Math.max(700, screenArea.availHeight || 950)
  const left = Math.max(0, screenArea.availLeft ?? 0)
  const top = Math.max(0, screenArea.availTop ?? 0)
  return `popup,left=${left},top=${top},width=${width},height=${height},resizable=yes,scrollbars=yes`
}

export function reviewWindowUrl(target: ReviewWindowTarget) {
  const url = new URL(window.location.href)
  url.search = ''
  url.hash = ''
  url.searchParams.set('review', `${target.kind}:${target.id}`)
  return url.toString()
}

/**
 * Open a record detail in its own browser window. The URL carries the target so
 * the popup survives a reload; the window name is per record, so one record is
 * never duplicated while different records get separate windows.
 */
export function openReviewWindow(target: ReviewWindowTarget): boolean {
  // `popup` + explicit size opens a real window instead of a tab.
  const handle = window.open(reviewWindowUrl(target), `ultra-review-${target.kind}-${target.id}`, maximizeFeatures())
  if (!handle) return false
  handle.focus()
  return true
}

export function parseReviewTarget(value: string | null): ReviewWindowTarget | null {
  if (!value) return null
  const separator = value.indexOf(':')
  if (separator <= 0) return null
  const kind = value.slice(0, separator)
  const id = value.slice(separator + 1)
  if ((kind !== 'vlm' && kind !== 'video') || !IDENTIFIER.test(id)) return null
  return { kind, id }
}
