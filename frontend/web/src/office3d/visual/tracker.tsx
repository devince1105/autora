// One place that turns the store into visual states for the whole scene (T-406): recomputed when
// the store changes (plain subscription) or once a second (display_until), never through React.
// Screens, desk lamps and head tags read it every frame and act only when `version` moves.
import { useFrame } from "@react-three/fiber";
import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";

import { realtimeStore, serverNow, type RealtimeStoreState } from "@/stores/realtime";

import { visualForAgent, type VisualState } from "./mapping";

const RECHECK_MS = 1000;

type Store = { getState(): RealtimeStoreState; subscribe(listener: () => void): () => void };

export class VisualTracker {
  version = 0;
  /** Agents waiting for a human's approval now (the approval desk lamp blinks while > 0). */
  approvalsWaiting = 0;
  private visuals = new Map<string, VisualState>();
  private keys = new Map<string, string>();
  private dirty = true;
  private nextCheck = 0;
  private readonly unsubscribe: () => void;

  constructor(
    private readonly store: Store = realtimeStore,
    private readonly clock: () => number = () => performance.now(),
  ) {
    this.unsubscribe = store.subscribe(() => void (this.dirty = true));
  }

  /** Call once per frame. Returns the version (bumped when any agent's visual state changed). */
  tick(): number {
    const now = this.clock();
    if (!this.dirty && now < this.nextCheck) return this.version;
    this.dirty = false;
    this.nextCheck = now + RECHECK_MS;
    const state = this.store.getState();
    const at = serverNow(state);
    const next = new Map<string, VisualState>();
    let changed = false;
    let waiting = 0;
    for (const agent of Object.values(state.company?.agents ?? {})) {
      if (agent.activity?.stored_state === "WAITING" && agent.activity.detail.reason === "approval") waiting++;
      const visual = visualForAgent(agent, at);
      if (!visual) continue;
      next.set(agent.id, visual);
      const key = JSON.stringify(visual);
      if (this.keys.get(agent.id) !== key) {
        this.keys.set(agent.id, key);
        changed = true;
      }
    }
    for (const id of this.visuals.keys()) if (!next.has(id)) changed = true;
    this.visuals = next;
    if (waiting !== this.approvalsWaiting) {
      this.approvalsWaiting = waiting;
      changed = true;
    }
    if (changed) this.version++;
    return this.version;
  }

  get(agentId: string): VisualState | undefined {
    return this.visuals.get(agentId);
  }

  dispose(): void {
    this.unsubscribe();
  }
}

const TrackerContext = createContext<VisualTracker | null>(null);

/** Ticks the tracker before everyone else in the frame (priority -1 runs first). */
function Ticker({ tracker }: { tracker: VisualTracker }) {
  useFrame(() => void tracker.tick(), -1);
  return null;
}

export function VisualTrackerProvider({ children, tracker: given }: { children: ReactNode; tracker?: VisualTracker }) {
  const tracker = useMemo(() => given ?? new VisualTracker(), [given]);
  useEffect(() => () => void (given ? undefined : tracker.dispose()), [given, tracker]);
  return (
    <TrackerContext.Provider value={tracker}>
      <Ticker tracker={tracker} />
      {children}
    </TrackerContext.Provider>
  );
}

export function useVisualTracker(): VisualTracker {
  const tracker = useContext(TrackerContext);
  if (!tracker) throw new Error("useVisualTracker needs a VisualTrackerProvider");
  return tracker;
}
