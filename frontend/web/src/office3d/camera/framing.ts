// Camera framing (T-409, 04 §6): how much of the office the orthographic camera shows. Pure.
//   overview — the whole room fits the canvas, whatever its size;
//   focus    — an agent's desk, closer (FOCUS_FACTOR x the overview zoom);
//   a room   — one department's zone, when the operator enters it (T-600 batch 3);
// and the limits: a polar and azimuth range around the isometric view (the back and left walls
// stay behind the room), zoom from a bit wider than the overview to close up, and a target that
// cannot leave the room.
import { Box3, Matrix4, OrthographicCamera, Vector3, type Quaternion } from "three";

import { CEO_OFFICE, ROOM, ZONES } from "../scene/layout";

/** The default view direction: from the front right, above (the isometric view). */
export const VIEW_DIRECTION = new Vector3(1, 1.15, 1).normalize();
export const CAMERA_DISTANCE = 60;
export const OVERVIEW_MARGIN = 1.06;
export const FOCUS_FACTOR = 2.4;
export const ZOOM_RANGE = { min: 0.8, max: 5 } as const; // x the overview zoom
export const POLAR_RANGE = { min: 0.3, max: 1.3 } as const; // radians from straight down
export const AZIMUTH_SPREAD = 0.9; // radians either side of the isometric azimuth
export const DEFAULT_AZIMUTH = Math.atan2(VIEW_DIRECTION.x, VIEW_DIRECTION.z);
export const FOCUS_MS = 600;

/** The room as the camera must frame it: slab to wall caps. */
/** A department's room, as a box the camera can frame (T-600 batch 3); null for no such room. */
export function zoneBox(zone: string): Box3 | null {
  const area = zone === "ceo" ? CEO_OFFICE : ZONES[zone as keyof typeof ZONES];
  if (!area) return null;
  const pad = 1.2; // a little air, so the walls of the room are in shot
  return new Box3(
    new Vector3(area.minX - pad, -0.35, area.minZ - pad),
    new Vector3(area.maxX + pad, ROOM.wallHeight, area.maxZ + pad),
  );
}

export const ROOM_BOX = new Box3(new Vector3(ROOM.minX - 0.4, -0.35, ROOM.minZ - 0.4), new Vector3(ROOM.maxX + 0.4, ROOM.wallHeight, ROOM.maxZ + 0.4));
export const ROOM_CENTRE = ROOM_BOX.getCenter(new Vector3());

/** How a camera looking along VIEW_DIRECTION is turned (the overview is always framed this way). */
export const DEFAULT_ORIENTATION: Quaternion = (() => {
  const probe = new OrthographicCamera();
  probe.position.copy(VIEW_DIRECTION);
  probe.lookAt(0, 0, 0);
  return probe.quaternion.clone();
})();

/**
 * The zoom at which `box` fills a `width` x `height` canvas seen with `orientation` (an
 * orthographic camera shows width/zoom world units across).
 */
export function fitZoom(box: Box3, orientation: Quaternion, width: number, height: number, margin = OVERVIEW_MARGIN): number {
  const view = new Matrix4().makeRotationFromQuaternion(orientation).invert();
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const x of [box.min.x, box.max.x]) {
    for (const y of [box.min.y, box.max.y]) {
      for (const z of [box.min.z, box.max.z]) {
        const p = new Vector3(x, y, z).applyMatrix4(view);
        minX = Math.min(minX, p.x);
        maxX = Math.max(maxX, p.x);
        minY = Math.min(minY, p.y);
        maxY = Math.max(maxY, p.y);
      }
    }
  }
  return Math.min(width / (maxX - minX), height / (maxY - minY)) / margin;
}

/** Keep the orbit target over the room (panning cannot lose it). */
export function clampTarget(target: Vector3): Vector3 {
  target.x = Math.min(ROOM.maxX, Math.max(ROOM.minX, target.x));
  target.z = Math.min(ROOM.maxZ, Math.max(ROOM.minZ, target.z));
  target.y = Math.min(2, Math.max(0, target.y));
  return target;
}

export interface Framing {
  target: Vector3;
  zoom: number;
  /** From the target towards the camera (unit). Omitted: keep the current viewing angle. */
  direction?: Vector3;
}

/** Ease in and out, 0..1 -> 0..1. */
export const ease = (t: number) => (t <= 0 ? 0 : t >= 1 ? 1 : t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/** A camera move from one framing to another over FOCUS_MS; interruptible (the user drags). */
export class CameraTween {
  private from: Framing | null = null;
  private to: Framing | null = null;
  private startedAt = 0;

  start(from: Framing, to: Framing, now: number): void {
    this.from = { target: from.target.clone(), zoom: from.zoom, direction: from.direction?.clone() };
    this.to = { target: to.target.clone(), zoom: to.zoom, direction: to.direction?.clone() };
    this.startedAt = now;
  }

  get active(): boolean {
    return this.to !== null;
  }

  /** Retarget a running move (follow a walking agent) without restarting it. */
  retarget(target: Vector3): void {
    this.to?.target.copy(target);
  }

  cancel(): void {
    this.from = this.to = null;
  }

  /** The framing at `now`, or null when no move is running (it ends by itself). */
  at(now: number): Framing | null {
    if (!this.from || !this.to) return null;
    const t = ease((now - this.startedAt) / FOCUS_MS);
    const framing: Framing = { target: this.from.target.clone().lerp(this.to.target, t), zoom: this.from.zoom + (this.to.zoom - this.from.zoom) * t };
    if (this.from.direction && this.to.direction) framing.direction = this.from.direction.clone().lerp(this.to.direction, t).normalize();
    if (t >= 1) this.cancel();
    return framing;
  }
}
