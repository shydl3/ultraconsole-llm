import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiVlm } from '../../api/client'
import { useT } from '../../i18n'
import { activeVlm, mergeVlmTasks, vlmProgress, type VlmRun, type VlmTask } from '../../utils/vlm'
import { openReviewWindow } from '../../utils/reviewWindow'
import { ConfirmDialog } from '../primitives/ConfirmDialog'
import styles from './TaskQueue.module.css'

/**
 * Server-driven task queue: polls run state so a page reload re-attaches to
 * queued/running analyses, and merges the uploads still in flight in this tab.
 */
export function TaskQueue({ submissions, version, selectedRunId, onBusyChange, onRunUpdate }: {
  submissions: VlmTask[]
  version: number
  selectedRunId: string
  onBusyChange: (busy: boolean) => void
  onRunUpdate?: (run: VlmRun) => void
}) {
  const t = useT()
  const [runs, setRuns] = useState<VlmRun[]>([])
  const [error, setError] = useState('')
  const [target, setTarget] = useState<VlmRun | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [tick, setTick] = useState(0)
  const selectedRef = useRef(selectedRunId)
  const onRunUpdateRef = useRef(onRunUpdate)
  useEffect(() => { selectedRef.current = selectedRunId }, [selectedRunId])
  useEffect(() => { onRunUpdateRef.current = onRunUpdate }, [onRunUpdate])

  useEffect(() => {
    let alive = true
    const controller = new AbortController()
    let timer: number | undefined
    // Polling lives here so queue updates do not re-render the video column.
    const poll = async () => {
      let active = false
      try {
        const [running, recent] = await Promise.all([
          apiVlm<VlmRun[]>('/runs?active=true', { signal: controller.signal }),
          apiVlm<VlmRun[]>('/runs?limit=20', { signal: controller.signal }),
        ])
        if (!alive) return
        const activeIds = new Set(running.map(run => run.runId))
        const list = [...running, ...recent.filter(run => !activeIds.has(run.runId))]
        setRuns(list)
        setError('')
        active = running.length > 0
        const selected = list.find(run => run.runId === selectedRef.current)
        if (selected) onRunUpdateRef.current?.(selected)
      } catch (e) {
        if (alive) setError(String(e))
      }
      if (!alive) return
      const hidden = document.visibilityState === 'hidden'
      timer = window.setTimeout(() => void poll(), hidden ? 15000 : active ? 2500 : 15000)
    }
    void poll()
    return () => { alive = false; controller.abort(); window.clearTimeout(timer) }
  }, [version, tick])

  const localIds = useMemo(() => new Set(submissions.flatMap(task => task.runId ? [task.runId] : [])), [submissions])
  const tasks = useMemo(() => mergeVlmTasks(submissions, runs).filter(task => task.upload != null || task.error != null
    || (task.runId != null && (localIds.has(task.runId) || (task.run != null && activeVlm(task.run.state))))),
  [submissions, runs, localIds])
  const busy = tasks.some(task => task.upload != null || (task.run != null && activeVlm(task.run.state)))

  useEffect(() => { onBusyChange(busy) }, [busy, onBusyChange])

  const cancel = useCallback(async () => {
    if (!target) return
    setCancelling(true)
    try {
      await apiVlm(`/runs/${target.runId}/cancel`, { method: 'POST' })
      setTarget(null)
      setTick(value => value + 1)
    } catch (e) {
      setError(String(e))
    } finally {
      setCancelling(false)
    }
  }, [target])

  return <section className={styles.queue} aria-label={t('vlm.queue')}>
    <div className={styles.head}>
      <strong>{t('vlm.queue')}</strong>
      {busy && <span className={styles.running}>{t('vlm.queueActive', { count: tasks.filter(task => task.upload || (task.run && activeVlm(task.run.state))).length })}</span>}
    </div>
    {error && <div role="alert" className={styles.error}>{error}</div>}
    {!tasks.length && <p className={styles.hint}>{t('vlm.queueEmpty')}</p>}
    {tasks.length > 0 && <ul className={styles.list}>
      {tasks.map(task => {
        const run = task.run
        const percent = task.upload ? Math.round(task.upload.uploaded / Math.max(1, task.upload.total) * 100)
          : run ? vlmProgress(run).percent : 0
        const isActive = task.upload != null || (run != null && activeVlm(run.state))
        return <li key={task.key} className={styles.task}>
          <div className={styles.row}>
            <span className={styles.name} title={task.name}>{task.name}</span>
            <span className={isActive ? styles.running : styles.badge}>
              {task.upload ? t('vlm.uploading') : task.error ? t('vlm.failed') : t(`vlm.${run?.state ?? 'queued'}`)}
            </span>
            <span className={styles.percent}>{t('vlm.percent', { percent })}</span>
          </div>
          <progress className={styles.bar} max={1} value={percent / 100} aria-label={t('vlm.taskProgress')} />
          <div className={styles.meta}>
            {run ? <>
              <span>{run.progress?.sampledFrames ?? 0} {t('vlm.frames')}</span>
              <span>{run.progress?.candidateCount ?? 0} {t('vlm.candidates')}</span>
              <span>{run.progress?.completedRequests ?? 0}/{run.progress?.totalRequests ?? 0} {t('vlm.judgments')}</span>
              <span>{t('vlm.eventCount')} {run.summary?.resultCount ?? '—'}</span>
              {(run.progress?.segmentCount ?? 0) > 1 && <span>{t('vlm.segment')} {(run.progress?.currentSegment ?? 0) + 1}/{run.progress?.segmentCount}</span>}
            </> : null}
            {task.error && <span className={styles.error}>{task.error}</span>}
            {run?.error && <span className={styles.error}>{run.error}</span>}
          </div>
          <div className={styles.actions}>
            {run && isActive && <button type="button" disabled={run.state === 'cancelling'}
              onClick={() => setTarget(run)}>{run.state === 'cancelling' ? t('vlm.cancelRequested') : t('vlm.cancel')}</button>}
            {task.runId && <button type="button" onClick={() => {
              if (!openReviewWindow({ kind: 'vlm', id: task.runId! })) setError(t('review.popupBlocked'))
            }}>{t('review.open')}</button>}
          </div>
        </li>
      })}
    </ul>}
    {target && <ConfirmDialog title={t('vlm.confirmCancelTitle')} confirmLabel={t('vlm.cancelConfirm')}
      cancelLabel={t('vlm.cancelKeep')} danger busy={cancelling} onConfirm={() => void cancel()} onClose={() => setTarget(null)}>
      <p>{t('vlm.confirmCancelBody')}</p>
      <p className={styles.confirmName}>{target.fileName || target.runId}</p>
    </ConfirmDialog>}
  </section>
}
