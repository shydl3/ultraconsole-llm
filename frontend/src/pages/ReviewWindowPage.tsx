import { useEffect } from 'react'
import { useT } from '../i18n'
import { ReviewDialog } from '../ui/review/ReviewDialog'
import type { ReviewTarget } from '../ui/review/useReviewData'

/** Standalone document rendered inside the detached review window (?review=kind:id). */
export function ReviewWindowPage({ kind, id }: { kind: 'vlm' | 'video'; id: string }) {
  const t = useT()
  const target: ReviewTarget = kind === 'video' ? { kind, id, name: '' } : { kind, id }
  useEffect(() => { document.title = `${t('review.title')} · UltraConsole` }, [t])
  useEffect(() => {
    // Script-opened windows may resize themselves; fill the work area like a
    // maximized window for browsers that ignore the initial size features.
    if (!window.opener) return
    const screenArea = window.screen as Screen & { availLeft?: number; availTop?: number }
    try {
      window.moveTo(Math.max(0, screenArea.availLeft ?? 0), Math.max(0, screenArea.availTop ?? 0))
      window.resizeTo(Math.max(1024, screenArea.availWidth), Math.max(700, screenArea.availHeight))
    } catch {
      /* The window keeps the size requested by window.open. */
    }
  }, [])
  return <ReviewDialog target={target} standalone onClose={closeReviewWindow} />
}

function closeReviewWindow() {
  window.close()
  // A directly opened link cannot close itself; return to the console instead.
  window.setTimeout(() => { if (!window.closed) window.location.assign(window.location.pathname) }, 60)
}
