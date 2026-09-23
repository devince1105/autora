// Who is walking across the 2D floor right now (T-408 on the board).
//
// The 3D office walks its couriers with a frame hook inside the canvas; the 2D office has no
// canvas to hang one on, so it steps the same queue itself (``PixelFloor``) and asks this
// module where everybody is. The cues, the routes and the courier's own maths are shared — one
// set of rules about who walks where, drawn twice.

import { courierState, type CourierPhase } from "../agents/courier";
import type { Roster } from "../agents/roster";
import { routeFor } from "../visual/CueRunner";
import type { CueDirector } from "../visual/CueRunner";

export interface Walker {
  agentId: string;
  /** Floor metres, as the layout counts them (x, z). */
  position: readonly [number, number];
  phase: CourierPhase;
  /** True while the document is in their hands: on the way there, not on the way back. */
  carrying: boolean;
}

/**
 * Everybody mid-walk at ``now``, with where they have got to.
 *
 * An agent with no route (no seat, nobody to hand to) is simply not walking: the office shows
 * what it can work out, and stays where it is about the rest.
 */
export function walkersNow(
  director: CueDirector | null,
  roster: Pick<Roster, "members" | "seats">,
  now: number,
): Walker[] {
  if (!director) return [];
  const out: Walker[] = [];
  for (const member of roster.members) {
    const active = director.queue.walk(member.id);
    if (!active) continue;
    const route = routeFor(active.cue, roster);
    if (!route) continue;
    const state = courierState(route, now - active.startedAt);
    if (state.phase === "done") continue;
    out.push({
      agentId: member.id,
      position: state.position,
      phase: state.phase,
      carrying: state.carrying,
    });
  }
  return out;
}
