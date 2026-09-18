// Feeds new events to the director and runs the cues (T-407). One CueDirector per scene: it
// subscribes to the store, turns events it has not seen into cues (the first snapshot and a
// company switch make none), and a frame hook steps the queue. Walks get their route and length
// from the floor plan; the courier (T-408) walks them, the screens and lamps show the effects.
import { useFrame } from "@react-three/fiber";
import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";

import { realtimeStore, serverNow, type RealtimeStoreState } from "@/stores/realtime";

import type { Roster } from "../agents/roster";
import { useRoster } from "../agents/roster";
import { seatsForRole, walkPath, type Seat, type Vec2 } from "../scene/layout";
import type { WalkCue } from "./cues";
import { cuesFor, CueQueue } from "./director";

/** Walking speed (m/s) and how long the courier stays at the other desk. */
export const WALK_SPEED = 1.4;
export const HANDOVER_MS = 1200;

type Store = { getState(): RealtimeStoreState; subscribe(listener: (state: RealtimeStoreState) => void): () => void };

export class CueDirector {
  readonly queue = new CueQueue();
  private companyId: string | null = null;
  private lastSeq = 0;
  private readonly unsubscribe: () => void;

  constructor(
    store: Store = realtimeStore,
    private readonly clock: () => number = () => performance.now(),
  ) {
    this.onState(store.getState());
    this.unsubscribe = store.subscribe((state) => this.onState(state));
  }

  private onState(state: RealtimeStoreState): void {
    const company = state.company;
    if (!company) {
      this.companyId = null;
      this.queue.clear();
      return;
    }
    if (company.companyId !== this.companyId) {
      // a snapshot is history, not news: nothing to animate
      this.companyId = company.companyId;
      this.lastSeq = company.lastSeq;
      this.queue.clear();
      return;
    }
    if (company.lastSeq <= this.lastSeq) return;
    const now = serverNow(state);
    for (const event of company.recentEvents) {
      if (event.seq === null || event.seq <= this.lastSeq) continue;
      this.queue.apply(cuesFor(event, company.agents, now), this.clock());
    }
    this.lastSeq = company.lastSeq;
  }

  dispose(): void {
    this.unsubscribe();
  }
}

export interface Route {
  path: Vec2[];
  /** One way, metres. */
  length: number;
  durationMs: number;
}

/** Where a walk goes and how long it takes (there and back, with the hand-over). */
export function routeFor(cue: WalkCue, roster: Pick<Roster, "members" | "seats">): Route | null {
  const from = roster.seats.get(cue.agentId);
  if (!from) return null;
  let to: Seat | "approval";
  if ("place" in cue.target) to = "approval";
  else {
    const role = cue.target.role;
    const colleague = roster.members.find((m) => m.role === role && m.id !== cue.agentId && roster.seats.has(m.id));
    const seat = colleague ? roster.seats.get(colleague.id) : seatsForRole(role, 1)[0];
    if (!seat || seat.key === from.key) return null;
    to = seat;
  }
  const path = walkPath(from, to);
  let length = 0;
  for (let i = 1; i < path.length; i++) length += Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]);
  const walking = (length / WALK_SPEED) * 1000 * (cue.returnAfter ? 2 : 1);
  return { path, length, durationMs: Math.round(walking + HANDOVER_MS) };
}

const CueContext = createContext<CueDirector | null>(null);

function Runner({ director }: { director: CueDirector }) {
  const roster = useRoster();
  const latest = useRef(roster);
  latest.current = roster;
  useFrame(() => director.queue.step(performance.now(), (cue) => routeFor(cue, latest.current)?.durationMs ?? null), -1);
  return null;
}

export function CueProvider({ children, director: given }: { children: ReactNode; director?: CueDirector }) {
  const director = useMemo(() => given ?? new CueDirector(), [given]);
  useEffect(() => () => void (given ? undefined : director.dispose()), [given, director]);
  return (
    <CueContext.Provider value={director}>
      <Runner director={director} />
      {children}
    </CueContext.Provider>
  );
}

/** The scene's cues, or null outside a CueProvider (tests of single components). */
export function useCues(): CueDirector | null {
  return useContext(CueContext);
}
