import { ConsolePage } from './pages/ConsolePage'
import { ReviewWindowPage } from './pages/ReviewWindowPage'
import { LibraryPage } from './ui/library/LibraryPage'
import { useUiStore } from './store/useUiStore'
import { parseReviewTarget } from './utils/reviewWindow'

export default function App() {
  const view = useUiStore((s) => s.view)
  // The detached review window loads the same bundle with ?review=kind:id.
  const review = parseReviewTarget(new URLSearchParams(window.location.search).get('review'))
  if (review) return <ReviewWindowPage kind={review.kind} id={review.id} />
  return view === 'library' ? <LibraryPage /> : <ConsolePage />
}
