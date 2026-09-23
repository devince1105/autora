// What stands where on the 2D floor, in canvas pixels (T-410; D-026, D-027).
//
// The 2D office is a projection of the 3D one. Every piece of furniture, and where it stands, comes
// from the bake: it rendered the pieces the 3D office is built from, from one fixed camera, and
// wrote down ``placedPieces()`` beside them — so this needs no three.js, which is the point of a
// view that has to work where WebGL does not.
//
// So there is no layout here to keep in step with the 3D office: move a plant in
// ``scene/layout.ts``, re-bake, and it moves in both views. This module only works out where each
// sprite lands on the canvas, who sits where, and which room each thing belongs to. It is pure,
// and tested without a canvas; the drawing (``PixelFloor``) reads this and paints.
//
// The camera looks down from about 61°, so a metre of floor is ``TILE`` pixels across and
// ``TILE_DEPTH`` deep, and a thing's height shows as its face above its footprint. Depth is by
// feet: whatever stands further forward is painted later and covers what is behind.
import { STAND_BACK } from "../agents/body";
import { characterFor } from "../assets/characters";
import { ROLE_COLOR } from "../palette";
import { figure, placeFigure } from "./art/people";
import { BACKDROP_KEY, piece, PLACED } from "./art/pieces";
import type { BoardCard, FloorPlan, PlanRoom } from "./board";

/** Pixels per metre across. The bake's resolution (``tools/bake-sprites``). */
export const TILE = 32;
/** Pixels per metre of floor going back: 32 × sin(61.04°). */
export const TILE_DEPTH = 28;

/** A baked piece where it stands on the canvas. */
export interface Prop {
  /** What it is, in the 3D office's words: desk, chair, lounge, fridge, glass, wall… */
  kind: string;
  /** Which baked sprite (``art/pieces``). */
  bake: string;
  /** Top-left of the sprite, canvas pixels. */
  x: number;
  y: number;
  /** Canvas row of its feet: what the scene is painted back to front by. */
  foot: number;
  zone: string | null;
  /** The colour its user-coloured parts are painted in: a chair's stripes are its sitter's. */
  accent?: string;
}

export interface Npc {
  agentId: string;
  /** The floor under them: the seat when seated, behind the chair when standing. Canvas pixels. */
  spot: { x: number; y: number };
  /** The box their figure covers — what a click hits and the selection frame goes round. */
  x: number;
  y: number;
  w: number;
  h: number;
  foot: number;
  /** Which of the 3D office's characters they wear (``assets/characters``): the same one as in 3D. */
  character: string;
  color: string;
  zone: string | null;
  sitting: boolean;
}

export interface SceneRoom {
  id: string;
  kind: PlanRoom["kind"];
  /** The room's floor on the canvas: where the focus overlay leaves a hole. */
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Scene {
  width: number;
  height: number;
  /** Where the backdrop's top-left is drawn. */
  backdrop: { x: number; y: number };
  rooms: SceneRoom[];
  props: Prop[];
  npcs: Npc[];
}

const BACK = piece(BACKDROP_KEY);

/**
 * Where a point of the office floor lands on the canvas, from world metres.
 *
 * Everything hangs off the backdrop: it was baked with the world's origin at a known pixel, and
 * it is drawn with its top-left at the canvas's. So a metre to the right is ``TILE`` pixels right
 * of that pixel, and a metre back is ``TILE_DEPTH`` up.
 */
export function worldToPixels(x: number, z: number): { x: number; y: number } {
  const [ox, oy] = BACK?.origin ?? [0, 0];
  return { x: Math.round(ox + x * TILE), y: Math.round(oy + z * TILE_DEPTH) };
}

/** The same, from the floor plan's metres (which count from the plan's corner, not the middle). */
export function planToPixels(x: number, z: number, plan: Pick<FloorPlan, "origin">): { x: number; y: number } {
  return worldToPixels(x + plan.origin.x, z + plan.origin.z);
}

/** The 3D office's names for its decor, shortened to what the 2D board calls them. */
function kindOf(bake: string): string {
  if (bake.startsWith("decor:")) return bake.split(":")[2];
  if (bake.startsWith("bench:")) return "bench";
  if (bake.startsWith("wall:")) return "wall";
  if (bake.startsWith("front:")) return "glass";
  if (bake === "counterDesk") return "counter";
  if (bake === "execDesk" || bake === "benchSeat") return "desk";
  if (bake === "chairTall") return "chair";
  return bake;
}

/** Which room a point of the plan is in, or null for the corridors. */
function roomAt(rooms: readonly PlanRoom[], x: number, z: number): string | null {
  // the walled rooms first: they sit inside the open plan's bounding boxes at the back
  const ordered = [...rooms].sort((a, b) => (a.kind === b.kind ? 0 : a.kind === "walled" ? -1 : 1));
  const hit = ordered.find((r) => x >= r.box.left && x <= r.box.left + r.box.width && z >= r.box.top && z <= r.box.top + r.box.height);
  return hit?.id ?? null;
}

/** The glass fronts face the corridor: each belongs to the room behind it (see ``GLASS_FRONTS``). */
const FRONT_ROOM = ["ceo", "meeting"] as const;

/**
 * The floor: the backdrop, every piece of furniture where the 3D office puts it, and the people
 * at their desks.
 */
export function buildScene(
  plan: FloorPlan,
  cards: Map<string, BoardCard>,
  /** agent -> character, from the roster; an agent missing from it wears ``characterFor(id)``. */
  characters: ReadonlyMap<string, string> = new Map(),
): Scene {
  const seats = new Map(plan.desks.map((desk) => [desk.key, desk]));
  const props: Prop[] = [];

  for (const placed of PLACED) {
    const art = piece(placed.bake);
    if (!art) continue; // not baked yet: the board shows what it has rather than failing
    const spot = worldToPixels(placed.at[0], placed.at[1]);
    const x = spot.x - art.origin[0];
    const y = spot.y - art.origin[1];
    const seat = placed.seat ? seats.get(placed.seat) : undefined;
    const planX = placed.at[0] - plan.origin.x;
    const planZ = placed.at[1] - plan.origin.z;
    const front = placed.bake.startsWith("front:") ? FRONT_ROOM[Number(placed.bake.split(":")[1])] : undefined;
    const sitter = seat?.agentId ? cards.get(seat.agentId) : undefined;
    props.push({
      kind: kindOf(placed.bake),
      bake: placed.bake,
      x,
      y,
      foot: y + art.anchor,
      zone: seat?.zone ?? front ?? roomAt(plan.rooms, planX, planZ),
      accent: kindOf(placed.bake) === "chair" ? (sitter?.color ?? ROLE_COLOR.spare) : undefined,
    });
  }

  // the people at their desks, in their own characters: seated behind their chair's back, facing
  // their screens as in 3D; or, when done, standing up behind the chair
  const npcs: Npc[] = [];
  for (const desk of plan.desks) {
    const card = desk.agentId ? cards.get(desk.agentId) : undefined;
    if (!card) continue;
    const sitting = card.visual.pose !== "stand";
    const character = characters.get(card.id) ?? characterFor(card.id);
    const spot = planToPixels(desk.chair.cx, desk.chair.cy + (sitting ? 0 : STAND_BACK), plan);
    const drawn = figure(character, sitting ? "sit" : "stand", "away");
    const box = drawn ? { ...placeFigure(drawn, spot), w: drawn.piece.w, h: drawn.piece.h } : { x: spot.x - 12, y: spot.y - 40, w: 24, h: 44 };
    npcs.push({
      agentId: card.id,
      spot,
      ...box,
      // the seat's middle, not its front: the chair's back is further forward and covers them
      foot: spot.y,
      character,
      color: card.color,
      zone: desk.zone,
      sitting,
    });
  }

  const rooms: SceneRoom[] = plan.rooms.map((room) => {
    const topLeft = planToPixels(room.box.left, room.box.top, plan);
    const bottomRight = planToPixels(room.box.left + room.box.width, room.box.top + room.box.height, plan);
    return { id: room.id, kind: room.kind, x: topLeft.x, y: topLeft.y, width: bottomRight.x - topLeft.x, height: bottomRight.y - topLeft.y };
  });

  // one metre is one tile across, so the canvas is a whole number of them
  const width = Math.ceil((BACK?.w ?? plan.width * TILE) / TILE) * TILE;
  const height = BACK?.h ?? Math.ceil(plan.height * TILE_DEPTH);
  return { width, height, backdrop: { x: 0, y: 0 }, rooms, props, npcs };
}

/** Who was clicked, in canvas pixels. Null when it was the floor. */
export function hitTest(scene: Scene, x: number, y: number): string | null {
  // a few pixels of slack: at this size a figure is a small target
  for (const npc of scene.npcs) {
    if (x >= npc.x - 3 && x <= npc.x + npc.w + 3 && y >= npc.y - 3 && y <= npc.y + npc.h + 3) return npc.agentId;
  }
  return null;
}
