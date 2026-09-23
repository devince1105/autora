// The floor as a tile map (T-410 stage 3): what goes on which 16×16 tile, before anything is
// drawn. Pure functions, so the map can be tested without a canvas — the drawing itself
// (``PixelFloor``) then only reads this and fills rectangles.
//
// One tile is one metre of the floor the 3D office is built from, so the two views are the same
// place at different resolutions. The internal canvas is 26×18 tiles = 416×288 pixels, drawn at
// whatever size the page gives it with nearest-neighbour scaling: chunky pixels by construction,
// not a smooth picture with a filter over it.

import * as atlas from "./art/atlas";
import { footOf, type Sprite } from "./art/sprites";
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
  | "palm"
  | "counter"
  | "sofa"
  | "shelf"
  | "cabinet"
  | "fridge"
  | "stove"
  | "coffee"
  | "whiteboard"
  | "lamp"
  | "window"
  | "rug"
  | "papers"
  | "mug"
  | "plate"
  | "box"
  | "door";

export interface Prop {
  kind: PropKind;
  /** The pixels this thing is made of. */
  art: Sprite;
  /** Pixels in the internal canvas, top-left. */
  x: number;
  y: number;
  zone: string | null;
  /** Mirrored, so a row of desks is not the same sprite six times. */
  flip?: boolean;
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

/** The person sprite's size: what a chair seats and what a click has to hit. */
export const NPC = { w: atlas.PERSON[0].w, h: atlas.PERSON[0].h } as const;

/** Where a point of the floor plan lands on the canvas, in internal pixels. */
export function metresToPixels(x: number, z: number, plan: { origin: { x: number; z: number } }): { x: number; y: number } {
  return { x: pxOf(x - plan.origin.x), y: pxOf(z - plan.origin.z) };
}

/** What the scene is drawn back to front by: a thing's feet, so nearer things cover farther. */
export function foot(item: { y: number; art: Sprite }): number {
  return item.y + footOf(item.art);
}

const ART: Record<PropKind, Sprite> = {
  desk: atlas.DESK,
  chair: atlas.CHAIR,
  plant: atlas.PLANT,
  palm: atlas.PALM,
  counter: atlas.COUNTER,
  sofa: atlas.SOFA,
  shelf: atlas.SHELF,
  cabinet: atlas.CABINET,
  fridge: atlas.FRIDGE,
  stove: atlas.STOVE,
  coffee: atlas.COFFEE,
  whiteboard: atlas.WHITEBOARD,
  lamp: atlas.LAMP,
  window: atlas.WINDOW,
  rug: atlas.RUG,
  papers: atlas.PAPERS,
  mug: atlas.MUG,
  plate: atlas.PLATE,
  box: atlas.BOX,
  door: atlas.DOOR,
};

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

  // the door in the right-hand wall, and the floor tile it opens onto
  const props: Prop[] = [place("door", plan.entrance.left, plan.entrance.top + 0.3, "lobby")];
  for (let row = tileOf(plan.entrance.top); row <= tileOf(plan.entrance.top + plan.entrance.height - 0.01); row++) {
    if (row >= 0 && row < rows) map.tiles[row * cols + (cols - MARGIN)] = "floor";
  }
  // windows along the top wall: the room has an outside
  for (let col = 3; col < cols - 4; col += 5) {
    props.push({ kind: "window", art: ART.window, x: col * TILE, y: (MARGIN - 1) * TILE + 5, zone: null });
  }

  const npcs: Npc[] = [];
  for (const [index, desk] of plan.desks.entries()) {
    props.push(deskProp(desk, index % 2 === 1));
    props.push({
      kind: "chair",
      art: ART.chair,
      x: pxOf(desk.chair.cx) - Math.round(ART.chair.w / 2),
      y: pxOf(desk.chair.cy) - Math.round(ART.chair.h / 2),
      zone: desk.zone,
    });
    // a desk is somebody's: papers on one, a mug on the next
    props.push(
      index % 2 === 0
        ? { kind: "papers", art: ART.papers, x: pxOf(desk.desk.left) + 20, y: pxOf(desk.desk.top) + 11, zone: desk.zone }
        : { kind: "mug", art: ART.mug, x: pxOf(desk.desk.left) + 4, y: pxOf(desk.desk.top) + 12, zone: desk.zone },
    );
    const card = desk.agentId ? cards.get(desk.agentId) : undefined;
    if (card) {
      npcs.push({
        agentId: card.id,
        x: pxOf(desk.chair.cx) - NPC.w / 2,
        y: pxOf(desk.chair.cy) - NPC.h + 6,
        color: card.color,
        zone: desk.zone,
        sitting: true,
      });
    }
  }

  const room = (id: string) => plan.rooms.find((r) => r.id === id);
  const lobby = room("lobby");
  if (lobby) {
    // reception: the counter, what stands on it, and a place to wait
    const counterTiles = Math.max(1, Math.round(plan.reception.width));
    for (let i = 0; i < counterTiles; i++) {
      props.push({ kind: "counter", art: ART.counter, x: pxOf(plan.reception.left) + i * TILE, y: pxOf(plan.reception.top), zone: "lobby" });
    }
    props.push({ kind: "coffee", art: ART.coffee, x: pxOf(plan.reception.left) + 2, y: pxOf(plan.reception.top) - 11, zone: "lobby" });
    props.push({ kind: "plate", art: ART.plate, x: pxOf(plan.reception.left) + 22, y: pxOf(plan.reception.top) - 3, zone: "lobby" });
    props.push({ kind: "mug", art: ART.mug, x: pxOf(plan.reception.left) + 32, y: pxOf(plan.reception.top) - 4, zone: "lobby" });
    rug(props, lobby.box.left + 0.6, lobby.box.top + lobby.box.height - 3.4, 3, 2, "lobby");
    props.push(place("sofa", lobby.box.left + 0.7, lobby.box.top + lobby.box.height - 3.0, "lobby"));
    props.push(place("lamp", lobby.box.left + 3.2, lobby.box.top + lobby.box.height - 3.2, "lobby"));
    props.push(place("palm", lobby.box.left + lobby.box.width - 1.6, lobby.box.top + lobby.box.height - 2.4, "lobby"));
    props.push(place("cabinet", lobby.box.left + 4.4, lobby.box.top + 0.4, "lobby"));
  }
  const pantry = room("pantry");
  if (pantry) {
    // the kitchen: things along the back wall, a counter with cups down the front
    props.push(place("fridge", pantry.box.left + 0.6, pantry.box.top + 0.8, "pantry"));
    props.push(place("stove", pantry.box.left + 2.2, pantry.box.top + 0.9, "pantry"));
    const run = Math.max(1, Math.round(pantry.box.width) - 2);
    for (let i = 0; i < run; i++) {
      props.push({ kind: "counter", art: ART.counter, x: pxOf(pantry.box.left + 0.6) + i * TILE, y: pxOf(pantry.box.top + pantry.box.height - 1.7), zone: "pantry" });
    }
    props.push({ kind: "mug", art: ART.mug, x: pxOf(pantry.box.left + 1.2), y: pxOf(pantry.box.top + pantry.box.height - 1.7) - 4, zone: "pantry" });
    props.push({ kind: "plate", art: ART.plate, x: pxOf(pantry.box.left + 2.4), y: pxOf(pantry.box.top + pantry.box.height - 1.7) - 3, zone: "pantry" });
    props.push(place("plant", pantry.box.left + pantry.box.width - 1.4, pantry.box.top + 1.6, "pantry"));
  }
  const meeting = room("meeting");
  if (meeting) {
    props.push(place("whiteboard", meeting.box.left + 1.0, meeting.box.top + 0.5, "meeting"));
    rug(props, meeting.box.left + meeting.box.width / 2 - 2, meeting.box.top + meeting.box.height / 2 - 1.2, 4, 2, "meeting");
    // a meeting table is desks end to end, with chairs down both sides
    for (let i = 0; i < 3; i++) {
      const x = meeting.box.left + meeting.box.width / 2 - 1.5 + i;
      props.push(place("desk", x, meeting.box.top + meeting.box.height / 2 - 0.5, "meeting"));
      props.push(place("chair", x, meeting.box.top + meeting.box.height / 2 + 0.6, "meeting"));
    }
    props.push(place("plant", meeting.box.left + meeting.box.width - 1.4, meeting.box.top + 1.4, "meeting"));
  }
  const ceo = room("ceo");
  if (ceo) {
    props.push(place("shelf", ceo.box.left + 0.6, ceo.box.top + 0.6, "ceo"));
    props.push(place("cabinet", ceo.box.left + 2.6, ceo.box.top + 0.6, "ceo"));
    rug(props, ceo.box.left + 0.8, ceo.box.top + ceo.box.height - 2.6, 3, 2, "ceo");
    props.push(place("palm", ceo.box.left + ceo.box.width - 1.6, ceo.box.top + ceo.box.height - 2.2, "ceo"));
    props.push(place("lamp", ceo.box.left + 0.5, ceo.box.top + ceo.box.height - 2.4, "ceo"));
  }
  // the open plan: something in every corner, so no stretch of floor is bare
  for (const zone of plan.rooms.filter((r) => r.kind === "open" && r.id !== "lobby")) {
    props.push(place("plant", zone.box.left + 0.3, zone.box.top + 0.4, zone.id));
    props.push(place("cabinet", zone.box.left + zone.box.width - 1.6, zone.box.top + 0.3, zone.id));
    props.push(place("box", zone.box.left + zone.box.width - 1.2, zone.box.top + zone.box.height - 1.2, zone.id));
  }
  for (const [x, y] of [
    [0.3, plan.height - 1.8],
    [plan.width - 1.5, plan.height - 1.8],
    [0.3, 0.5],
  ] as const) {
    props.push(place("palm", x, y, null));
  }
  // along the corridors: what an office leaves in a walkway — a plant, a bin, a box
  for (const corridor of plan.corridors) {
    const middle = corridor.top + corridor.height / 2;
    for (let x = corridor.left + 2.5; x < corridor.left + corridor.width - 2; x += 6.5) {
      props.push(place("plant", x, middle - 0.2, null));
      props.push(place("box", x + 3.2, middle + 0.1, null));
    }
  }

  return { width: cols * TILE, height: rows * TILE, map, props, npcs };
}

/** A rug is laid tile by tile, like a floor: it is part of the ground, not a thing on it. */
function rug(props: Prop[], left: number, top: number, cols: number, rows: number, zone: string) {
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      props.push({ kind: "rug", art: ART.rug, x: pxOf(left) + col * TILE, y: pxOf(top) + row * TILE, zone });
    }
  }
}

/** A thing standing on the floor at these metres, drawn from its middle. */
function place(kind: PropKind, left: number, top: number, zone: string | null): Prop {
  const art = ART[kind];
  return { kind, art, x: pxOf(left), y: pxOf(top) - art.h + TILE, zone };
}

function deskProp(desk: PlanDesk, flip: boolean): Prop {
  return {
    kind: "desk",
    art: ART.desk,
    x: pxOf(desk.desk.left),
    y: pxOf(desk.desk.top) - ART.desk.h + TILE + 2,
    zone: desk.zone,
    flip,
  };
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
