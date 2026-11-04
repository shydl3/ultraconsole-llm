import { create } from 'zustand'

export type UiView = 'console' | 'library'

type UiState = {
  view: UiView
  setView: (view: UiView) => void
}

export const useUiStore = create<UiState>((set) => ({
  view: 'console',
  setView: (view) => set({ view }),
}))
