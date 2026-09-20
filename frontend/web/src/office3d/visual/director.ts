// The animation director (T-407, 04 §4): events -> cues (a pure function), and the per-agent cue
// queue with the rules that keep an event flood from turning into animation noise:
//   - a walk to a target already queued (or being walked) is not added again;
//   - a new run for an agent aborts its walks at once (it snaps back to its desk: state wins);
//   - events older than CUE_TTL_MS make no cue (a reconnect replaying history only updates state);
//   - at most MAX_WALKS_QUEUED walks wait per agent.
// Poses are not cues: the mapping derives them from state.
import type { EventEnvelope } from "@autora/event-schema";

import type { AgentState } from "@/stores/realtime";

import { sameTarget, type EffectCue, type VisualCue, type WalkCue } from "./cues";

export const CUE_TTL_MS = 10_000;
export const MAX_WALKS_QUEUED = 4;
export const FLASH_MS = 1000;
export const SCREEN_ALERT_MS = 2000;

type Payload = Record<string, unknown>;

/**
 * The agent an event is about: its `agent_id`, or its actor when that is an agent (approval
 * events leave `agent_id` empty; their actor is the agent that asked).
 */
export function agentOf(event: EventEnvelope): string | null {
  return event.agent_id ?? (event.actor.kind === "agent" ? event.actor.id : null);
}

/** The cues one event asks for. `now` is server time; `agents` the company's agents. */
/** The room an agent works in: its department's zone, or none when it has no place yet. */
function roomOf(agent: AgentState | undefined): string | null {
  return agent?.office_zone_key ?? null;
}

export function cuesFor(event: EventEnvelope, agents: Record<string, AgentState>, now: Date): VisualCue[] {
  if (event.seq === null) return [];
  if (Date.parse(event.occurred_at) < now.getTime() - CUE_TTL_MS) return [];
  const seq = event.seq;
  const agentId = agentOf(event);
  const payload = event.payload as Payload;
  const walk = (target: WalkCue["target"]): WalkCue => ({ kind: "walk", agentId: agentId!, target, carry: "document", returnAfter: true, seq });
  /**
   * Where work going to `role` is carried (T-600 batch 4).
   *
   * To a colleague's desk when one of them sits in this agent's own room; to the door of the
   * room they work in when they do not. Nothing is invented for a role nobody holds — that is
   * still a walk to the desk the floor plan keeps for it.
   */
  const handTo = (role: string): WalkCue => {
    const mine = agentId ? agents[agentId] : undefined;
    const holders = Object.values(agents).filter((a) => a.role === role && a.id !== agentId);
    if (holders.length === 0) return walk({ role });
    const here = holders.some((a) => roomOf(a) === roomOf(mine));
    if (here) return walk({ role });
    const room = roomOf(holders[0]);
    return room ? walk({ door: room }) : walk({ role });
  };

  switch (event.event_type) {
    case "AGENT_RUN_STARTED":
      return agentId ? [{ kind: "abort_walks", agentId, seq }] : [];
    case "AGENT_RUN_COMPLETED": {
      if (!agentId) return [];
      const roles = new Set(((payload.handoff as { to_role: string }[] | undefined) ?? []).map((h) => h.to_role));
      return [...roles].map(handTo);
    }
    case "TASK_SUCCEEDED": {
      if (!agentId) return [];
      const roles = new Set(((payload.unlocks as { required_role: string }[] | undefined) ?? []).map((u) => u.required_role));
      return [...roles].map((role) => (role === "human" ? walk({ place: "approval" }) : handTo(role)));
    }
    case "APPROVAL_REQUESTED":
      return agentId ? [walk({ place: "approval" })] : [];
    case "AGENT_RUN_FAILED":
    case "AGENT_RUN_ABORTED":
      return agentId && payload.final === true ? [{ kind: "flash", agentId, color: "red", durationMs: FLASH_MS, seq }] : [];
    case "CYCLE_STARTED":
      return Object.values(agents)
        .filter((a) => a.role === "ceo")
        .map((a) => ({ kind: "screen_alert", agentId: a.id, durationMs: SCREEN_ALERT_MS, seq }));
    default:
      return [];
  }
}

export interface ActiveWalk {
  cue: WalkCue;
  startedAt: number;
  durationMs: number;
}

export interface ActiveEffect {
  cue: EffectCue;
  until: number;
}

/**
 * Per-agent cues. Walks run one after another; effects (flash, screen alert) are overlays that
 * start at once and run alongside. `plan` turns a walk into a duration (null: nowhere to go).
 */
export class CueQueue {
  private readonly waiting = new Map<string, WalkCue[]>();
  private readonly walking = new Map<string, ActiveWalk>();
  private readonly effects = new Map<string, ActiveEffect>();

  apply(cues: readonly VisualCue[], now: number): void {
    for (const cue of cues) {
      if (cue.kind === "abort_walks") {
        this.waiting.delete(cue.agentId);
        this.walking.delete(cue.agentId);
      } else if (cue.kind === "walk") {
        const queue = this.waiting.get(cue.agentId) ?? [];
        const current = this.walking.get(cue.agentId)?.cue;
        const duplicate = (current && sameTarget(current.target, cue.target)) || queue.some((q) => sameTarget(q.target, cue.target));
        if (duplicate || queue.length >= MAX_WALKS_QUEUED) continue;
        this.waiting.set(cue.agentId, [...queue, cue]);
      } else {
        // the same effect again restarts it
        this.effects.set(`${cue.kind}:${cue.agentId}`, { cue, until: now + cue.durationMs });
      }
    }
  }

  /** Finish what is over, start what is next. Call every frame (a long pause fast-forwards). */
  step(now: number, plan: (cue: WalkCue) => number | null): void {
    for (const [agentId, walk] of this.walking) if (now >= walk.startedAt + walk.durationMs) this.walking.delete(agentId);
    for (const [key, effect] of this.effects) if (now >= effect.until) this.effects.delete(key);
    for (const [agentId, queue] of this.waiting) {
      if (this.walking.has(agentId)) continue;
      while (queue.length) {
        const cue = queue.shift()!;
        const durationMs = plan(cue);
        if (durationMs !== null) {
          this.walking.set(agentId, { cue, startedAt: now, durationMs });
          break;
        }
      }
      if (!queue.length) this.waiting.delete(agentId);
    }
  }

  walk(agentId: string): ActiveWalk | undefined {
    return this.walking.get(agentId);
  }

  queued(agentId: string): readonly WalkCue[] {
    return this.waiting.get(agentId) ?? [];
  }

  effect(kind: EffectCue["kind"], agentId: string): ActiveEffect | undefined {
    return this.effects.get(`${kind}:${agentId}`);
  }

  clear(): void {
    this.waiting.clear();
    this.walking.clear();
    this.effects.clear();
  }
}
