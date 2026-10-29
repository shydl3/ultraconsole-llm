import { useT } from '../../i18n'
import type { RuleSnapshot } from '../../utils/eventRules'
import styles from './EventRulesEditor.module.css'

export function RuleSnapshotView({ snapshot }: { snapshot: RuleSnapshot }) {
  const t = useT()
  return <details className={styles.snapshot}><summary>{t('eventRules.snapshot')} · {snapshot.rules.length}</summary>
    {snapshot.rules.map(rule => <article key={rule.id}>
      <strong>{rule.name} · {rule.version == null ? t('eventRules.legacy') : `v${rule.version}`}</strong>
      <p>{rule.event.description}</p>
      <p>{rule.event.triggerKinds?.map(kind => t(`eventRules.${kind}`)).join(' / ')} · −{rule.event.preSeconds}s / +{rule.event.postSeconds}s</p>
      <p>POI: {rule.event.poiIds?.length ? rule.event.poiIds.join(', ') : t('eventRules.allPoi')}</p>
      {!!rule.event.criteria?.length && <div>{t('eventRules.criteria')}<ul>{rule.event.criteria.map((s, i) => <li key={i}>{s}</li>)}</ul></div>}
      {!!rule.event.uncertainWhen?.length && <div>{t('eventRules.uncertainWhen')}<ul>{rule.event.uncertainWhen.map((s, i) => <li key={i}>{s}</li>)}</ul></div>}
    </article>)}
  </details>
}
