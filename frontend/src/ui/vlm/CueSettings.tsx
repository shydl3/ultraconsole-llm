import { useConsoleStore } from '../../store/useConsoleStore'
import { useT } from '../../i18n'
import { CUE_DEFAULTS } from '../../utils/vlm'
import styles from './CueSettings.module.css'

const FIELDS = [
  ['minOnRatio', 0.01],
  ['minR', 1],
  ['minRG', 1],
  ['minRB', 1],
  ['minOnSec', 0.1],
  ['minOffSec', 0.1],
  ['sampleFps', 1],
] as const

export function CueSettings() {
  const t = useT()
  const eventsConfig = useConsoleStore((s) => s.eventsConfig)
  const setEventsConfig = useConsoleStore((s) => s.setEventsConfig)
  const cues = eventsConfig.cues || []
  if (!cues.length) return null

  function update(id: string, key: string, value: string) {
    const trimmed = value.trim()
    const num = trimmed === '' ? NaN : Number(trimmed)
    setEventsConfig({ cues: cues.map(c => c.id === id ? { ...c, [key]: Number.isFinite(num) ? num : undefined } : c) })
  }

  return <details className={styles.wrap}>
    <summary>{t('vlmCue.title')}</summary>
    <p className={styles.hint}>{t('vlmCue.hint')}</p>
    {cues.map(cue => <fieldset key={cue.id} className={styles.cue}>
      <legend>{cue.name || cue.id}</legend>
      <div className={styles.grid}>
        {FIELDS.map(([key, step]) => <label key={key} className={styles.field}>
          <span>{t(`vlmCue.${key}`)}</span>
          <input type="number" step={step} value={cue[key] ?? CUE_DEFAULTS[key]}
            onChange={e => update(cue.id, key, e.target.value)} />
        </label>)}
      </div>
    </fieldset>)}
  </details>
}
