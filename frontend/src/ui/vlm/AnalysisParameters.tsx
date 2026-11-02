import { useEffect, useState } from 'react'
import { apiVlm } from '../../api/client'
import { useT } from '../../i18n'
import { numericParameters, parameterDraft, parameterRange, parameterStorageKey, parseParameters, savedParameters, type NumericParameter, type ParameterContract, type ParameterDraft, type VlmParameters } from '../../utils/vlmParameters'
import styles from './VlmPanel.module.css'

export function AnalysisParameters({ disabled, onChange }: { disabled: boolean; onChange: (value: VlmParameters | null) => void }) {
  const t = useT()
  const [contract, setContract] = useState<ParameterContract | null>(null)
  const [draft, setDraft] = useState<ParameterDraft | null>(null)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    void apiVlm<ParameterContract>('/parameters', { signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return
      let saved: string | null = null
      try { saved = localStorage.getItem(parameterStorageKey) } catch { /* Storage can be disabled. */ }
      const initial = savedParameters(saved, value)
      setContract(value)
      setDraft(parameterDraft(initial))
      onChange(parseParameters(parameterDraft(initial), value).value)
    }).catch(e => { if (!controller.signal.aborted) { setError(String(e)); onChange(null) } })
    return () => controller.abort()
  }, [onChange, retry])

  function update(next: ParameterDraft) {
    if (!contract) return
    setDraft(next)
    const { value } = parseParameters(next, contract)
    onChange(value)
    if (value) {
      try { localStorage.setItem(parameterStorageKey, JSON.stringify({ ...value, dryRun: false })) } catch { /* Optional persistence. */ }
    }
  }

  const parsed = draft && contract ? parseParameters(draft, contract) : null
  function input(key: NumericParameter) {
    if (!contract || !draft) return null
    const field = contract.fields[key]
    const invalid = parsed?.invalid.includes(key)
    return <label className={styles.parameterField} key={key}>
      <span>{t(`vlmParameters.${key}`)}</span>
      <input type="number" value={draft[key]} min={field.minimum ?? field.exclusiveMinimum} max={field.maximum ?? field.exclusiveMaximum}
        step={field.multipleOf ?? (field.type === 'integer' ? 1 : 'any')} aria-invalid={invalid || undefined}
        aria-describedby={`vlm-param-${key}-hint`} onChange={e => update({ ...draft, [key]: e.target.value })} />
      <span className={styles.hint} id={`vlm-param-${key}-hint`}>{t(`vlmParameters.${key}Hint`)} {t('vlmParameters.defaultRange', { value: contract.defaults[key], range: parameterRange(field) })}</span>
      {invalid && <span className={styles.error}>{t(key === 'maxFrames' ? 'vlmParameters.invalidFrames' : 'vlmParameters.invalid')}</span>}
    </label>
  }

  return <section className={styles.parameters} aria-label={t('vlmParameters.title')}>
    <div className={styles.toolbar}><strong>{t('vlmParameters.title')}</strong>
      <button type="button" disabled={disabled || !contract} onClick={() => contract && update(parameterDraft(contract.defaults))}>{t('vlmParameters.reset')}</button>
    </div>
    <div className={styles.hint}>{t('vlmParameters.savedHint')}</div>
    {error ? <div role="alert" className={styles.error}>{error} <button type="button" onClick={() => { setError(''); setRetry(value => value + 1) }}>{t('vlmParameters.retry')}</button></div> : !contract && <div role="status">{t('vlmParameters.loading')}</div>}
    {contract && draft && <fieldset className={styles.parameterControls} disabled={disabled}>
      <legend className={styles.hint}>{t('vlmParameters.common')}</legend>
      <label className={styles.parameterField}><span>{t('vlmParameters.modelPath')}</span>
        <select value={draft.modelPath} aria-invalid={parsed?.invalidModel || undefined} aria-describedby="vlm-param-modelPath-hint"
          onChange={e => update({ ...draft, modelPath: e.target.value })}>
          {!contract.models.some(model => model.path === draft.modelPath) && <option value={draft.modelPath} disabled>{draft.modelPath} — {t('vlmParameters.modelUnavailable')}</option>}
          {contract.models.map(model => <option key={model.path} value={model.path} disabled={!model.available}>
            {model.name}{model.path === contract.defaults.modelPath ? ` (${t('vlmParameters.defaultModel')})` : ''}{!model.available ? ` — ${t('vlmParameters.modelUnavailable')}` : ''}
          </option>)}
        </select>
        <span className={styles.modelPath}>{draft.modelPath}</span>
        <span className={styles.hint} id="vlm-param-modelPath-hint">{t('vlmParameters.modelHint')}</span>
        {parsed?.invalidModel && <span role="alert" className={styles.error}>{contract.models.find(model => model.path === draft.modelPath)?.error || t('vlmParameters.modelUnavailableHint')}</span>}
      </label>
      <div className={styles.parameterGrid}>{(['sampleFps', 'maxFrames', 'maxPixels', 'poseFps', 'maxNewTokens'] as const).map(input)}
        <label className={styles.parameterField}><span>{t('vlmParameters.poseDevice')}</span>
          <select value={draft.poseDevice} onChange={e => update({ ...draft, poseDevice: e.target.value as VlmParameters['poseDevice'] })}>
            <option value="cuda">CUDA</option><option value="cpu">CPU</option>
          </select><span className={styles.hint}>{t('vlmParameters.deviceHint')}</span>
        </label>
      </div>
      {parsed?.value && parsed.value.sampleFps > parsed.value.poseFps && <p className={styles.hint}>{t('vlmParameters.rateHint')}</p>}
      {parsed?.value && parsed.value.maxNewTokens < contract.defaults.maxNewTokens && <div className={styles.toolbar}>
        <span className={styles.hint}>{t('vlmParameters.lowOutputBudget')}</span>
        <button type="button" onClick={() => update({ ...draft, maxNewTokens: String(contract.defaults.maxNewTokens) })}>{t('vlmParameters.useOutputBudget')}</button>
      </div>}
      <p className={styles.hint}>{t('vlmParameters.samplingHint')}</p>
      <p className={styles.hint}>{t('vlmParameters.windowHint')}</p>
      <details><summary>{t('vlmParameters.advanced')}</summary>
        <div className={styles.parameterGrid}>{(['conf', 'iou', 'stableSeconds', 'hysteresisPx', 'maxGapSeconds'] as const).map(input)}</div>
      </details>
      <label className={styles.toolbar}><input type="checkbox" checked={draft.dryRun} onChange={e => update({ ...draft, dryRun: e.target.checked })} />{t('vlm.dryRun')}</label>
    </fieldset>}
    {!!parsed?.invalid.length && <p role="alert" className={styles.error}>{t('vlmParameters.fixBeforeStart')}</p>}
  </section>
}

export function ParameterSnapshot({ value }: { value: VlmParameters }) {
  const t = useT()
  return <details className={styles.parameters}><summary>{t('vlmParameters.snapshot')}</summary>
    <dl className={styles.parameterSnapshot}>
      <div className={styles.modelPath}><dt>{t('vlmParameters.modelPath')}</dt><dd>{value.modelPath}</dd></div>
      {numericParameters.map(key => <div key={key}><dt>{t(`vlmParameters.${key}`)}</dt><dd>{value[key]}</dd></div>)}
      <div><dt>{t('vlmParameters.poseDevice')}</dt><dd>{value.poseDevice.toUpperCase()}</dd></div>
      <div><dt>{t('vlm.dryRun')}</dt><dd>{value.dryRun ? t('vlmParameters.yes') : t('vlmParameters.no')}</dd></div>
    </dl>
  </details>
}
