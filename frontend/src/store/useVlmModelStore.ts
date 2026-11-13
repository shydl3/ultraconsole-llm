import { create } from 'zustand'

type VlmModelState = {
  /** Selected Qwen3-VL weights for the next semantic analysis run. */
  modelPath: string
  setModelPath: (modelPath: string) => void
}

/**
 * Shared semantic-model choice so the left Model Hub and the analysis panel
 * always agree, even when the video tab is unmounted.
 */
export const useVlmModelStore = create<VlmModelState>((set) => ({
  modelPath: '',
  setModelPath: (modelPath) => set({ modelPath }),
}))
