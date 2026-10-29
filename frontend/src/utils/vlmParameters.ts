export type VlmParameters = {
  modelPath: string
  poseDevice: 'cpu' | 'cuda'; dryRun: boolean
  poseFps: number; conf: number; iou: number; stableSeconds: number; hysteresisPx: number
  maxGapSeconds: number; sampleFps: number; maxFrames: number; maxPixels: number; maxNewTokens: number
}
export const numericParameters = ['sampleFps', 'maxFrames', 'maxPixels', 'poseFps', 'maxNewTokens', 'conf', 'iou', 'stableSeconds', 'hysteresisPx', 'maxGapSeconds'] as const
export type NumericParameter = typeof numericParameters[number]
export type ParameterField = { type: string; minimum?: number; maximum?: number; exclusiveMinimum?: number; exclusiveMaximum?: number; multipleOf?: number }
export type ParameterContract = { defaults: VlmParameters; fields: Record<NumericParameter, ParameterField>; models: { path: string; name: string; available: boolean; error: string | null }[] }
export type ParameterDraft = Record<NumericParameter, string> & Pick<VlmParameters, 'poseDevice' | 'dryRun' | 'modelPath'>
export const parameterStorageKey = 'ultraconsole.vlm.parameters.v1'

export function validParameter(value: unknown, field: ParameterField): value is number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return false
  if (field.type === 'integer' && !Number.isInteger(value)) return false
  if (field.minimum !== undefined && value < field.minimum) return false
  if (field.maximum !== undefined && value > field.maximum) return false
  if (field.exclusiveMinimum !== undefined && value <= field.exclusiveMinimum) return false
  if (field.exclusiveMaximum !== undefined && value >= field.exclusiveMaximum) return false
  return field.multipleOf === undefined || Math.abs(value / field.multipleOf - Math.round(value / field.multipleOf)) < 1e-8
}

export function parameterDraft(value: VlmParameters): ParameterDraft {
  return { ...Object.fromEntries(numericParameters.map(key => [key, String(value[key])])), poseDevice: value.poseDevice, dryRun: value.dryRun, modelPath: value.modelPath } as ParameterDraft
}

export function parseParameters(draft: ParameterDraft, contract: ParameterContract) {
  const invalid = numericParameters.filter(key => !draft[key].trim() || !validParameter(Number(draft[key]), contract.fields[key]))
  const invalidModel = !draft.dryRun && !contract.models.some(model => model.path === draft.modelPath && model.available)
  const values = { ...Object.fromEntries(numericParameters.map(key => [key, Number(draft[key])])), poseDevice: draft.poseDevice, dryRun: draft.dryRun, modelPath: draft.modelPath } as VlmParameters
  return { invalid, invalidModel, value: invalid.length || invalidModel || !['cuda', 'cpu'].includes(values.poseDevice) ? null : values }
}

export function savedParameters(raw: string | null, contract: ParameterContract): VlmParameters {
  const value = { ...contract.defaults }
  try {
    const saved: unknown = JSON.parse(raw || '{}')
    if (saved && typeof saved === 'object') {
      const item = saved as Record<string, unknown>
      for (const key of numericParameters) if (validParameter(item[key], contract.fields[key])) value[key] = item[key]
      if (item.poseDevice === 'cpu' || item.poseDevice === 'cuda') value.poseDevice = item.poseDevice
      // Keep an unavailable selection visible and block submission instead of silently switching models.
      if (typeof item.modelPath === 'string' && item.modelPath.trim()) value.modelPath = item.modelPath
    }
  } catch { /* Invalid or stale browser preferences fall back to server defaults. */ }
  // Preview-only mode is deliberate for each page session, never restored silently.
  value.dryRun = false
  return value
}

export function parameterRange(field: ParameterField) {
  return `${field.exclusiveMinimum === undefined ? '[' : '('}${field.minimum ?? field.exclusiveMinimum}, ${field.maximum ?? field.exclusiveMaximum}${field.exclusiveMaximum === undefined ? ']' : ')'}`
}
