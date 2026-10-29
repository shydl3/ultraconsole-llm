import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { ConsolePage } from './pages/ConsolePage'
import { LibraryPage } from './ui/library/LibraryPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {window.location.pathname.replace(/\/$/, '') === '/library' ? <LibraryPage /> : <ConsolePage />}
  </StrictMode>,
)
