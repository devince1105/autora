// The people, ready to paint (D-027): the 3D office's twelve characters, baked.
//
// Each agent wears the same character on the 2D floor as in 3D (``roster`` picks it with
// ``characterFor``). A figure is looked up by what it is doing and which way it faces; the left
// profile is the right one mirrored, so it comes back with ``flip``.
import { WALK_FRAMES, PEOPLE } from "./baked-people";
import { expand, type BakedPiece } from "./pieces";

export { WALK_FRAMES };

export type Pose = "sit" | "walk" | "stand";
/** ``left`` is drawn as ``side`` mirrored. */
export type Facing = "toward" | "away" | "right" | "left";

const expanded = new Map<string, BakedPiece>();

function decoded(key: string): BakedPiece | null {
  const cached = expanded.get(key);
  if (cached) return cached;
  const encoded = PEOPLE[key];
  if (!encoded) return null;
  const full = { ...encoded, rows: encoded.rows.map(expand), shadow: encoded.shadow.map(expand) };
  expanded.set(key, full);
  return full;
}

export interface Figure {
  key: string;
  piece: BakedPiece;
  flip: boolean;
}

/** A character doing something, facing somewhere. Null for a character that was never baked. */
export function figure(character: string, pose: Pose, facing: Facing, frame = 0): Figure | null {
  // seated, everybody faces their screens: the back view is the only one baked
  const dir = pose === "sit" ? "away" : facing === "left" || facing === "right" ? "side" : facing;
  const step = pose === "walk" ? ((frame % WALK_FRAMES) + WALK_FRAMES) % WALK_FRAMES : 0;
  const key = `${character}|${pose}|${dir}|${step}`;
  const piece = decoded(key);
  return piece ? { key, piece, flip: pose !== "sit" && facing === "left" } : null;
}

/**
 * Which way somebody walking on ``heading`` faces the camera. The heading is the 3D office's
 * (``courier``): 0 walks toward the viewer (+z), a quarter turn walks right (+x).
 */
export function facingOf(heading: number): Facing {
  const turn = Math.atan2(Math.sin(heading), Math.cos(heading)); // -π..π
  if (Math.abs(turn) <= Math.PI / 4) return "toward";
  if (Math.abs(turn) >= (3 * Math.PI) / 4) return "away";
  return turn > 0 ? "right" : "left";
}

/** Where a figure's top-left goes for its feet to stand at ``spot``, mirrored or not. */
export function placeFigure(f: Figure, spot: { x: number; y: number }): { x: number; y: number } {
  const originX = f.flip ? f.piece.w - f.piece.origin[0] : f.piece.origin[0];
  return { x: spot.x - originX, y: spot.y - f.piece.origin[1] };
}

export const PEOPLE_KEYS: readonly string[] = Object.keys(PEOPLE);
