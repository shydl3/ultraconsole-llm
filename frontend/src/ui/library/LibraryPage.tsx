import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { apiLibrary } from '../../api/client'
import { useT } from '../../i18n'
import { useLocaleStore } from '../../i18n/store'
import { formatBytes, libraryKey, libraryPath, type LibraryItem, type LibraryKind, type LibraryList } from '../../utils/library'
import { LibraryDetail } from './LibraryDetail'
import styles from './LibraryPage.module.css'

function Dialog({ title, children, onClose, busy }: { title: string; children: ReactNode; onClose: () => void; busy: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => { ref.current?.showModal() }, [])
  return <dialog ref={ref} className={styles.dialog} onCancel={e => { e.preventDefault(); if (!busy) onClose() }} aria-labelledby="dialog-title">
    <h2 id="dialog-title">{title}</h2>{children}
  </dialog>
}

export function LibraryPage() {
  const t = useT()
  const locale = useLocaleStore(s => s.locale)
  const setLocale = useLocaleStore(s => s.setLocale)
  const [kind, setKind] = useState<LibraryKind | 'all'>('all')
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [list, setList] = useState<LibraryList | null>(null)
  const [selected, setSelected] = useState<LibraryItem[]>([])
  const [opened, setOpened] = useState<LibraryItem | null>(null)
  const [rename, setRename] = useState<LibraryItem | null>(null)
  const [name, setName] = useState('')
  const [deleting, setDeleting] = useState<LibraryItem[] | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [modalError, setModalError] = useState('')
  const pageSize = 30

  useEffect(() => { document.title = `${t('library.title')} · UltraConsole` }, [t])
  const refresh = useCallback(() => setRevision(value => value + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      const qs = new URLSearchParams({ kind, q: query, offset: String(offset), limit: String(pageSize) })
      void apiLibrary<LibraryList>(`?${qs}`, { signal: controller.signal }).then(value => {
        if (!controller.signal.aborted) { setList(value); setError(''); setLoading(false) }
      }).catch(e => { if (!controller.signal.aborted) { setError(String(e)); setLoading(false) } })
    }, 150)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [kind, query, offset, revision])
  useEffect(() => {
    const timer = window.setInterval(refresh, 15000)
    return () => window.clearInterval(timer)
  }, [refresh])

  function filter(next: LibraryKind | 'all') { setKind(next); setOffset(0); setSelected([]); setLoading(true) }
  function select(item: LibraryItem, checked: boolean) {
    setSelected(previous => checked ? [...previous.filter(p => libraryKey(p) !== libraryKey(item)), item] : previous.filter(p => libraryKey(p) !== libraryKey(item)))
  }
  function openRename(item: LibraryItem) { setRename(item); setName(item.name); setModalError('') }
  function openDelete(items: LibraryItem[]) { setDeleting(items); setModalError('') }
  async function saveName() {
    if (!rename || !name.trim()) return
    setBusy(true); setModalError('')
    try {
      const changed = await apiLibrary<LibraryItem>(libraryPath(rename), { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: name.trim() }) })
      setSelected(previous => previous.map(p => libraryKey(p) === libraryKey(changed) ? changed : p))
      if (opened && libraryKey(opened) === libraryKey(changed)) setOpened(changed)
      setRename(null); setNotice(t('library.renamed')); refresh()
    } catch (e) { setModalError(String(e)) } finally { setBusy(false) }
  }
  async function removeItems() {
    if (!deleting) return
    setBusy(true); setModalError('')
    const outcomes = await Promise.allSettled(deleting.map(item => apiLibrary(libraryPath(item), { method: 'DELETE' })))
    const removed = new Set(deleting.filter((_, index) => outcomes[index].status === 'fulfilled').map(libraryKey))
    const failed = deleting.filter((_, index) => outcomes[index].status === 'rejected')
    setSelected(previous => previous.filter(item => !removed.has(libraryKey(item))))
    if (opened && removed.has(libraryKey(opened))) setOpened(null)
    setNotice(t('library.deleted', { count: removed.size }))
    if (failed.length) {
      setDeleting(failed)
      setModalError(outcomes.flatMap((outcome, index) => outcome.status === 'rejected' ? [`${deleting[index].name}: ${String(outcome.reason)}`] : []).join('\n'))
    } else setDeleting(null)
    if (list && removed.size >= list.items.length && offset > 0) setOffset(Math.max(0, offset - pageSize))
    refresh(); setBusy(false)
  }

  const selectable = list?.items.filter(item => !item.active) || []
  const checked = new Set(selected.map(libraryKey))
  return <div className={styles.page}>
    <header className={styles.header}>
      <div><span className={styles.brand}>ULTRACONSOLE / LIBRARY</span><h1>{t('library.title')}</h1><p className={styles.muted}>{t('library.subtitle')}</p></div>
      <div className={styles.actions}>
        <a href="/" target="ultraconsole-main" className={styles.link}>{t('library.console')}</a>
        <button onClick={() => setLocale(locale === 'zh' ? 'en' : 'zh')}>{locale === 'zh' ? 'EN' : '中文'}</button>
        <button onClick={() => { setLoading(true); refresh() }}>{t('library.refresh')}</button>
      </div>
    </header>
    <div className={styles.tabs} role="group" aria-label={t('library.filter')}>
      {(['all', 'vlm', 'video'] as const).map(tab => <button key={tab} aria-pressed={kind === tab} className={kind === tab ? styles.currentTab : ''} onClick={() => filter(tab)}>
        <span>{t(`library.${tab}`)}</span><strong>{list ? tab === 'all' ? Object.values(list.counts).reduce((a, b) => a + b, 0) : list.counts[tab] : '—'}</strong>
      </button>)}
    </div>
    <div className={styles.toolbar}>
      <input type="search" aria-label={t('library.search')} placeholder={t('library.search')} value={query} onChange={e => { setQuery(e.target.value); setOffset(0); setSelected([]); setLoading(true) }} />
      <span className={styles.muted}>{t('library.selected', { count: selected.length })}</span>
      <button disabled={selected.length !== 1 || busy} onClick={() => openRename(selected[0])}>{t('library.rename')}</button>
      <button className={styles.danger} disabled={!selected.length || busy} onClick={() => openDelete(selected)}>{t('library.deleteSelected')}</button>
    </div>
    {error && <p className={styles.error} role="alert">{error}</p>}
    {notice && <p className={styles.notice} role="status">{notice}</p>}
    <div className={opened ? styles.split : styles.content}>
      <section className={styles.records} aria-label={t('library.records')} aria-busy={loading}>
        <div className={styles.tableWrap}>
          <table>
            <thead><tr>
              <th><input type="checkbox" aria-label={t('library.selectPage')} disabled={!selectable.length || loading} checked={selectable.length > 0 && selectable.every(item => checked.has(libraryKey(item)))} onChange={e => setSelected(e.target.checked ? selectable : [])} /></th>
              <th>{t('library.name')}</th><th>{t('library.state')}</th><th>{t('library.date')}</th><th>{t('library.size')}</th><th>{t('library.actions')}</th>
            </tr></thead>
            <tbody>{list?.items.map(item => <tr key={libraryKey(item)} className={opened && libraryKey(opened) === libraryKey(item) ? styles.activeRow : ''}>
              <td><input type="checkbox" aria-label={`${t('library.select')} ${item.name}`} disabled={item.active || loading} checked={checked.has(libraryKey(item))} onChange={e => select(item, e.target.checked)} /></td>
              <td><button className={styles.name} onClick={() => setOpened(item)}>{item.name}</button><span className={styles.subline}>{t(`library.${item.kind}`)} · {item.id.slice(-12)}</span></td>
              <td><span className={item.active ? styles.running : styles.badge}>{['ready', 'running', 'unknown'].includes(item.state) ? t(`library.${item.state}`) : t(`vlm.${item.state}`)}</span></td>
              <td className={styles.date}>{new Date(item.createdAt * 1000).toLocaleString()}</td>
              <td>{item.kind === 'video' ? formatBytes(item.sizeBytes) : item.resultCount == null ? '—' : t('library.resultCount', { count: item.resultCount })}</td>
              <td><div className={styles.rowActions}>
                <button onClick={() => setOpened(item)}>{t('library.open')}</button>
                <button onClick={() => openRename(item)}>{t('library.rename')}</button>
                <button className={styles.danger} disabled={item.active || busy} title={item.active ? t('library.activeProtected') : t('library.delete')} onClick={() => openDelete([item])}>{t('library.delete')}</button>
              </div></td>
            </tr>)}</tbody>
          </table>
        </div>
        {loading && <p className={styles.empty}>{t('library.loading')}</p>}
        {!loading && !list?.items.length && <p className={styles.empty}>{t('library.empty')}</p>}
        <div className={styles.pager}>
          <button disabled={!offset || loading} onClick={() => { setOffset(Math.max(0, offset - pageSize)); setSelected([]); setLoading(true) }}>←</button>
          <span>{list?.total ? `${offset + 1}–${Math.min(offset + pageSize, list.total)} / ${list.total}` : '0 / 0'}</span>
          <button disabled={!list || offset + pageSize >= list.total || loading} onClick={() => { setOffset(offset + pageSize); setSelected([]); setLoading(true) }}>→</button>
        </div>
      </section>
      {opened && <LibraryDetail key={libraryKey(opened)} item={opened} revision={revision} onClose={() => setOpened(null)} />}
    </div>
    <footer className={styles.footer}>{t('library.storage')} <code>{list?.videoDirectory || 'data/video-previews/'}</code><p>{t('library.retainHint')}</p></footer>
    {rename && <Dialog title={t('library.rename')} busy={busy} onClose={() => setRename(null)}>
      <form onSubmit={e => { e.preventDefault(); void saveName() }}>
        <label>{t('library.name')}<input autoFocus maxLength={160} required value={name} onChange={e => setName(e.target.value)} /></label>
        <p className={styles.muted}>{t('library.renameHint')}</p>
        {modalError && <p role="alert" className={styles.error}>{modalError}</p>}
        <div className={styles.actions}><button type="button" disabled={busy} onClick={() => setRename(null)}>{t('library.cancel')}</button><button type="submit" disabled={busy || !name.trim()}>{busy ? t('library.working') : t('library.save')}</button></div>
      </form>
    </Dialog>}
    {deleting && <Dialog title={t('library.confirmDelete', { count: deleting.length })} busy={busy} onClose={() => setDeleting(null)}>
      <p>{t('library.deleteWarning')}</p>
      <ul className={styles.deleteList}>{deleting.map(item => <li key={libraryKey(item)}>{item.name}</li>)}</ul>
      {modalError && <p role="alert" className={styles.error}>{modalError}</p>}
      <div className={styles.actions}><button disabled={busy} onClick={() => setDeleting(null)}>{t('library.cancel')}</button><button className={styles.danger} disabled={busy} onClick={() => void removeItems()}>{busy ? t('library.working') : t('library.confirm')}</button></div>
    </Dialog>}
  </div>
}
