// Where a courier is at a moment of its walk (T-408): out along the route carrying the document,
// a pause at the other desk to hand it over (facing the colleague), and back empty-handed. Pure;
// the route and its timing come from routeFor (T-407), so the queue and the walk agree.
import { HANDOVER_MS, WALK_SPEED, type Route } from "../visual/CueRunner";
import type { Vec2 } from "../scene/layout";

export type CourierPhase = "going" | "handover" | "returning" | "done";

export interface CourierState {
  phase: CourierPhase;
  position: Vec2;
  /** Rotation about y so the avatar (facing +z at 0) looks where it goes. */
  heading: number;
  carrying: boolean;
}

const headingOf = (from: Vec2, to: Vec2) => Math.atan2(to[0] - from[0], to[1] - from[1]);

/** The point `distance` metres along a polyline, and the heading of that segment. */
export function along(path: readonly Vec2[], distance: number): { position: Vec2; heading: number } {
  let left = Math.max(0, distance);
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1];
    const b = path[i];
    const segment = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (left <= segment || i === path.length - 1) {
      const t = segment === 0 ? 1 : Math.min(1, left / segment);
      return { position: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], heading: headingOf(a, b) };
    }
    left -= segment;
  }
  return { position: path[0], heading: 0 };
}

export function courierState(route: Route, elapsedMs: number): CourierState {
  const oneWayMs = (route.length / WALK_SPEED) * 1000;
  const end = route.path[route.path.length - 1];
  if (elapsedMs < oneWayMs) {
    const { position, heading } = along(route.path, (elapsedMs / 1000) * WALK_SPEED);
    return { phase: "going", position, heading, carrying: true };
  }
  if (elapsedMs < oneWayMs + HANDOVER_MS) {
    return { phase: "handover", position: end, heading: headingOf(end, route.lookAt), carrying: elapsedMs < oneWayMs + HANDOVER_MS / 2 };
  }
  // the walk ends when the queue ends it (route.durationMs), so both agree to the millisecond
  if (route.returnAfter && elapsedMs < route.durationMs) {
    const back = [...route.path].reverse();
    const { position, heading } = along(back, ((elapsedMs - oneWayMs - HANDOVER_MS) / 1000) * WALK_SPEED);
    return { phase: "returning", position, heading, carrying: false };
  }
  return { phase: "done", position: route.returnAfter ? route.path[0] : end, heading: 0, carrying: false };
}
