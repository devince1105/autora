// Visual cues (T-407, 04 §4): short, event-triggered animations on top of the pose the mapping
// gives. Poses come from state; cues come from events and are allowed to be missed (a reload
// shows the state, not the walk that led to it).

/** Where a walk goes: the desk of an agent of `role`, or the approval desk. */
export type WalkTarget = { role: string } | { place: "approval" };

export type VisualCue =
  | {
      kind: "walk";
      agentId: string;
      target: WalkTarget;
      carry: "document" | "none";
      returnAfter: boolean;
      /** The event that caused it (for dedupe and debugging). */
      seq: number;
    }
  | { kind: "flash"; agentId: string; color: "red"; durationMs: number; seq: number }
  | { kind: "screen_alert"; agentId: string; durationMs: number; seq: number }
  /** Not an animation: stop this agent's walks now (a new run started; state beats animation). */
  | { kind: "abort_walks"; agentId: string; seq: number };

export type WalkCue = Extract<VisualCue, { kind: "walk" }>;
export type EffectCue = Extract<VisualCue, { kind: "flash" | "screen_alert" }>;

export const sameTarget = (a: WalkTarget, b: WalkTarget): boolean =>
  "role" in a ? "role" in b && a.role === b.role : "place" in b && a.place === b.place;
