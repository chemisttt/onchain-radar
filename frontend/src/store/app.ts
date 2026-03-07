import { create } from 'zustand'

export type ActiveTab = 'feed' | 'funding' | 'analyzer' | 'derivatives' | 'docs'

interface AppStore {
  activeTab: ActiveTab
  setActiveTab: (tab: ActiveTab) => void
}

export const useAppStore = create<AppStore>((set) => ({
  activeTab: 'feed',
  setActiveTab: (tab) => set({ activeTab: tab }),
}))
