import { useEffect, useState } from 'react'
import { useT } from '../../i18n'
import { REVIEW_DECISIONS, type ReviewFacets, type ReviewFilter } from '../../utils/review'
import styles from './ReviewDialog.module.css'

export function ReviewFilters({ filter, facets, rules, pageSize, onChange, onPageSize }: {
  filter: ReviewFilter
  facets: ReviewFacets
  rules: { id: string; name: string }[]
  pageSize: number
  onChange: (patch: Partial<ReviewFilter>) => void
  onPageSize: (size: number) => void
}) {
  const t = useT()
  const [query, setQuery] = useState(filter.q ?? '')
  const [syncedQuery, setSyncedQuery] = useState(filter.q)
  // Adjust local draft when the parent clears or replaces the filter (no effect round-trip).
  if (syncedQuery !== filter.q) {
    setSyncedQuery(filter.q)
    setQuery(filter.q ?? '')
  }
  useEffect(() => {
    const timer = window.setTimeout(() => {
      if ((filter.q ?? '') !== query.trim()) onChange({ q: query.trim() || undefined })
    }, 300)
    return () => window.clearTimeout(timer)
  }, [query, filter.q, onChange])

  const chips = [
    { key: 'all', label: t('review.all'), count: facets.total, patch: { decision: undefined, inferenceStatus: undefined, flagged: undefined } },
    ...REVIEW_DECISIONS.map(decision => ({
      key: decision, label: t(`vlm.${decision}`), count: facets.decision[decision] ?? 0,
      patch: { decision, inferenceStatus: undefined, flagged: undefined },
    })),
    {
      key: 'failed', label: t('vlm.failed'), count: facets.inferenceStatus.failed ?? 0,
      patch: { decision: undefined, inferenceStatus: 'failed', flagged: undefined },
    },
    {
      key: 'dry_run', label: t('review.dryRun'), count: facets.inferenceStatus.dry_run ?? 0,
      patch: { decision: undefined, inferenceStatus: 'dry_run', flagged: undefined },
    },
    {
      key: 'flagged', label: t('review.flagged'), count: facets.flagged,
      patch: { decision: undefined, inferenceStatus: undefined, flagged: true },
    },
  ] as { key: string; label: string; count: number; patch: Partial<ReviewFilter> }[]
  const current = filter.decision ?? filter.inferenceStatus ?? (filter.flagged ? 'flagged' : 'all')

  const ruleIds = Array.from(new Set([...rules.map(rule => rule.id), ...Object.keys(facets.eventDefinitionId)]))
  const ruleName = new Map(rules.map(rule => [rule.id, rule.name]))
  const tracks = Object.keys(facets.trackId).sort((a, b) => Number(a) - Number(b))
  const filtered = Boolean(filter.decision || filter.inferenceStatus || filter.flagged || filter.eventId || filter.trackId != null || filter.q)

  return <div className={styles.filters}>
    <div className={styles.chips} role="group" aria-label={t('review.kind')}>
      {chips.map(chip => <button
        key={chip.key}
        type="button"
        aria-pressed={current === chip.key}
        className={[styles.chip, current === chip.key ? styles.chipActive : ''].filter(Boolean).join(' ')}
        onClick={() => onChange(chip.patch)}
      >{chip.label}<strong>{chip.count}</strong></button>)}
    </div>
    <div className={styles.fields}>
      <label>{t('review.rule')}
        <select value={filter.eventId ?? ''} onChange={event => onChange({ eventId: event.target.value || undefined })}>
          <option value="">{t('review.allRules')}</option>
          {ruleIds.map(id => <option key={id} value={id}>
            {ruleName.get(id) || id} ({facets.eventDefinitionId[id] ?? 0})
          </option>)}
        </select>
      </label>
      <label>{t('review.track')}
        <select value={filter.trackId ?? ''} onChange={event => onChange({ trackId: event.target.value ? Number(event.target.value) : undefined })}>
          <option value="">{t('review.allTracks')}</option>
          {tracks.map(track => <option key={track} value={track}>{`#${track} (${facets.trackId[track]})`}</option>)}
        </select>
      </label>
      <input type="search" value={query} placeholder={t('review.search')} aria-label={t('review.search')} onChange={event => setQuery(event.target.value)} />
      <label>{t('review.sort')}
        <select value={`${filter.sort}:${filter.order}`} onChange={event => {
          const [sort, order] = event.target.value.split(':') as [ReviewFilter['sort'], ReviewFilter['order']]
          onChange({ sort, order })
        }}>
          <option value="time:asc">{t('review.sortTimeAsc')}</option>
          <option value="time:desc">{t('review.sortTimeDesc')}</option>
          <option value="track:asc">{t('review.sortTrackAsc')}</option>
          <option value="track:desc">{t('review.sortTrackDesc')}</option>
        </select>
      </label>
      <label>{t('review.pageSize')}
        <select value={pageSize} onChange={event => onPageSize(Number(event.target.value))}>
          {[20, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}
        </select>
      </label>
      {filtered && <button type="button" onClick={() => onChange({
        decision: undefined, inferenceStatus: undefined, flagged: undefined,
        eventId: undefined, trackId: undefined, q: undefined,
      })}>{t('review.clearFilters')}</button>}
    </div>
  </div>
}
