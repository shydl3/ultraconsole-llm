import { useEffect, useMemo, useRef, useState } from 'react'
import { apiGetState, apiListModels, apiSelectEngine, apiSelectModel, apiUpdateParams, apiVlm } from '../../api/client'
import { useT } from '../../i18n'
import { useConsoleStore } from '../../store/useConsoleStore'
import { useVlmModelStore } from '../../store/useVlmModelStore'
import type { Params } from '../../api/contracts'
import { parameterStorageKey, persistModelPath, savedParameters, type ParameterContract } from '../../utils/vlmParameters'
import { Card } from '../primitives/Card'
import { NeoButton } from '../primitives/NeoButton'
import styles from './ControlPanel.module.css'

export function ControlPanel() {
  const t = useT()
  const { engine, params, models, currentModelId, classes } = useConsoleStore()
  const setEngine = useConsoleStore((s) => s.setEngine)
  const setParams = useConsoleStore((s) => s.setParams)
  const setModels = useConsoleStore((s) => s.setModels)
  const setCurrentModel = useConsoleStore((s) => s.setCurrentModel)
  const pushLog = useConsoleStore((s) => s.pushLog)

  const [busy, setBusy] = useState(false)
  const vlmModelPath = useVlmModelStore((s) => s.modelPath)
  const setVlmModelPath = useVlmModelStore((s) => s.setModelPath)
  const [vlmContract, setVlmContract] = useState<ParameterContract | null>(null)
  const [vlmError, setVlmError] = useState('')
  const [vlmRetry, setVlmRetry] = useState(0)

  useEffect(() => {
    // The semantic model lives here so model choice is visible before opening the VLM panel.
    const controller = new AbortController()
    void apiVlm<ParameterContract>('/parameters', { signal: controller.signal }).then(contract => {
      if (controller.signal.aborted) return
      setVlmContract(contract)
      setVlmError('')
      let saved: string | null = null
      try { saved = localStorage.getItem(parameterStorageKey) } catch { /* Storage can be disabled. */ }
      if (!useVlmModelStore.getState().modelPath) setVlmModelPath(savedParameters(saved, contract).modelPath)
    }).catch(e => { if (!controller.signal.aborted) setVlmError(String(e)) })
    return () => controller.abort()
  }, [setVlmModelPath, vlmRetry])

  const vlmModel = vlmContract?.models.find(model => model.path === vlmModelPath)

  function onSelectVlmModel(path: string) {
    setVlmModelPath(path)
    persistModelPath(path)
    pushLog({ ts: Date.now() / 1000, level: 'INFO', event: 'vlm.model.selected', msg: path, fields: {} })
  }

  useEffect(() => {
    let active = true
    Promise.all([apiListModels(), apiGetState()])
      .then(([ms, snapshot]) => {
        if (!active) return
        setModels(ms)
        setEngine(snapshot.engine)
        if (snapshot.model) setCurrentModel(snapshot.model)
      })
      .catch((e) => {
        if (active) pushLog({ ts: Date.now() / 1000, level: 'ERROR', event: 'models.list', msg: String(e), fields: {} })
      })
    return () => {
      active = false
    }
  }, [pushLog, setModels, setEngine, setCurrentModel])

  const classOptions = useMemo(() => Array.from(new Set(classes)).sort(), [classes])

  async function onSelectModel(modelId: string) {
    setBusy(true)
    try {
      const model = await apiSelectModel(modelId)
      setCurrentModel(model)
      pushLog({ ts: Date.now() / 1000, level: 'INFO', event: 'model.selected', msg: modelId, fields: {} })
    } catch (e) {
      pushLog({ ts: Date.now() / 1000, level: 'ERROR', event: 'model.select_failed', msg: String(e), fields: { modelId } })
    } finally {
      setBusy(false)
    }
  }

  async function onToggleEngine(next: 'cpu' | 'cuda') {
    const previous = engine.device
    setEngine({ warming: true, device: next })
    try {
      await apiSelectEngine(next)
      pushLog({ ts: Date.now() / 1000, level: 'INFO', event: 'engine.selected', msg: next, fields: {} })
    } catch (e) {
      setEngine({ device: previous })
      pushLog({ ts: Date.now() / 1000, level: 'ERROR', event: 'engine.select_failed', msg: String(e), fields: { device: next } })
    } finally {
      setEngine({ warming: false })
    }
  }

  // 滑动条/标签变更要防抖到后端，避免几十次/秒的 /api/params 请求
  const flushTimerRef = useRef<number | null>(null)
  const pendingParamsRef = useRef<Params | null>(null)
  function flushParams(next: Params) {
    pendingParamsRef.current = next
    if (flushTimerRef.current) window.clearTimeout(flushTimerRef.current)
    flushTimerRef.current = window.setTimeout(async () => {
      const p = pendingParamsRef.current
      pendingParamsRef.current = null
      if (!p) return
      try {
        await apiUpdateParams(p)
      } catch (e) {
        pushLog({ ts: Date.now() / 1000, level: 'WARN', event: 'params.sync_failed', msg: String(e), fields: {} })
      }
    }, 200)
  }
  useEffect(() => {
    return () => {
      if (flushTimerRef.current) window.clearTimeout(flushTimerRef.current)
    }
  }, [])

  return (
    <div className={styles.stack}>
      <Card title={t('cards.modelHub')} right={vlmError ? t('vlmParameters.modelUnavailable') : busy ? t('control.switching') : undefined}>
        <div className={styles.field}>
          <div className={styles.labelRow}><span className={styles.label}>{t('control.detectModel')}</span></div>
          <select
            className={styles.select}
            value={currentModelId || ''}
            onChange={(e) => onSelectModel(e.target.value)}
            disabled={busy}
          >
            <option value="" disabled>
              {t('control.selectModelHint')}
            </option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.filename}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.field}>
          <div className={styles.labelRow}>
            <span className={styles.label}>{t('vlmParameters.modelPath')}</span>
            {vlmContract && vlmModelPath !== vlmContract.defaults.modelPath && (
              <button type="button" className={styles.hint} onClick={() => onSelectVlmModel(vlmContract.defaults.modelPath)}>
                {t('vlmParameters.useDefaultModel')}
              </button>
            )}
          </div>
          <select
            className={styles.select}
            value={vlmModelPath}
            disabled={!vlmContract}
            onChange={(e) => onSelectVlmModel(e.target.value)}
          >
            {vlmModelPath && !vlmContract?.models.some(model => model.path === vlmModelPath) && (
              <option value={vlmModelPath} disabled>{vlmModelPath} — {t('vlmParameters.modelUnavailable')}</option>
            )}
            {(vlmContract?.models ?? []).map((model) => (
              <option key={model.path} value={model.path} disabled={!model.available}>
                {model.name}
                {model.path === vlmContract?.defaults.modelPath ? ` (${t('vlmParameters.defaultModel')})` : ''}
                {!model.available ? ` — ${t('vlmParameters.modelUnavailable')}` : ''}
              </option>
            ))}
          </select>
          {vlmError
            ? <div className={styles.hint} role="alert">{vlmError}{' '}
              <button type="button" onClick={() => { setVlmError(''); setVlmRetry(value => value + 1) }}>{t('vlmParameters.retry')}</button>
            </div>
            : <div className={styles.hint}>{vlmContract ? t('vlmParameters.modelHint') : t('vlmParameters.loading')}</div>}
          {vlmModel?.available === false && <div className={styles.hint} role="alert">{vlmModel.error || t('vlmParameters.modelUnavailableHint')}</div>}
        </div>
      </Card>

      <Card title={t('cards.engine')} right={engine.warming ? t('control.warming') : engine.device.toUpperCase()}>
        <div className={styles.row}>
          <NeoButton onClick={() => onToggleEngine('cpu')} disabled={engine.warming || engine.device === 'cpu'}>
            CPU
          </NeoButton>
          <NeoButton onClick={() => onToggleEngine('cuda')} disabled={engine.warming || engine.device === 'cuda'}>
            GPU (CUDA)
          </NeoButton>
        </div>
      </Card>

      <Card title={t('cards.classFilter')}>
        {classOptions.length === 0 ? (
          <div className={styles.hint}>{t('control.classHint')}</div>
        ) : (
          <div className={styles.tags}>
            {classOptions.map((c) => {
              const active = params.classFilter.includes(c)
              return (
                <button
                  key={c}
                  className={[styles.tag, active ? styles.tagOn : ''].join(' ')}
                  onClick={() => {
                    const classFilter = active ? params.classFilter.filter((x) => x !== c) : [...params.classFilter, c]
                    const next = { ...params, classFilter }
                    setParams({ classFilter })
                    flushParams(next)
                  }}
                >
                  {c}
                </button>
              )
            })}
          </div>
        )}
      </Card>

      <Card title={t('cards.tracking')} right={params.track ? 'on' : 'off'}>
        <label className={styles.toggleRow}>
          <input
            type="checkbox"
            checked={params.track}
            onChange={(e) => {
              const track = e.target.checked
              const next = { ...params, track }
              setParams({ track })
              flushParams(next)
            }}
          />
          {t('control.enableTracking')}
        </label>
        <div className={styles.hint}>
          {t('control.trackingHint')}
          <br />
          {t('control.drawLineHint')}
        </div>
      </Card>
    </div>
  )
}
