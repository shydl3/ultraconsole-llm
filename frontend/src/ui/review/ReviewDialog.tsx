import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useT } from '../../i18n'
import {
  EMPTY_FILTER, markerOffset, nearestMarker, savedPageSize, savedPreRoll, seekTarget,
  type ReviewFilter, type ReviewMarker,
} from '../../utils/review'
import { activeVlm } from '../../utils/vlm'
import { ParameterSnapshot } from '../vlm/AnalysisParameters'
import { RuleSnapshotView } from '../vlm/RuleSnapshotView'
import { ResultCard } from './ResultCard'
import { ReviewFilters } from './ReviewFilters'
import { ReviewVideo, type ReviewVideoHandle } from './ReviewVideo'
import { useReviewData, type ReviewTarget } from './useReviewData'
import styles from './ReviewDialog.module.css'

const readSort = (): ReviewFilter['sort'] => (localStorage.getItem('review.sort') === 'track' ? 'track' : 'time')
const readOrder = (): ReviewFilter['order'] => (localStorage.getItem('review.order') === 'desc' ? 'desc' : 'asc')

export function ReviewDialog({ target, onClose, standalone = false }: {
  target: ReviewTarget; onClose: () => void; standalone?: boolean
}) {
  const t = useT()
  const dialogRef = useRef<HTMLDialogElement>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const videoRef = useRef<ReviewVideoHandle | null>(null)
  const [filter, setFilter] = useState<ReviewFilter>(() => ({ ...EMPTY_FILTER, sort: readSort(), order: readOrder() }))
  const [offset, setOffset] = useState(0)
  const [pageSize, setPageSize] = useState(() => savedPageSize(localStorage.getItem('review.pageSize')))
  const [preRoll, setPreRoll] = useState(() => savedPreRoll(localStorage.getItem('review.preRoll')))
  const [follow, setFollow] = useState(() => localStorage.getItem('review.follow') === '1')
  const [expanded, setExpanded] = useState('')
  const [focusId, setFocusId] = useState('')
  const [followId, setFollowId] = useState('')
  const data = useReviewData(target, filter, offset, pageSize)
  const isRun = target.kind === 'vlm'

  useEffect(() => {
    // Inside the detached window the dialog is the page itself: non-modal, filling
    // the viewport. In-page callers still get the regular modal.
    const dialog = dialogRef.current
    if (!dialog || dialog.open) return
    if (standalone) dialog.show()
    else dialog.showModal()
    headingRef.current?.focus()
  }, [standalone])
  useEffect(() => { localStorage.setItem('review.pageSize', String(pageSize)) }, [pageSize])
  useEffect(() => { localStorage.setItem('review.preRoll', String(preRoll)) }, [preRoll])
  useEffect(() => { localStorage.setItem('review.follow', follow ? '1' : '0') }, [follow])
  useEffect(() => {
    localStorage.setItem('review.sort', filter.sort)
    localStorage.setItem('review.order', filter.order)
  }, [filter.sort, filter.order])

  const patch = useCallback((next: Partial<ReviewFilter>) => {
    setFilter(previous => ({ ...previous, ...next }))
    setOffset(0)
    setExpanded('')
    setFocusId('')
  }, [])
  const changePageSize = useCallback((size: number) => {
    setPageSize(size)
    setOffset(0)
    setExpanded('')
    setFocusId('')
  }, [])

  const jump = useCallback((marker: ReviewMarker) => {
    const index = data.markers.findIndex(item => item.resultId === marker.resultId)
    if (index >= 0) {
      const nextOffset = markerOffset(index, pageSize)
      if (nextOffset !== offset) setOffset(nextOffset)
    }
    setExpanded(marker.resultId)
    setFocusId(marker.resultId)
    videoRef.current?.seek(seekTarget(marker.triggerSec, preRoll))
  }, [data.markers, pageSize, offset, preRoll])

  const step = useCallback((delta: -1 | 1) => {
    const markers = data.markers
    if (!markers.length) return
    const index = focusId ? markers.findIndex(item => item.resultId === focusId) : -1
    if (index >= 0) {
      jump(markers[Math.min(markers.length - 1, Math.max(0, index + delta))])
      return
    }
    const now = videoRef.current?.getTime() ?? 0
    const marker = delta > 0
      ? markers.find(item => item.triggerSec >= now) ?? markers[0]
      : [...markers].reverse().find(item => item.triggerSec <= now) ?? markers[markers.length - 1]
    jump(marker)
  }, [data.markers, focusId, jump])

  // Follow-playback reads the playhead twice a second and only re-renders when
  // the highlighted event actually changes, so decoding stays in the clear.
  useEffect(() => {
    if (!follow) return
    const timer = window.setInterval(() => {
      const next = nearestMarker(data.markers, videoRef.current?.getTime() ?? 0)?.resultId ?? ''
      setFollowId(previous => (previous === next ? previous : next))
    }, 500)
    return () => window.clearInterval(timer)
  }, [follow, data.markers])

  useEffect(() => {
    if (!focusId) return
    document.getElementById(`result-${focusId}`)?.scrollIntoView({ block: 'nearest' })
  }, [focusId, data.loading])

  const seek = useCallback((sec: number) => { videoRef.current?.seek(sec) }, [])
  const toggle = useCallback((resultId: string) => setExpanded(previous => (previous === resultId ? '' : resultId)), [])
  const jumpTo = useCallback((resultId: string) => {
    const marker = data.markers.find(item => item.resultId === resultId)
    if (marker) jump(marker)
  }, [data.markers, jump])
  const rules = useMemo(() => (data.snapshot?.rules ?? []).map(rule => ({ id: rule.id, name: rule.name })), [data.snapshot])
  const ruleNames = useMemo(() => new Map(rules.map(rule => [rule.id, rule.name])), [rules])
  const activeId = follow && data.items.some(item => item.resultId === followId) ? followId : focusId

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const tag = (event.target as HTMLElement).tagName
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
    const items = data.items
    if (!items.length) return
    const index = items.findIndex(item => item.resultId === (focusId || expanded))
    if (event.key === 'ArrowDown' || event.key === 'j') {
      event.preventDefault()
      const next = items[Math.min(items.length - 1, index + 1)]
      if (next) setFocusId(next.resultId)
    } else if (event.key === 'ArrowUp' || event.key === 'k') {
      event.preventDefault()
      const next = items[index < 0 ? items.length - 1 : Math.max(0, index - 1)]
      if (next) setFocusId(next.resultId)
    } else if (event.key === 'Enter') {
      const marker = data.markers.find(item => item.resultId === (focusId || expanded))
      if (marker) { event.preventDefault(); jump(marker) }
    }
  }

  return <dialog
    ref={dialogRef}
    className={[styles.dialog, standalone ? styles.standalone : ''].filter(Boolean).join(' ')}
    aria-labelledby="review-title"
    onCancel={event => { event.preventDefault(); onClose() }}
  >
    <div className={styles.shell}>
      <div className={styles.header}>
        <div className={styles.headingRow}>
          <h2 ref={headingRef} id="review-title" tabIndex={-1}>{data.name || t('review.title')}</h2>
          {data.run && <span className={styles.stateChip}>{t(`vlm.${data.run.state}`)}</span>}
          <span className={styles.muted}>{target.kind === 'vlm' ? t('library.vlm') : t('library.video')} · {target.id.slice(-12)}</span>
        </div>
        <div className={styles.actions}>
          {isRun && <span className={styles.muted}>{t('review.count', { count: data.total })}</span>}
          <button type="button" onClick={data.reload} disabled={data.loading}>{t('library.refresh')}</button>
          <button type="button" className={styles.close} onClick={onClose} aria-label={t('review.close')}>×</button>
        </div>
      </div>
      <div className={[styles.layout, isRun ? '' : styles.layoutSingle].filter(Boolean).join(' ')}>
        <div className={styles.left}>
          <ReviewVideo
            src={data.videoUrl}
            videoRef={videoRef}
            markers={isRun ? data.markers : []}
            preRoll={preRoll}
            onPreRoll={setPreRoll}
            follow={follow}
            onFollow={setFollow}
            onJump={jump}
            onStep={step}
            transport={isRun}
          />
          {isRun && <div className={styles.playbackBar}>
            {data.playback.state === 'ready'
              ? <span className={styles.hint}>{t('review.playbackReady')}</span>
              : data.playback.state === 'running'
                ? <>
                  <progress max={100} value={data.playback.progress} />
                  <span className={styles.hint}>{t('review.playbackBuilding', { progress: data.playback.progress })}</span>
                </>
                : <>
                  <button type="button" onClick={data.startPlayback}>{data.playback.state === 'failed' ? t('review.playbackRetry') : t('review.buildPlayback')}</button>
                  <span className={styles.hint}>{data.playback.state === 'failed' && data.playback.error ? data.playback.error : t('review.playbackHint')}</span>
                </>}
          </div>}
          {data.error && <p role="alert" className={styles.error}>{data.error}</p>}
          {data.run?.parameters && <ParameterSnapshot value={data.run.parameters} />}
          {data.snapshot && <RuleSnapshotView snapshot={data.snapshot} />}
          {!isRun && data.videoUrl && <a className={styles.download} href={data.videoUrl} download={`${data.name || target.id}.mp4`}>{t('library.download')}</a>}
          {!isRun && data.metadata && <details className={styles.raw}>
            <summary>{t('library.metadata')}</summary><pre>{JSON.stringify(data.metadata, null, 2)}</pre>
          </details>}
        </div>
        {isRun && <div className={styles.right}>
          <ReviewFilters
            filter={filter}
            facets={data.facets}
            rules={rules}
            pageSize={pageSize}
            onChange={patch}
            onPageSize={changePageSize}
          />
          <div className={styles.results} onKeyDown={onKeyDown} tabIndex={0} aria-label={t('review.results')}>
            {data.run && activeVlm(data.run.state) && <p className={styles.hint}>{t('review.live')}</p>}
            {data.loading && !data.items.length && <p className={styles.empty}>{t('review.loading')}</p>}
            {!data.loading && !data.items.length && <p className={styles.empty}>{data.facets.total ? t('review.empty') : t('review.noResults')}</p>}
            <div className={styles.list}>{data.items.map(result => <ResultCard
              key={result.resultId}
              result={result}
              runId={target.id}
              ruleName={ruleNames.get(result.eventDefinitionId)}
              expanded={expanded === result.resultId}
              active={activeId === result.resultId}
              onToggle={toggle}
              onJump={jumpTo}
              onSeek={seek}
            />)}</div>
          </div>
          {data.total > 0 && <div className={styles.pager}>
            <button type="button" disabled={offset === 0 || data.loading} onClick={() => { setOffset(Math.max(0, offset - pageSize)); setExpanded(''); setFocusId('') }}>←</button>
            <span>{offset + 1}–{Math.min(offset + pageSize, data.total)} / {data.total}</span>
            <button type="button" disabled={offset + pageSize >= data.total || data.loading} onClick={() => { setOffset(offset + pageSize); setExpanded(''); setFocusId('') }}>→</button>
          </div>}
        </div>}
      </div>
    </div>
  </dialog>
}
