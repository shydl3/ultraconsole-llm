import { ConsolePage } from './pages/ConsolePage'
import { LibraryPage } from './ui/library/LibraryPage'
import { useUiStore } from './store/useUiStore'

export default function App() {
  const view = useUiStore((s) => s.view)
  return view === 'library' ? <LibraryPage /> : <ConsolePage />
}
