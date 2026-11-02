import { useCallback, useEffect, useRef, useState } from 'react'
import { apiVlm, apiVlmMedia } from '../../api/client'
import type { EventsConfig } from '../../api/contracts'
import { useT } from '../../i18n'
import { activeVlm, vlmPois, type VlmResult, type VlmRun } from '../../utils/vlm'
import styles from './VlmPanel.module.css'
import { EventRulesEditor, type RulesEditorHandle, type RulesEditorState } from './EventRulesEditor'
import { RuleSnapshotView } from './RuleSnapshotView'
import { QualityWarnings } from './QualityWarnings'
import { ModelExplanation } from './ModelExplanation'
import type { RuleSnapshot } from '../../utils/eventRules'
import { AnalysisParameters, ParameterSnapshot } from './AnalysisParameters'
import type { VlmParameters } from '../../utils/vlmParameters'

function EvidenceImage({ runId, path }: { runId: string; path: string }) {
  const [url, setUrl] = useState('')
  useEffect(() => {
    let alive = true
    let resource = ''
    void apiVlmMedia(`/runs/${runId}/artifacts/${path}`).then(value => {
      resource = value
      if (alive) setUrl(value)
      else if (value.startsWith('blob:')) URL.revokeObjectURL(value)
    }).catch(() => { /* The result and source video remain available. */ })
    return () => { alive = false; if (resource.startsWith('blob:')) URL.revokeObjectURL(resource) }
  }, [runId, path])
  return url ? <img src={url} alt="Sampled target evidence" /> : null
}

export function VlmPanel({ file, eventsConfig, basicRunning, onBusyChange }: {
  file: File | null; eventsConfig: EventsConfig; basicRunning: boolean; onBusyChange: (busy: boolean) => void
}) {
  const t = useT()
  const rulesRef = useRef<RulesEditorHandle>(null)
  const [rulesState, setRulesState] = useState<RulesEditorState>({ dirty: false, enabled: 0, ready: false, busy: false })
  const [snapshot, setSnapshot] = useState<RuleSnapshot | null>(null)
  const [runId, setRunId] = useState('')
  const [runs, setRuns] = useState<VlmRun[]>([])
  const [run, setRun] = useState<VlmRun | null>(null)
  const [results, setResults] = useState<VlmResult[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [parameters, setParameters] = useState<VlmParameters | null>(null)
  const [expanded, setExpanded] = useState('')
  const [source, setSource] = useState('')
  const videoRef = useRef<HTMLVideoElement>(null)
  const uploadRef = useRef<{ file: File; videoId: string } | null>(null)
  const isBusy = submitting || runs.some(r => activeVlm(r.state))

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const list = await apiVlm<VlmRun[]>('/runs', { signal })
    if (signal?.aborted) return
    setRuns(list)
    onBusyChange(submitting || list.some(r => activeVlm(r.state)))
    if (runId) {
      const [current, page, rules] = await Promise.all([
        apiVlm<VlmRun>(`/runs/${runId}`, { signal }),
        apiVlm<{ items: VlmResult[]; total: number }>(`/runs/${runId}/results?offset=${offset}&limit=20`, { signal }),
        apiVlm<RuleSnapshot>(`/runs/${runId}/rules`, { signal }),
      ])
      if (signal?.aborted) return
      setSnapshot(rules)
      setRun(current)
      setResults(page.items)
      setTotal(page.total)
    }
  }, [runId, offset, onBusyChange, submitting])

  useEffect(() => {
    let alive = true
    const controller = new AbortController()
    let timer: number
    const poll = async () => {
      try { if (alive) await refresh(controller.signal) } catch (e) { if (alive) setError(String(e)) }
      if (alive) timer = window.setTimeout(() => void poll(), 2500)
    }
    void poll()
    return () => { alive = false; controller.abort(); window.clearTimeout(timer) }
  }, [refresh])

  useEffect(() => {
    if (!run?.videoId) return
    let alive = true
    let resource = ''
    void apiVlmMedia(`/videos/${run.videoId}/source`).then(url => {
      resource = url
      if (alive) setSource(url)
      else if (url.startsWith('blob:')) URL.revokeObjectURL(url)
    }).catch(e => { if (alive) setError(String(e)) })
    return () => { alive = false; if (resource.startsWith('blob:')) URL.revokeObjectURL(resource) }
  }, [run?.videoId])

  async function start() {
    if (!file || !parameters) return
    setError('')
    setSubmitting(true)
    onBusyChange(true)
    try {
      if (!rulesRef.current) throw new Error(t('eventRules.loading'))
      const selection = await rulesRef.current.prepare()
      let videoId = uploadRef.current?.file === file ? uploadRef.current.videoId : ''
      if (!videoId) {
        const form = new FormData()
        form.append('file', file)
        const video = await apiVlm<{ videoId: string }>('/videos', { method: 'POST', body: form })
        videoId = video.videoId
        uploadRef.current = { file, videoId }
      }
      const created = await apiVlm<{ runId: string }>('/runs/from-rules', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ videoId, pois: vlmPois(eventsConfig), ...selection, ...parameters }),
      })
      setRunId(created.runId)
      setRun(null)
      setResults([])
      setOffset(0)
      setTotal(0)
      setExpanded('')
      const list = await apiVlm<VlmRun[]>('/runs')
      setRuns(list)
    } catch (e) {
      setError(String(e))
      onBusyChange(false)
    } finally { setSubmitting(false) }
  }

  async function cancel() {
    if (!runId) return
    try { await apiVlm(`/runs/${runId}/cancel`, { method: 'POST' }); await refresh() }
    catch (e) { setError(String(e)) }
  }

  const pois = vlmPois(eventsConfig)
  return <section className={styles.panel} aria-label={t('vlm.title')}>
    <strong>{t('vlm.title')}</strong>
    <div className={styles.hint}>{t('vlm.hint')}</div>
    <EventRulesEditor ref={rulesRef} pois={pois} onStateChange={setRulesState} />
    <AnalysisParameters disabled={isBusy} onChange={setParameters} />
    <div className={styles.toolbar}>
      <button disabled={!file || !parameters || !pois.length || basicRunning || isBusy || rulesState.busy || !rulesState.ready} onClick={() => void start()}>{submitting ? t('vlm.uploading') : rulesState.dirty ? t('eventRules.saveAndStart') : t('vlm.start')}</button>
      <span className={styles.hint}>{pois.length} POI · {file?.name || t('vlm.chooseVideo')}</span>
    </div>
    <div className={styles.toolbar}>
      <label>{t('vlm.history')} <select value={runId} onChange={e => {
        setRunId(e.target.value); setSnapshot(null); setOffset(0); setResults([]); setRun(null); setTotal(0); setExpanded(''); setError('')
      }}><option value="">—</option>{runs.map(r => <option key={r.runId} value={r.runId}>{r.fileName || r.runId} · {t(`vlm.${r.state}`)} · {r.runId.slice(-6)}</option>)}</select></label>
      {run && activeVlm(run.state) && <button onClick={() => void cancel()} disabled={run.state === 'cancelling'}>{t('vlm.cancel')}</button>}
    </div>
    {run && <div role="status">
      {t(`vlm.${run.state}`)} · {run.progress?.sampledFrames ?? 0} {t('vlm.frames')} · {run.progress?.candidateCount ?? 0} {t('vlm.candidates')} · {run.progress?.completedRequests ?? 0}/{run.progress?.totalRequests ?? 0} {t('vlm.judgments')}
      {run.state === 'tracking' && <progress max={1} value={run.progress?.trackingFraction ?? 0} />}
      {run.summary?.reason === 'no_candidates' && <p>{t('vlm.noCandidates')}</p>}
      {run.summary?.dryRun && <p>{t('vlm.dryRun')}</p>}
    </div>}
    {(error || run?.error) && <div role="alert" className={styles.error}>{error || run?.error}</div>}
    {run && snapshot && <RuleSnapshotView snapshot={snapshot} />}
    {run?.parameters && <ParameterSnapshot value={run.parameters} />}
    {run && source && <video key={run.videoId} className={styles.video} ref={videoRef} src={source} controls preload="metadata" />}
    {results.map(result => <article className={styles.result} key={result.resultId}>
      <div className={styles.toolbar}>
        <button onClick={() => {
          setExpanded(expanded === result.resultId ? '' : result.resultId)
          if (videoRef.current) videoRef.current.currentTime = Math.max(0, result.triggerSec - 2)
        }}>#{result.trackId} · {result.triggerSec.toFixed(2)}s · {result.poiId}</button>
        <strong className={styles[result.decision]}>{result.inferenceStatus === 'failed' ? t('vlm.failed') : result.inferenceStatus === 'dry_run' ? t('vlm.dryRun') : t(`vlm.${result.decision}`)}</strong>
      </div>
      <div>{result.description}</div>
      <ModelExplanation videoDescription={result.videoDescription} reason={result.reason} />
      {result.modelDecision && result.modelDecision !== result.decision && <div className={styles.hint}>
        {t('vlm.originalDecision')}: {t(`vlm.${result.modelDecision}`)}
      </div>}
      {result.modelDecisionConfidence != null && <div className={styles.hint}>{t('vlm.confidence')}: {(result.modelDecisionConfidence * 100).toFixed(0)}%</div>}
      <div className={styles.hint}><QualityWarnings warnings={result.qualityWarnings} /></div>
      {result.downgradeReasons?.length ? <div className={styles.hint}>{t('vlm.review')}: {result.downgradeReasons.join(', ')}</div> : null}
      {result.error && <div className={styles.error}>{result.error}</div>}
      {expanded === result.resultId && (result.segments || []).map((segment, index) => <div key={index}>
        <p>{segment.window.startSec.toFixed(2)}–{segment.window.endSec.toFixed(2)}s</p>
        <ModelExplanation videoDescription={segment.videoDescription} reason={segment.reason} />
        {segment.previewPath && <EvidenceImage runId={runId} path={segment.previewPath} />}
      </div>)}
    </article>)}
    {total > 20 && <div className={styles.toolbar}>
      <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>←</button>
      {offset + 1}–{Math.min(total, offset + 20)} / {total}
      <button disabled={offset + 20 >= total} onClick={() => setOffset(offset + 20)}>→</button>
    </div>}
  </section>
}
