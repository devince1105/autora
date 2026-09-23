// The floor as a tile map (T-410 stage 3): what goes on which 16×16 tile, before anything is
// drawn. Pure functions, so the map can be tested without a canvas — the drawing itself
// (``PixelFloor``) then only reads this and fills rectangles.
//
// One tile is one metre of the floor the 3D office is built from, so the two views are the same
// place at different resolutions. The internal canvas is 26×18 tiles = 416×288 pixels, drawn at
// whatever size the page gives it with nearest-neighbour scaling: chunky pixels by construction,
// not a smooth picture with a filter over it.

import type { BoardCard, FloorPlan, PlanDesk } from "./board";

export const TILE = 16;
/** A metre of floor is a tile; the walls sit in the one-tile margin around it. */
export const MARGIN = 1;

export type TileKind =
  | "outside"
  | "wall"
  | "floor"
  | "floor_alt"
  | "corridor"
  | "carpet"
  | "room_floor";

export interface TileMap {
  cols: number;
  rows: number;
  /** ``rows × cols``; index with ``at(map, col, row)``. */
  tiles: TileKind[];
  /** Which room each tile belongs to, for lighting the chosen one. */
  zone: (string | null)[];
}

export type PropKind =
  | "desk"
  | "chair"
  | "plant"
  | "counter"
  | "sofa"
  | "shelf"
  | "fridge"
  | "stove"
  | "whiteboard"
  | "door";

export interface Prop {
  kind: PropKind;
  /** Pixels in the internal canvas, top-left. */
  x: number;
  y: number;
  w: number;
  h: number;
  zone: string | null;
  /** Which way it faces, for the props that have a front (desks, counters). */
  facing?: "up" | "down";
}

export interface Npc {
  agentId: string;
  /** Pixels in the internal canvas: the sprite's top-left corner. */
  x: number;
  y: number;
  color: string;
  zone: string | null;
  sitting: boolean;
}

export interface Scene {
  width: number;
  height: number;
  map: TileMap;
  props: Prop[];
  npcs: Npc[];
}

export const NPC = { w: 10, h: 14 } as const;

const tileOf = (metres: number) => Math.floor(metres) + MARGIN;
const pxOf = (metres: number) => Math.round((metres + MARGIN) * TILE);

export function at(map: TileMap, col: number, row: number): TileKind {
  if (col < 0 || row < 0 || col >= map.cols || row >= map.rows) return "outside";
  return map.tiles[row * map.cols + col];
}

export function zoneAt(map: TileMap, col: number, row: number): string | null {
  if (col < 0 || row < 0 || col >= map.cols || row >= map.rows) return null;
  return map.zone[row * map.cols + col];
}

function fill(map: TileMap, box: { left: number; top: number; width: number; height: number }, kind: TileKind, zone: string | null) {
  const from = { col: tileOf(box.left), row: tileOf(box.top) };
  const to = { col: tileOf(box.left + box.width - 0.01), row: tileOf(box.top + box.height - 0.01) };
  for (let row = from.row; row <= to.row; row++) {
    for (let col = from.col; col <= to.col; col++) {
      if (col < 0 || row < 0 || col >= map.cols || row >= map.rows) continue;
      map.tiles[row * map.cols + col] = kind;
      if (zone) map.zone[row * map.cols + col] = zone;
    }
  }
}

function outline(map: TileMap, box: { left: number; top: number; width: number; height: number }, zone: string) {
  const left = tileOf(box.left);
  const right = tileOf(box.left + box.width - 0.01);
  const top = tileOf(box.top);
  const bottom = tileOf(box.top + box.height - 0.01);
  for (let col = left; col <= right; col++) {
    for (const row of [top, bottom]) {
      if (col >= 0 && row >= 0 && col < map.cols && row < map.rows) {
        map.tiles[row * map.cols + col] = "wall";
        map.zone[row * map.cols + col] = zone;
      }
    }
  }
  for (let row = top; row <= bottom; row++) {
    for (const col of [left, right]) {
      if (col >= 0 && row >= 0 && col < map.cols && row < map.rows) {
        map.tiles[row * map.cols + col] = "wall";
        map.zone[row * map.cols + col] = zone;
      }
    }
  }
}

/**
 * The floor plan as tiles, props and the people at their desks.
 *
 * Walled rooms keep their walls (the back three), open zones get a carpet, corridors get their
 * own tile, and the door is a gap in the right-hand wall. The furniture a room needs to look
 * like that room — a counter in reception, a fridge and a stove in the pantry, a whiteboard in
 * the meeting room, sofas in the lounge — is placed from the room's own box, not hand-tuned
 * coordinates, so it follows if the floor plan changes.
 */
export function buildScene(plan: FloorPlan, cards: Map<string, BoardCard>): Scene {
  const cols = Math.ceil(plan.width) + MARGIN * 2;
  const rows = Math.ceil(plan.height) + MARGIN * 2;
  const map: TileMap = {
    cols,
    rows,
    tiles: Array.from({ length: cols * rows }, () => "outside" as TileKind),
    zone: Array.from({ length: cols * rows }, () => null as string | null),
  };

  // the slab, in two shades so the grid is visible
  for (let row = MARGIN; row < rows - MARGIN; row++) {
    for (let col = MARGIN; col < cols - MARGIN; col++) {
      map.tiles[row * map.cols + col] = (col + row) % 2 === 0 ? "floor" : "floor_alt";
    }
  }
  // the outer walls, one tile thick
  for (let col = 0; col < cols; col++) {
    map.tiles[(MARGIN - 1) * cols + col] = "wall";
    map.tiles[(rows - MARGIN) * cols + col] = "wall";
  }
  for (let row = MARGIN - 1; row <= rows - MARGIN; row++) {
    map.tiles[row * cols + (MARGIN - 1)] = "wall";
    map.tiles[row * cols + (cols - MARGIN)] = "wall";
  }

  for (const room of plan.rooms) {
    fill(map, room.box, room.kind === "walled" ? "room_floor" : "carpet", room.id);
    if (room.kind === "walled") outline(map, room.box, room.id);
  }
  for (const corridor of plan.corridors) fill(map, corridor, "corridor", null);

  // the door: a gap in the right-hand wall, in the console's accent when drawn
  const props: Prop[] = [
    {
      kind: "door",
      x: pxOf(plan.entrance.left),
      y: pxOf(plan.entrance.top),
      w: TILE,
      h: Math.max(TILE, Math.round(plan.entrance.height * TILE)),
      zone: "lobby",
    },
  ];
  for (let row = tileOf(plan.entrance.top); row <= tileOf(plan.entrance.top + plan.entrance.height - 0.01); row++) {
    if (row >= 0 && row < rows) map.tiles[row * cols + (cols - MARGIN)] = "floor";
  }

  const npcs: Npc[] = [];
  for (const desk of plan.desks) {
    props.push(deskProp(desk));
    props.push({
      kind: "chair",
      x: pxOf(desk.chair.cx - 0.35),
      y: pxOf(desk.chair.cy - 0.3),
      w: Math.round(0.7 * TILE),
      h: Math.round(0.7 * TILE),
      zone: desk.zone,
    });
    const card = desk.agentId ? cards.get(desk.agentId) : undefined;
    if (card) {
      npcs.push({
        agentId: card.id,
        x: pxOf(desk.chair.cx) - NPC.w / 2,
        y: pxOf(desk.chair.cy) - NPC.h + 4,
        color: card.color,
        zone: desk.zone,
        sitting: true,
      });
    }
  }

  const room = (id: string) => plan.rooms.find((r) => r.id === id);
  const lobby = room("lobby");
  if (lobby) {
    props.push({
      kind: "counter",
      x: pxOf(plan.reception.left),
      y: pxOf(plan.reception.top),
      w: Math.round(plan.reception.width * TILE),
      h: Math.round(plan.reception.height * TILE),
      zone: "lobby",
      facing: "down",
    });
    props.push(sized("sofa", lobby.box.left + 0.6, lobby.box.top + lobby.box.height - 2.2, 2.6, 1.0, "lobby"));
    props.push(sized("plant", lobby.box.left + 3.8, lobby.box.top + lobby.box.height - 2.0, 0.8, 1.2, "lobby"));
  }
  const pantry = room("pantry");
  if (pantry) {
    props.push(sized("fridge", pantry.box.left + 0.8, pantry.box.top + 1.0, 1.0, 1.4, "pantry"));
    props.push(sized("stove", pantry.box.left + 2.4, pantry.box.top + 1.0, 1.4, 1.0, "pantry"));
    props.push(sized("counter", pantry.box.left + 0.8, pantry.box.top + pantry.box.height - 1.6, pantry.box.width - 1.6, 0.8, "pantry", "down"));
  }
  const meeting = room("meeting");
  if (meeting) {
    props.push(sized("whiteboard", meeting.box.left + 1.0, meeting.box.top + 0.6, meeting.box.width - 2.0, 0.5, "meeting"));
    props.push(sized("desk", meeting.box.left + meeting.box.width / 2 - 1.6, meeting.box.top + meeting.box.height / 2 - 0.6, 3.2, 1.2, "meeting"));
  }
  const ceo = room("ceo");
  if (ceo) {
    props.push(sized("shelf", ceo.box.left + 0.6, ceo.box.top + 0.6, 1.6, 0.5, "ceo"));
    props.push(sized("plant", ceo.box.left + ceo.box.width - 1.4, ceo.box.top + ceo.box.height - 1.8, 0.8, 1.2, "ceo"));
  }
  for (const [x, y] of [
    [0.4, plan.height - 1.6],
    [plan.width - 1.4, plan.height - 1.6],
    [0.4, 0.6],
  ] as const) {
    props.push(sized("plant", x, y, 0.8, 1.2, null));
  }

  return { width: cols * TILE, height: rows * TILE, map, props, npcs };
}

function deskProp(desk: PlanDesk): Prop {
  return {
    kind: "desk",
    x: pxOf(desk.desk.left),
    y: pxOf(desk.desk.top),
    w: Math.round(desk.desk.width * TILE),
    h: Math.round(desk.desk.height * TILE),
    zone: desk.zone,
    facing: desk.chair.cy > desk.desk.top ? "down" : "up",
  };
}

function sized(kind: PropKind, left: number, top: number, w: number, h: number, zone: string | null, facing?: "up" | "down"): Prop {
  return { kind, x: pxOf(left), y: pxOf(top), w: Math.round(w * TILE), h: Math.round(h * TILE), zone, facing };
}

/** Who was clicked, in internal canvas pixels. Null when it was the floor. */
export function hitTest(scene: Scene, x: number, y: number): string | null {
  for (const npc of scene.npcs) {
    if (x >= npc.x - 3 && x <= npc.x + NPC.w + 3 && y >= npc.y - 3 && y <= npc.y + NPC.h + 5) {
      return npc.agentId;
    }
  }
  return null;
}
