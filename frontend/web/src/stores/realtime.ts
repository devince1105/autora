// Realtime store (T-305, 3d-office/05 §5): the company's domain projection, pushed by the
// realtime client. The only way to change it is hydrate / applyEvent(s) / applyEphemeral, which
// delegate to the pure reducer. No visual state and no UI state (selectedAgent...) here.
//
// Two ways to read it: `useRealtime(selector)` in React, and `realtimeStore.subscribe` for the
// 3D office, which must not re-render React on every event (05 §5).
import { parseEvent } from "@autora/event-schema";
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";

import {
  applyEphemeral,
  applyEvent,
  hydrate,
  type EphemeralMessage,
  type RealtimeState,
} from "@/realtime/reducer";
import { handling } from "@/realtime/latency";
import { RealtimeSnapshot } from "@/realtime/snapshot";

// What the 3D office may read (it must not import @/realtime/* itself, 3d-office/04 §1): the
// state types and the projection's one time-based rule.
export { effectiveState } from "@/realtime/reducer";
export type { AgentState, LiveProgress, RealtimeState } from "@/realtime/reducer";
export { ACTIVITY_STATES, type ActivityState, type ActivityView } from "@/realtime/snapshot";

/**
 * idle: not started; connecting: loading the snapshot or replaying the backlog; live: up to
 * date; reconnecting: lost, retrying; offline: 5+ failures in a row, still retrying;
 * unauthorized / not_found: stopped, retrying cannot help.
 */
export type ConnectionStatus =
  | "idle"
  | "connecting"
  | "live"
  | "reconnecting"
  | "offline"
  | "unauthorized"
  | "not_found";

export interface Connection {
  status: ConnectionStatus;
  /** Server clock minus local clock, from HELLO / HEARTBEAT (ms). */
  serverOffsetMs: number;
  lastEventAt: number | null;
}

export interface RealtimeStoreState {
  /** null until the first snapshot has been applied. */
  company: RealtimeState | null;
  kpis: Record<string, unknown> | null;
  cycle: Record<string, unknown> | null;
  connection: Connection;
  /** Events rejected by validation (unknown type, newer schema); logged, never applied. */
  dropped: number;

  hydrate(snapshot: unknown): void;
  applyEvent(raw: unknown): boolean;
  applyEvents(raws: readonly unknown[]): number;
  applyEphemeral(message: EphemeralMessage): void;
  setConnection(patch: Partial<Connection>): void;
  reset(): void;
}

const initialConnection: Connection = { status: "idle", serverOffsetMs: 0, lastEventAt: null };

export function createRealtimeStore(): StoreApi<RealtimeStoreState> {
  return createStore<RealtimeStoreState>()((set, get) => {
    /** Apply raw events in order; returns the new company state and counts. */
    const reduce = (raws: readonly unknown[]) => {
      let company = get().company;
      let applied = 0;
      let dropped = 0;
      if (!company) return { company, applied, dropped: raws.length };
      for (const raw of raws) {
        const parsed = parseEvent(raw);
        if (!parsed.ok) {
          dropped += 1;
          console.warn(`realtime: dropped ${parsed.eventType ?? "event"}: ${parsed.error}`);
          continue;
        }
        const next = applyEvent(company, parsed.event);
        if (next !== company) applied += 1;
        company = next;
      }
      return { company, applied, dropped };
    };

    return {
      company: null,
      kpis: null,
      cycle: null,
      connection: initialConnection,
      dropped: 0,

      hydrate(snapshot) {
        const parsed = RealtimeSnapshot.parse(snapshot);
        set({ company: hydrate(parsed), kpis: parsed.kpis, cycle: parsed.cycle });
      },

      applyEvent(raw) {
        return get().applyEvents([raw]) === 1;
      },

      applyEvents(raws) {
        const started = performance.now();
        const { company, applied, dropped } = reduce(raws);
        if (applied || dropped) {
          set((state) => ({
            company,
            dropped: state.dropped + dropped,
            connection: applied
              ? { ...state.connection, lastEventAt: Date.now() }
              : state.connection,
          }));
        }
        // one socket message, one sample: what the browser spent turning it into state (AC-S7)
        handling.record(performance.now() - started, raws.length);
        return applied;
      },

      applyEphemeral(message) {
        const company = get().company;
        if (!company) return;
        const next = applyEphemeral(company, message);
        if (next !== company) set({ company: next });
      },

      setConnection(patch) {
        set((state) => ({ connection: { ...state.connection, ...patch } }));
      },

      reset() {
        set({ company: null, kpis: null, cycle: null, connection: initialConnection, dropped: 0 });
      },
    };
  });
}

/** The app's store (one company at a time in the MVP). */
export const realtimeStore = createRealtimeStore();

export function useRealtime<T>(selector: (state: RealtimeStoreState) => T): T {
  return useStore(realtimeStore, selector);
}

/** Server-corrected "now", for effective activity states and the finished-task window. */
export function serverNow(state: Pick<RealtimeStoreState, "connection">): Date {
  return new Date(Date.now() + state.connection.serverOffsetMs);
}
