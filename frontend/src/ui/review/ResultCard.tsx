import { memo, useEffect, useState } from 'react'
import { apiVlmMedia } from '../../api/client'
import { useT } from '../../i18n'
import { decisionKey, decisionTone, isFlagged } from '../../utils/review'
import type { VlmCondition, VlmResult } from '../../utils/vlm'
import { ModelExplanation } from '../vlm/ModelExplanation'
import { QualityWarnings } from '../vlm/QualityWarnings'
import styles from './ReviewDialog.module.css'

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
  return url ? <img className={styles.evidence} src={url} alt="Sampled target evidence" /> : null
}

function ConditionList({ conditions, criteria, onSeek }: {
  conditions?: VlmCondition[]; criteria?: string[]; onSeek: (sec: number) => void
}) {
  const t = useT()
  if (!conditions?.length) return null
  const label = (value: string) => t(value === 'yes' ? 'vlm.conditionYes' : value === 'no' ? 'vlm.conditionNo' : 'vlm.conditionUnknown')
  return <div className={styles.section}>
    <strong>{t('vlm.conditions')}</strong>
    <ul>{conditions.map(condition => <li key={condition.index}>
      {criteria?.[condition.index] ?? `#${condition.index}`} · {label(condition.value)}
      {condition.evidenceTimeSec != null && <button type="button" onClick={() => onSeek(condition.evidenceTimeSec as number)}>
        {t('vlm.evidenceAt')} {condition.evidenceTimeSec.toFixed(2)}s
      </button>}
    </li>)}</ul>
  </div>
}

export const ResultCard = memo(function ResultCard({ result, runId, ruleName, expanded, active, onToggle, onJump, onSeek }: {
  result: VlmResult
  runId: string
  ruleName?: string
  expanded: boolean
  active: boolean
  onToggle: (resultId: string) => void
  onJump: (resultId: string) => void
  onSeek: (sec: number) => void
}) {
  const t = useT()
  return <article id={`result-${result.resultId}`} className={[styles.card, active ? styles.cardActive : ''].filter(Boolean).join(' ')}>
    <div className={styles.cardHead}>
      <button type="button" className={styles.cardTitle} aria-expanded={expanded} onClick={() => onToggle(result.resultId)}>
        #{result.trackId} · {result.triggerSec.toFixed(2)}s · {result.poiId}
      </button>
      <span className={[styles.badge, styles[decisionTone(result)]].join(' ')}>{t(decisionKey(result))}</span>
    </div>
    <div className={styles.cardMeta}>
      {ruleName && <span className={styles.rule}>{ruleName}</span>}
      <span>{t(`eventRules.${result.triggerKind}`)}</span>
      {isFlagged(result) && <span>{t('review.flagged')}</span>}
      <button type="button" className={styles.jump} onClick={() => onJump(result.resultId)}>{t('review.jump')}</button>
    </div>
    <p className={styles.description}>{result.description}</p>
    {expanded && <>
      {result.deterministicDecision && <div className={styles.section}>
        {t('vlm.deterministic')}: {t(`vlm.${result.deterministicDecision}`)}
        {result.cueEvidence ? ` · ${t('vlm.cueState')}=${result.cueEvidence.state}${result.cueEvidence.onsetSec != null ? ` @${result.cueEvidence.onsetSec.toFixed(2)}s` : ''}` : ''}
      </div>}
      <ConditionList conditions={result.conditions} criteria={result.eventCriteria} onSeek={onSeek} />
      <ModelExplanation videoDescription={result.videoDescription} reason={result.reason} />
      {result.modelDecision && result.modelDecision !== result.decision && <div className={styles.section}>
        {t('vlm.originalDecision')}: {t(`vlm.${result.modelDecision}`)}
      </div>}
      {result.modelDecisionConfidence != null && <div className={styles.section}>
        {t('vlm.confidence')}: {(result.modelDecisionConfidence * 100).toFixed(0)}%
      </div>}
      <div className={styles.section}><QualityWarnings warnings={result.qualityWarnings} /></div>
      {result.downgradeReasons?.length ? <div className={styles.section}>{t('vlm.review')}: {result.downgradeReasons.join(', ')}</div> : null}
      {result.error && <div className={styles.error}>{result.error}</div>}
      {(result.segments || []).map((segment, index) => <div key={index} className={styles.section}>
        <p className={styles.muted}>{segment.window.startSec.toFixed(2)}–{segment.window.endSec.toFixed(2)}s</p>
        <ModelExplanation videoDescription={segment.videoDescription} reason={segment.reason} />
        <ConditionList conditions={segment.conditions} criteria={result.eventCriteria} onSeek={onSeek} />
        {segment.previewPath && <EvidenceImage runId={runId} path={segment.previewPath} />}
      </div>)}
    </>}
  </article>
})
