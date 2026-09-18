// UI store (T-307, 3d-office/05 §5): what the operator is looking at. Ephemeral and local:
// never sent to the server, never derived from events, and kept apart from the realtime store
// so a burst of events never re-renders a panel because of UI state, or the other way round.
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";

export type PanelTab = "live" | "steps" | "tools" | "output";
export type CameraMode = "overview" | "follow" | "free";

export interface TimelineFilters {
  agentIds: string[];
  eventTypes: string[];
}

export interface UiState {
  selectedAgentId: string | null;
  panelTab: PanelTab;
  cameraMode: CameraMode;
  timelinePaused: boolean;
  filters: TimelineFilters;

  /** Select an agent (opens its panel on the live tab); null closes the panel. */
  selectAgent(agentId: string | null): void;
  setPanelTab(tab: PanelTab): void;
  setCameraMode(mode: CameraMode): void;
  setTimelinePaused(paused: boolean): void;
  setFilters(patch: Partial<TimelineFilters>): void;
  reset(): void;
}

const initial = {
  selectedAgentId: null,
  panelTab: "live" as PanelTab,
  cameraMode: "overview" as CameraMode,
  timelinePaused: false,
  filters: { agentIds: [], eventTypes: [] } as TimelineFilters,
};

export function createUiStore(): StoreApi<UiState> {
  return createStore<UiState>()((set, get) => ({
    ...initial,

    selectAgent(agentId) {
      if (agentId === get().selectedAgentId) return;
      // A new agent starts on the live tab; the camera follows it only if it already follows.
      set({ selectedAgentId: agentId, panelTab: "live" });
      if (agentId === null && get().cameraMode === "follow") set({ cameraMode: "overview" });
    },
    setPanelTab(tab) {
      set({ panelTab: tab });
    },
    setCameraMode(mode) {
      // Following needs someone to follow.
      set({ cameraMode: mode === "follow" && !get().selectedAgentId ? "overview" : mode });
    },
    setTimelinePaused(paused) {
      set({ timelinePaused: paused });
    },
    setFilters(patch) {
      set((state) => ({ filters: { ...state.filters, ...patch } }));
    },
    reset() {
      set({ ...initial, filters: { agentIds: [], eventTypes: [] } });
    },
  }));
}

export const uiStore = createUiStore();

export function useUi<T>(selector: (state: UiState) => T): T {
  return useStore(uiStore, selector);
}
