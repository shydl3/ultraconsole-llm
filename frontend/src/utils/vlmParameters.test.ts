import { describe, expect, it } from 'vitest'
import { parameterDraft, parseParameters, savedParameters, type ParameterContract } from './vlmParameters'

const contract: ParameterContract = {
  defaults: { modelPath: '/models/Qwen3-VL-2B-Instruct', poseDevice: 'cuda', dryRun: false, poseFps: 10, conf: .25, iou: .5, stableSeconds: .2, hysteresisPx: 6, maxGapSeconds: 1, sampleFps: 2, maxFrames: 32, maxPixels: 196608, maxNewTokens: 512 },
  models: ['2B', '4B'].map(size => ({ path: `/models/Qwen3-VL-${size}-Instruct`, name: size, available: true, error: null })),
  fields: {
    poseFps: { type: 'number', minimum: 1, maximum: 30 },
    conf: { type: 'number', exclusiveMinimum: 0, exclusiveMaximum: 1 },
    iou: { type: 'number', exclusiveMinimum: 0, exclusiveMaximum: 1 },
    stableSeconds: { type: 'number', minimum: 0, maximum: 2 },
    hysteresisPx: { type: 'number', minimum: 0, maximum: 50 },
    maxGapSeconds: { type: 'number', minimum: .2, maximum: 5 },
    sampleFps: { type: 'number', minimum: .5, maximum: 8 },
    maxFrames: { type: 'integer', minimum: 4, maximum: 64, multipleOf: 2 },
    maxPixels: { type: 'integer', minimum: 65536, maximum: 786432 },
    maxNewTokens: { type: 'integer', minimum: 128, maximum: 1024 },
  },
}

describe('VLM parameters', () => {
  it('submits and restores the selected model, without silently replacing missing weights', () => {
    const modelPath = '/models/Qwen3-VL-4B-Instruct'
    const value = savedParameters(JSON.stringify({ modelPath }), contract)
    expect(parseParameters(parameterDraft(value), contract).value?.modelPath).toBe(modelPath)
    const missing = { ...contract, models: contract.models.filter(model => model.path !== modelPath) }
    const draft = parameterDraft(savedParameters(JSON.stringify({ modelPath }), missing))
    expect(draft.modelPath).toBe(modelPath)
    expect(parseParameters(draft, missing).invalidModel).toBe(true)
    expect(parseParameters(draft, missing).value).toBeNull()
    expect(parseParameters({ ...draft, dryRun: true }, missing).value?.dryRun).toBe(true)
    expect(parseParameters(parameterDraft(contract.defaults), contract).value?.modelPath).toBe(contract.defaults.modelPath)
  })
  it('uses server defaults and preserves every numeric parameter in the submitted payload', () => {
    expect(parseParameters(parameterDraft(contract.defaults), contract).value).toEqual(contract.defaults)
    const edited = { ...parameterDraft(contract.defaults), sampleFps: '4', maxFrames: '48', poseFps: '15', dryRun: true }
    expect(parseParameters(edited, contract).value).toEqual({ ...contract.defaults, sampleFps: 4, maxFrames: 48, poseFps: 15, dryRun: true })
    expect(parseParameters(parameterDraft(contract.defaults), contract).invalid).toEqual([])
  })
  it.each([{ maxFrames: '5' }, { maxFrames: '4.5' }, { maxFrames: '' }, { conf: '0' }, { iou: '1' }, { sampleFps: 'NaN' }, { maxPixels: 'Infinity' }, { poseFps: '31' }])('blocks invalid input %o', changes => {
    expect(parseParameters({ ...parameterDraft(contract.defaults), ...changes }, contract).value).toBeNull()
  })
  it('restores only validated preferences and does not restore preview-only mode', () => {
    const raw = JSON.stringify({ sampleFps: 4, maxFrames: 7, conf: 1, poseDevice: 'cpu', dryRun: true, maxPixels: '393216', unexpected: 123 })
    expect(savedParameters(raw, contract)).toEqual({ ...contract.defaults, sampleFps: 4, poseDevice: 'cpu' })
    for (const value of [null, 'broken', 'null', '[]']) expect(savedParameters(value, contract)).toEqual(contract.defaults)
    const next = { ...contract, defaults: { ...contract.defaults, sampleFps: 3 } }
    expect(savedParameters(null, next).sampleFps).toBe(3)
  })
})
