import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from 'react'
import { apiVlm } from '../../api/client'
import { useT } from '../../i18n'
import { bindingError, newRule, newRuleId, parseRuleImport, ruleChanged, ruleError, rulePayload, triggerKinds, type EventRule, type RuleLibrary, type RulePoi, type RuleSelection } from '../../utils/eventRules'
import styles from './EventRulesEditor.module.css'

export type RulesEditorState = { dirty: boolean; enabled: number; ready: boolean; busy: boolean }
export type RulesEditorHandle = { prepare: () => Promise<{ revision: number; selections: RuleSelection[] }> }
function Dialog({ title, children, close }: { title: string; children: ReactNode; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => { ref.current?.showModal() }, [])
  return <dialog ref={ref} onCancel={e => { e.preventDefault(); close() }} className={styles.dialog} aria-label={title}><h3>{title}</h3>{children}</dialog>
}

export const EventRulesEditor = forwardRef<RulesEditorHandle, { pois: RulePoi[]; onStateChange: (state: RulesEditorState) => void }>(function EventRulesEditor({ pois, onStateChange }, ref) {
  const t = useT()
  const [library, setLibrary] = useState<RuleLibrary | null>(null)
  const [rows, setRows] = useState<EventRule[]>([])
  const [bindings, setBindings] = useState<Record<string, string[]>>({})
  const [selected, setSelected] = useState<string[]>([])
  const [query, setQuery] = useState('')
  const [edit, setEdit] = useState<EventRule | null>(null)
  const [bulk, setBulk] = useState<string | null>(null)
  const [importMode, setImportMode] = useState('copy')
  const [pending, setPending] = useState<EventRule[]>([])
  const [error, setError] = useState('')
  const [modalError, setModalError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const [deleting, setDeleting] = useState<string[] | null>(null)
  const saved = new Map(library?.rules.map(row => [row.id, row]))
  const dirty = rows.filter(row => ruleChanged(row, saved.get(row.id)))
  const enabled = rows.filter(row => row.enabled)
  const ready = !!library && enabled.length > 0 && enabled.length <= 30 && enabled.every(row => !ruleError(row) && !bindingError(row, bindings[row.id] || [], pois))

  useEffect(() => {
    const controller = new AbortController()
    void apiVlm<RuleLibrary>('/event-rules', { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { setLibrary(value); setRows(value.rules); setSelected([]); setError('') }
    }).catch(e => { if (!controller.signal.aborted) setError(String(e)) })
    return () => controller.abort()
  }, [reload])
  useEffect(() => { onStateChange({ dirty: dirty.length > 0, enabled: enabled.length, ready, busy }) }, [dirty.length, enabled.length, ready, busy, onStateChange])
  useEffect(() => {
    if (!dirty.length) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty.length])

  function stage(row: EventRule) { setRows(previous => previous.some(r => r.id === row.id) ? previous.map(r => r.id === row.id ? row : r) : [...previous, row]) }
  async function commit(upserts: EventRule[], deletes: string[] = []) {
    if (!library) throw new Error(t('eventRules.loading'))
    for (const row of upserts) { const problem = ruleError(row); if (problem) throw new Error(`${row.name || row.id}: ${problem}`) }
    const response = await apiVlm<RuleLibrary>('/event-rules/batch', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ revision: library.revision, upserts: upserts.map(rulePayload), deletes }) })
    const committed = new Set([...upserts.map(r => r.id), ...deletes])
    const remaining = rows.filter(r => !committed.has(r.id) && ruleChanged(r, saved.get(r.id)))
    const nextRows = response.rules.map(r => remaining.find(p => p.id === r.id) || r)
    nextRows.push(...remaining.filter(r => !response.rules.some(p => p.id === r.id)))
    setLibrary(response); setRows(nextRows); setNotice(t('eventRules.saved')); setError('')
    return { library: response, rows: nextRows }
  }
  async function save(ids?: string[]) {
    setBusy(true); setError('')
    try { await commit(dirty.filter(row => !ids || ids.includes(row.id))) }
    catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  async function prepare() {
    if (!ready) throw new Error(t('eventRules.notReady'))
    setBusy(true); setError('')
    try {
      const current = dirty.length ? await commit(dirty) : { library: library!, rows }
      return { revision: current.library.revision, selections: current.rows.filter(r => r.enabled).map(r => ({ id: r.id, version: r.version, poiIds: bindings[r.id] || [] })) }
    } finally { setBusy(false) }
  }
  useImperativeHandle(ref, () => ({ prepare }))
  function exportRules() {
    const chosen = selected.length ? rows.filter(r => selected.includes(r.id)) : rows
    const blob = new Blob([JSON.stringify({ version: 1, rules: chosen.map(rulePayload) }, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a'); link.href = url; link.download = 'event-rules.json'; link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  async function remove() {
    if (!deleting) return
    setBusy(true); setModalError('')
    try {
      const persisted = deleting.filter(id => saved.has(id))
      const next = persisted.length ? (await commit([], persisted)).rows : rows
      setRows(next.filter(r => !deleting.includes(r.id))); setSelected(previous => previous.filter(id => !deleting.includes(id)))
      setNotice(t('eventRules.deleted')); setDeleting(null)
    } catch (e) { setModalError(String(e)) } finally { setBusy(false) }
  }
  function acceptImport() {
    const mapped = pending.map(row => ({ ...row, id: importMode === 'copy' ? newRuleId() : row.id, version: 0 }))
    setRows(previous => {
      const map = new Map(previous.map(row => [row.id, row]))
      mapped.forEach(row => map.set(row.id, { ...row, poiIds: [] }))
      return [...map.values()]
    })
    setBindings(previous => ({ ...previous, ...Object.fromEntries(mapped.map(row => [row.id, row.poiIds])) }))
    setSelected(mapped.map(row => row.id)); setPending([]); setBulk(null); setNotice(t('eventRules.imported'))
  }
  const filtered = rows.filter(row => `${row.name} ${row.description} ${row.id}`.toLowerCase().includes(query.toLowerCase()))
  return <section className={styles.editor} aria-label={t('eventRules.title')}>
    <div className={styles.toolbar}><strong>{t('eventRules.title')}</strong><span>{t('eventRules.count', { total: rows.length, enabled: enabled.length, dirty: dirty.length })}</span></div>
    <div className={styles.toolbar}>
      <button disabled={busy || !library} onClick={() => { setEdit(newRule()); setModalError('') }}>{t('eventRules.add')}</button>
      <button disabled={busy || !library} onClick={() => { setBulk(''); setPending([]); setModalError('') }}>{t('eventRules.bulk')}</button>
      <label className={styles.import}>{t('eventRules.import')}<input type="file" accept=".json,.txt" disabled={busy || !library} onChange={e => {
        const file = e.target.files?.[0]; e.target.value = ''
        if (file) void file.text().then(text => { setBulk(text); setPending([]); setModalError('') }).catch(err => setError(String(err)))
      }} /></label>
      <button disabled={!rows.length || busy} onClick={exportRules}>{t('eventRules.export')}</button>
      <button disabled={busy} onClick={() => { if (!dirty.length || window.confirm(t('eventRules.discard'))) setReload(n => n + 1) }}>{t('eventRules.reload')}</button>
    </div>
    <div className={styles.toolbar}>
      <input type="search" aria-label={t('eventRules.search')} placeholder={t('eventRules.search')} value={query} onChange={e => setQuery(e.target.value)} />
      <button disabled={!dirty.length || busy} onClick={() => void save()}>{t('eventRules.saveAll')}</button>
      <button disabled={!selected.some(id => dirty.some(r => r.id === id)) || busy} onClick={() => void save(selected)}>{t('eventRules.saveSelected')}</button>
      <button disabled={!selected.length || busy} onClick={() => setRows(previous => previous.map(r => selected.includes(r.id) ? { ...r, enabled: true } : r))}>{t('eventRules.enable')}</button>
      <button disabled={!selected.length || busy} onClick={() => setRows(previous => previous.map(r => selected.includes(r.id) ? { ...r, enabled: false } : r))}>{t('eventRules.disable')}</button>
      <button disabled={!selected.length || busy} onClick={() => { setDeleting(selected); setModalError('') }}>{t('eventRules.deleteSelected')}</button>
    </div>
    <p className={styles.hint}>{t('eventRules.hint')}</p>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {notice && <p role="status" className={styles.notice}>{notice}</p>}
    {!library && !error && <p>{t('eventRules.loading')}</p>}
    <div className={styles.tableWrap}><table><thead><tr>
      <th><input type="checkbox" aria-label={t('eventRules.selectAll')} disabled={busy || !filtered.length} checked={!!filtered.length && filtered.every(r => selected.includes(r.id))} onChange={e => setSelected(e.target.checked ? filtered.map(r => r.id) : [])} /></th>
      <th>{t('eventRules.enabled')}</th><th>{t('eventRules.definition')}</th><th>{t('eventRules.poi')}</th><th>{t('eventRules.state')}</th><th>{t('eventRules.actions')}</th>
    </tr></thead><tbody>{filtered.map(row => {
      const changed = ruleChanged(row, saved.get(row.id))
      const problem = ruleError(row) || (row.enabled && (pois.length || bindings[row.id]?.length) ? bindingError(row, bindings[row.id] || [], pois) : '')
      return <tr key={row.id} data-rule-id={row.id}>
        <td><input type="checkbox" aria-label={`${t('eventRules.select')} ${row.name}`} disabled={busy} checked={selected.includes(row.id)} onChange={e => setSelected(previous => e.target.checked ? [...previous, row.id] : previous.filter(id => id !== row.id))} /></td>
        <td><input type="checkbox" role="switch" aria-label={`${t('eventRules.enabled')} ${row.name}`} disabled={busy} checked={row.enabled} onChange={e => stage({ ...row, enabled: e.target.checked })} /></td>
        <td className={styles.definition}><strong>{row.name || t('eventRules.untitled')}</strong><p>{row.description}</p><span className={styles.hint}>{row.triggerKinds.map(k => t(`eventRules.${k}`)).join(' / ')} · −{row.preSeconds}s / +{row.postSeconds}s</span>{problem && <p className={styles.error}>{problem}</p>}</td>
        <td><details><summary>{bindings[row.id]?.length ? t('eventRules.poiCount', { count: bindings[row.id].length }) : t('eventRules.allPoi')}</summary>
          <button disabled={busy} onClick={() => setBindings(previous => ({ ...previous, [row.id]: [] }))}>{t('eventRules.allPoi')}</button>
          {Array.from(new Set([...pois.map(p => p.id), ...(bindings[row.id] || [])])).map(id => <label className={styles.poi} key={id}><input type="checkbox" disabled={busy} checked={(bindings[row.id] || []).includes(id)} onChange={e => setBindings(previous => ({ ...previous, [row.id]: e.target.checked ? [...(previous[row.id] || []), id] : (previous[row.id] || []).filter(p => p !== id) }))} />{pois.find(p => p.id === id)?.label || id}{!pois.some(p => p.id === id) ? ` (${t('eventRules.missing')})` : ''}</label>)}
        </details></td>
        <td><span className={changed ? styles.dirty : styles.hint}>{!saved.has(row.id) ? t('eventRules.unsaved') : changed ? t('eventRules.modified') : `${t('eventRules.savedState')} · v${row.version}`}</span></td>
        <td><div className={styles.actions}>
          <button disabled={busy} onClick={() => { setEdit(structuredClone(row)); setModalError('') }}>{t('eventRules.edit')}</button>
          <button disabled={busy || !changed} onClick={() => void save([row.id])}>{t('eventRules.save')}</button>
          <button disabled={busy || !changed} onClick={() => { const old = saved.get(row.id); if (old) stage(old); else { setRows(previous => previous.filter(r => r.id !== row.id)); setSelected(previous => previous.filter(id => id !== row.id)) } }}>{t('eventRules.undo')}</button>
          <button disabled={busy} onClick={() => stage({ ...structuredClone(row), id: newRuleId(), name: `${row.name.slice(0, 140)} (${t('eventRules.copy')})`, version: 0 })}>{t('eventRules.copy')}</button>
          <button disabled={busy} onClick={() => { setDeleting([row.id]); setModalError('') }}>{t('eventRules.delete')}</button>
        </div></td>
      </tr>
    })}</tbody></table></div>
    {library && !filtered.length && <p className={styles.hint}>{t('eventRules.empty')}</p>}
    {enabled.length > 30 && <p className={styles.error}>{t('eventRules.limit')}</p>}
    {edit && <Dialog title={t('eventRules.edit')} close={() => setEdit(null)}><form onSubmit={e => { e.preventDefault(); const clean = { ...edit, criteria: edit.criteria.map(s => s.trim()).filter(Boolean), uncertainWhen: edit.uncertainWhen.map(s => s.trim()).filter(Boolean) }; const problem = ruleError(clean); if (problem) { setModalError(problem); return }; stage(clean); setEdit(null) }}>
      <label>{t('eventRules.name')}<input autoFocus required maxLength={160} value={edit.name} onChange={e => setEdit({ ...edit, name: e.target.value })} /></label>
      <label>{t('eventRules.description')}<textarea required maxLength={1500} value={edit.description} onChange={e => setEdit({ ...edit, description: e.target.value })} /></label>
      <label className={styles.poi}><input type="checkbox" checked={edit.enabled} onChange={e => setEdit({ ...edit, enabled: e.target.checked })} />{t('eventRules.enabled')}</label>
      <details><summary>{t('eventRules.advanced')}</summary>
        <fieldset><legend>{t('eventRules.triggers')}</legend>{triggerKinds.map(kind => <label className={styles.poi} key={kind}><input type="checkbox" checked={edit.triggerKinds.includes(kind)} onChange={e => setEdit({ ...edit, triggerKinds: e.target.checked ? [...edit.triggerKinds, kind] : edit.triggerKinds.filter(k => k !== kind) })} />{t(`eventRules.${kind}`)}</label>)}</fieldset>
        <div className={styles.toolbar}>{(['preSeconds', 'postSeconds'] as const).map(key => <label key={key}>{t(`eventRules.${key}`)}<input type="number" min={0} max={60} step={0.1} value={edit[key]} onChange={e => setEdit({ ...edit, [key]: Number(e.target.value) })} /></label>)}</div>
        {(['criteria', 'uncertainWhen'] as const).map(key => <label key={key}>{t(`eventRules.${key}`)}<textarea value={edit[key].join('\n')} onChange={e => setEdit({ ...edit, [key]: e.target.value.split('\n') })} /></label>)}
      </details>
      {modalError && <p role="alert" className={styles.error}>{modalError}</p>}
      <div className={styles.toolbar}><button type="button" onClick={() => setEdit(null)}>{t('eventRules.cancel')}</button><button type="submit">{t('eventRules.apply')}</button></div>
    </form></Dialog>}
    {bulk !== null && <Dialog title={t('eventRules.bulk')} close={() => setBulk(null)}>
      <p className={styles.hint}>{t('eventRules.bulkHint')}</p><textarea aria-label={t('eventRules.bulkText')} value={bulk} onChange={e => { setBulk(e.target.value); setPending([]) }} />
      <button onClick={() => { try { setPending(parseRuleImport(bulk)); setModalError('') } catch (e) { setModalError(String(e)) } }}>{t('eventRules.preview')}</button>
      {pending.length > 0 && <><ul className={styles.importList}>{pending.map(row => <li key={row.id}>{row.name}{rows.some(r => r.id === row.id) ? ` · ${t('eventRules.conflictId')}` : ''}</li>)}</ul>
        <label>{t('eventRules.importMode')}<select value={importMode} onChange={e => setImportMode(e.target.value)}><option value="copy">{t('eventRules.importCopy')}</option><option value="update">{t('eventRules.importUpdate')}</option></select></label>
        <button onClick={acceptImport}>{t('eventRules.addDrafts')}</button></>}
      {modalError && <p role="alert" className={styles.error}>{modalError}</p>}
    </Dialog>}
    {deleting && <Dialog title={t('eventRules.delete')} close={() => { if (!busy) setDeleting(null) }}><p>{t('eventRules.deleteWarning')}</p>
      <ul>{rows.filter(row => deleting.includes(row.id)).map(row => <li key={row.id}>{row.name}</li>)}</ul>
      {modalError && <p role="alert" className={styles.error}>{modalError}</p>}
      <div className={styles.toolbar}><button disabled={busy} onClick={() => setDeleting(null)}>{t('eventRules.cancel')}</button><button disabled={busy} onClick={() => void remove()}>{t('eventRules.confirmDelete')}</button></div>
    </Dialog>}
  </section>
})
