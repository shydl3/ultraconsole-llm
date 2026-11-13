import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiVlm, apiVlmUploadChunked } from '../../api/client'
import type { EventsConfig } from '../../api/contracts'
import { useT } from '../../i18n'
import { vlmCues, vlmPois, type VlmRun, type VlmTask } from '../../utils/vlm'
import styles from './VlmPanel.module.css'
import { EventRulesEditor, type RulesEditorHandle, type RulesEditorState } from './EventRulesEditor'
import { CueSettings } from './CueSettings'
import { RuleSnapshotView } from './RuleSnapshotView'
import type { RuleSnapshot } from '../../utils/eventRules'
import { AnalysisParameters, ParameterSnapshot } from './AnalysisParameters'
import type { VlmParameters } from '../../utils/vlmParameters'
import { openReviewWindow } from '../../utils/reviewWindow'
import { TaskQueue } from './TaskQueue'

const fileKey = (file: File) => `${file.name}:${file.size}:${file.lastModified}`

export function VlmPanel({ file, eventsConfig, basicRunning, onBusyChange }: {
  file: File | null; eventsConfig: EventsConfig; basicRunning: boolean; onBusyChange: (busy: boolean) => void
}) {
  const t = useT()
  const rulesRef = useRef<RulesEditorHandle>(null)
  const [rulesState, setRulesState] = useState<RulesEditorState>({ dirty: false, enabled: 0, ready: false, busy: false })
  const [snapshot, setSnapshot] = useState<RuleSnapshot | null>(null)
  const [batch, setBatch] = useState<File[]>([])
  const [submissions, setSubmissions] = useState<VlmTask[]>([])
  const [queueVersion, setQueueVersion] = useState(0)
  const [queueBusy, setQueueBusy] = useState(false)
  const [runId, setRunId] = useState('')
  const [runs, setRuns] = useState<VlmRun[]>([])
  const [run, setRun] = useState<VlmRun | null>(null)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [activeUpload, setActiveUpload] = useState<{ current: number; total: number; percent: number } | null>(null)
  const [parameters, setParameters] = useState<VlmParameters | null>(null)
  const uploadRef = useRef(new Map<string, string>())
  const submittingRef = useRef(false)
  const queueBusyRef = useRef(false)
  const runIdRef = useRef('')
  const isBusy = submitting || queueBusy

  useEffect(() => { submittingRef.current = submitting }, [submitting])
  useEffect(() => { runIdRef.current = runId }, [runId])

  const files = useMemo(() => {
    const list = file ? [file] : []
    for (const extra of batch) {
      if (!list.some(item => fileKey(item) === fileKey(extra))) list.push(extra)
    }
    return list
  }, [file, batch])

  const handleBusy = useCallback((busy: boolean) => {
    queueBusyRef.current = busy
    setQueueBusy(busy)
    onBusyChange(busy || submittingRef.current)
  }, [onBusyChange])

  // Completed records only; unfinished runs are followed in the task queue.
  useEffect(() => {
    const controller = new AbortController()
    const load = () => void apiVlm<VlmRun[]>('/runs?limit=20&state=completed', { signal: controller.signal })
      .then(list => { if (!controller.signal.aborted) setRuns(list) }).catch(() => {})
    load()
    const timer = window.setInterval(load, 15000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [queueVersion])

  useEffect(() => {
    if (!runId) { setRun(null); setSnapshot(null); return }
    const controller = new AbortController()
    void Promise.all([
      apiVlm<VlmRun>(`/runs/${runId}`, { signal: controller.signal }),
      apiVlm<RuleSnapshot>(`/runs/${runId}/rules`, { signal: controller.signal }),
    ]).then(([current, rules]) => {
      if (controller.signal.aborted) return
      setRun(current)
      setSnapshot(rules)
    }).catch(e => { if (!controller.signal.aborted) setError(String(e)) })
    return () => controller.abort()
  }, [runId])

  const handleRunUpdate = useCallback((current: VlmRun) => {
    if (current.runId === runIdRef.current) setRun(current)
  }, [])

  function addFiles(picked: FileList | null) {
    if (!picked) return
    setBatch(previous => {
      const next = [...previous]
      for (const candidate of Array.from(picked)) {
        if (file && fileKey(candidate) === fileKey(file)) continue
        if (!next.some(item => fileKey(item) === fileKey(candidate))) next.push(candidate)
      }
      return next
    })
  }

  function removeFile(target: File) {
    setBatch(previous => previous.filter(item => fileKey(item) !== fileKey(target)))
  }

  function openRecord(id: string) {
    // Detail pages live in their own browser window; reviewing a task never makes
    // it a saved record and never replaces the console view.
    if (!openReviewWindow({ kind: 'vlm', id })) setError(t('review.popupBlocked'))
  }

  async function start() {
    if (!files.length || !parameters) return
    setError('')
    setSubmitting(true)
    submittingRef.current = true
    onBusyChange(true)
    try {
      if (!rulesRef.current) throw new Error(t('eventRules.loading'))
      const selection = await rulesRef.current.prepare()
      for (const [index, target] of files.entries()) {
        const key = `${fileKey(target)}#${Date.now()}-${index}`
        const progress = { uploaded: 0, total: target.size }
        setActiveUpload({ current: index + 1, total: files.length, percent: 0 })
        setSubmissions(previous => [...previous, { key, name: target.name, upload: progress }])
        try {
          let videoId = uploadRef.current.get(fileKey(target)) ?? ''
          if (!videoId) {
            const video = await apiVlmUploadChunked(target, next => {
              setSubmissions(previous => previous.map(task => task.key === key ? { ...task, upload: next } : task))
              setActiveUpload({ current: index + 1, total: files.length,
                percent: Math.round(next.uploaded / Math.max(1, next.total) * 100) })
            })
            videoId = video.videoId
            uploadRef.current.set(fileKey(target), videoId)
          }
          const created = await apiVlm<{ runId: string }>('/runs/from-rules', {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ videoId, pois: vlmPois(eventsConfig), cues: vlmCues(eventsConfig), ...selection, ...parameters }),
          })
          setSubmissions(previous => previous.map(task => task.key === key
            ? { ...task, runId: created.runId, upload: undefined } : task))
          setQueueVersion(value => value + 1)
        } catch (e) {
          setSubmissions(previous => previous.map(task => task.key === key
            ? { ...task, upload: undefined, error: String(e) } : task))
        }
      }
    } catch (e) {
      setError(String(e))
    } finally {
      submittingRef.current = false
      setSubmitting(false)
      setActiveUpload(null)
      setQueueVersion(value => value + 1)
      onBusyChange(queueBusyRef.current)
    }
  }

  const pois = vlmPois(eventsConfig)
  const cues = vlmCues(eventsConfig).map(c => ({ id: c.id, label: c.label, kind: 'cue' }))
  const uploadLabel = activeUpload && submitting
    ? t('vlm.batchUploading', { current: activeUpload.current, total: activeUpload.total, percent: activeUpload.percent })
    : t('vlm.uploading')
  return <section className={styles.panel} aria-label={t('vlm.title')}>
    <strong>{t('vlm.title')}</strong>
    <div className={styles.hint}>{t('vlm.hint')}</div>
    <EventRulesEditor ref={rulesRef} pois={pois} cues={cues} onStateChange={setRulesState} />
    <CueSettings />
    <AnalysisParameters disabled={isBusy} onChange={setParameters} />
    <div className={styles.files}>
      <div className={styles.toolbar}>
        <strong>{t('vlm.batchTitle')}</strong>
        <label className={styles.fileBtn}>
          <input type="file" multiple disabled={submitting} accept="video/*,.mp4,.mov,.mkv,.avi,.m4v,.webm,.ts,.ps,.dav"
            onChange={e => { addFiles(e.target.files); e.target.value = '' }} />
          {t('vlm.batchAdd')}
        </label>
        <span className={styles.hint}>{t('vlm.batchCount', { count: files.length })}</span>
      </div>
      <div className={styles.hint}>{t('vlm.batchHint')}</div>
      <ul className={styles.fileList}>
        {files.map(item => <li key={fileKey(item)} className={styles.fileRow}>
          <span className={styles.fileName} title={item.name}>{item.name}</span>
          {file && fileKey(item) === fileKey(file)
            ? <span className={styles.hint}>{t('vlm.taskCurrent')}</span>
            : <button type="button" disabled={submitting} onClick={() => removeFile(item)}>{t('vlm.batchRemove')}</button>}
        </li>)}
      </ul>
    </div>
    <div className={styles.toolbar}>
      <button disabled={!files.length || !parameters || !pois.length || basicRunning || submitting || rulesState.busy || !rulesState.ready} onClick={() => void start()}>
        {submitting ? uploadLabel : files.length > 1 ? t('vlm.batchStart', { count: files.length })
          : rulesState.dirty ? t('eventRules.saveAndStart') : t('vlm.start')}
      </button>
      <span className={styles.hint}>{pois.length} POI · {files.length ? files[0].name : t('vlm.chooseVideo')}</span>
    </div>
    <TaskQueue submissions={submissions} version={queueVersion} selectedRunId={runId}
      onBusyChange={handleBusy} onRunUpdate={handleRunUpdate} />
    <div className={styles.toolbar}>
      <label>{t('vlm.history')} <select value={runId} onChange={e => {
        setRunId(e.target.value); setSnapshot(null); setRun(null); setError('')
      }}><option value="">—</option>{runs.map(item => <option key={item.runId} value={item.runId}>{item.fileName || item.runId} · {t(`vlm.${item.state}`)} · {item.runId.slice(-6)}</option>)}</select></label>
    </div>
    {run && <div role="status">
      {t(`vlm.${run.state}`)} · {run.progress?.sampledFrames ?? 0} {t('vlm.frames')} · {run.progress?.candidateCount ?? 0} {t('vlm.candidates')} · {run.progress?.completedRequests ?? 0}/{run.progress?.totalRequests ?? 0} {t('vlm.judgments')}
      {run.summary?.resultCount != null && <span> · {t('vlm.eventCount')} {run.summary.resultCount}</span>}
      {(run.progress?.segmentCount ?? 0) > 1 && <span className={styles.hint}>{' '}{t('vlm.segment')} {(run.progress?.currentSegment ?? 0) + 1}/{run.progress?.segmentCount}</span>}
      {run.summary?.reason === 'no_candidates' && <p>{t('vlm.noCandidates')}</p>}
      {run.summary?.dryRun && <p>{t('vlm.dryRun')}</p>}
    </div>}
    {(error || run?.error) && <div role="alert" className={styles.error}>{error || run?.error}</div>}
    {run && snapshot && <RuleSnapshotView snapshot={snapshot} />}
    {run?.parameters && <ParameterSnapshot value={run.parameters} />}
    <div className={styles.toolbar}>
      <button disabled={!runId} onClick={() => openRecord(runId)}>{t('review.open')}</button>
      <span className={styles.hint}>{run?.summary?.resultCount != null ? t('library.resultCount', { count: run.summary.resultCount }) : t('review.hint')}</span>
    </div>
  </section>
}
