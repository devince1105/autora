// The office's furniture and architecture as primitive parts (T-402, D-010). Every piece is built
// around its own origin, facing +z (a chair's sitter faces -z: its back is on the +z side), and
// placed from layout.ts. Nothing here knows about agents or state.
import { Matrix4 } from "three";

import { DEFAULT_THEME, ROLE_COLOR, THEMES, type FloorKind, type Palette } from "../palette";
import { block, box, cyl, place, sphere, type Part } from "./kit";
import {
  allSeats,
  APPROVAL_DESK,
  BACK_ROOMS_Z,
  BENCHES,
  CEO_OFFICE,
  DECOR,
  DESK,
  doorLeaves,
  DOORS,
  ENTRANCE,
  MEETING_ROOM,
  PANTRY,
  ROOM,
  WALL_HALF,
  ZONES,
  type Decor,
  type Seat,
} from "./layout";

/**
 * The palette the builders below paint with. Set by the exported entry points (officeParts and
 * the glass) for the duration of one build, so the builders need not pass it along.
 */
let P: Palette = THEMES[DEFAULT_THEME].palette;

function paintedWith<T>(palette: Palette, build: () => T): T {
  const previous = P;
  P = palette;
  try {
    return build();
  } finally {
    P = previous;
  }
}
const TOP = DESK.height; // desk top surface

/** A deterministic pseudo-random sequence, so the books look the same on every load. */
function seeded(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647;
  };
}

/** A leaf: a flat blade from the origin along +z, tilted by `pitch` (negative = up). */
function leaf(length: number, width: number, pitch: number, color: string): Part {
  const m = new Matrix4().makeRotationX(pitch).multiply(new Matrix4().makeTranslation(0, 0, length / 2));
  return { shape: { kind: "box", w: width, h: 0.012, d: length }, matrix: m, color };
}

function rosette(count: number, length: number, width: number, pitch: (i: number) => number, y: number, color: (i: number) => string, twist = 0): Part[] {
  return Array.from({ length: count }, (_, i) => place([leaf(length, width, pitch(i), color(i))], 0, 0, twist + (i * Math.PI * 2) / count, y)).flat();
}

// --- seating & desks ---------------------------------------------------------------------------

/** An office chair: five-star base, gas lift, seat, back with two accent stripes, arms. */
export function officeChair(accent: string, tall = false): Part[] {
  const parts: Part[] = [];
  for (let i = 0; i < 5; i++) {
    const a = (i * Math.PI * 2) / 5;
    parts.push(box(0.05, 0.035, 0.3, [Math.sin(a) * 0.15, 0.06, Math.cos(a) * 0.15], P.metal, [0, a, 0]));
    parts.push(sphere(0.03, [Math.sin(a) * 0.29, 0.03, Math.cos(a) * 0.29], P.metal, undefined, 6));
  }
  const backH = tall ? 0.78 : 0.6;
  parts.push(
    cyl(0.025, 0.025, 0.32, [0, 0.07, 0], P.metal, 6),
    block(0.52, 0.08, 0.5, [0, 0.38, 0], P.chair),
    box(0.48, backH, 0.07, [0, 0.47 + backH / 2, 0.26], P.chair, [-0.1, 0, 0]),
    box(0.07, backH - 0.08, 0.075, [-0.14, 0.47 + backH / 2, 0.265], accent, [-0.1, 0, 0]),
    box(0.07, backH - 0.08, 0.075, [0.14, 0.47 + backH / 2, 0.265], accent, [-0.1, 0, 0]),
  );
  for (const side of [-1, 1]) {
    parts.push(block(0.04, 0.2, 0.04, [side * 0.28, 0.44, 0.02], P.metal), block(0.07, 0.03, 0.28, [side * 0.28, 0.63, 0.02], P.chair));
  }
  return parts;
}

/** A black metal leg frame under a table, across its depth. */
function legFrame(x: number, depth: number, height = TOP - 0.03): Part[] {
  const z = depth / 2 - 0.06;
  return [
    block(0.05, height, 0.05, [x, 0, -z], P.metal),
    block(0.05, height, 0.05, [x, 0, z], P.metal),
    box(0.05, 0.05, depth - 0.1, [x, 0.04, 0], P.metal),
    box(0.05, 0.05, depth - 0.1, [x, height - 0.03, 0], P.metal),
  ];
}

/** A lit strip along a desk top's front edge (the sitter's side, +z), in a theme that has one. */
function deskEdge(length: number, depth: number, top = TOP): Part[] {
  return P.deskEdge ? [box(length, 0.015, 0.015, [0, top - 0.02, depth / 2 + 0.006], P.deskEdge)] : [];
}

function benchTable(minX: number, maxX: number, z: number): Part[] {
  const length = maxX - minX;
  const parts: Part[] = [block(length, 0.05, DESK.depth, [0, TOP - 0.05, 0], P.deskTop), ...deskEdge(length, DESK.depth)];
  const frames = Math.max(2, Math.round(length / 2.2) + 1);
  for (let i = 0; i < frames; i++) parts.push(...legFrame(-length / 2 + 0.1 + (i * (length - 0.2)) / (frames - 1), DESK.depth));
  return place(parts, (minX + maxX) / 2, z);
}

function singleDesk(): Part[] {
  return [
    block(DESK.width, 0.05, DESK.depth, [0, TOP - 0.05, 0], P.deskTop),
    ...deskEdge(DESK.width, DESK.depth),
    ...legFrame(-DESK.width / 2 + 0.08, DESK.depth),
    ...legFrame(DESK.width / 2 - 0.08, DESK.depth),
    block(0.4, 0.55, 0.5, [0.42, 0.02, -0.05], P.pedestal),
  ];
}

function execDesk(): Part[] {
  return [
    block(DESK.width + 0.2, 0.06, DESK.depth + 0.1, [0, TOP - 0.06, 0], P.execWood),
    ...deskEdge(DESK.width + 0.2, DESK.depth + 0.1),
    block(0.06, TOP - 0.06, DESK.depth, [-DESK.width / 2, 0, 0], P.execWood),
    block(0.06, TOP - 0.06, DESK.depth, [DESK.width / 2, 0, 0], P.execWood),
    block(DESK.width, 0.5, 0.04, [0, 0.15, -DESK.depth / 2 + 0.05], P.execWood),
  ];
}

export const MONITOR = { width: 0.62, height: 0.38, y: TOP + 0.32, spread: 0.33, z: -0.18, turn: 0.2 } as const;

/** Where each of a seat's two screens is, for the screen meshes (T-406 lights them). */
export function screenSpots(seat: Seat): { pos: [number, number, number]; rotY: number }[] {
  return [-1, 1].map((side) => {
    const rotY = -side * MONITOR.turn;
    const x = seat.desk[0] + side * MONITOR.spread + Math.sin(rotY) * 0.02;
    const z = seat.desk[1] + MONITOR.z + Math.cos(rotY) * 0.02;
    return { pos: [x, MONITOR.y, z], rotY };
  });
}

/** Two monitors on stands, a keyboard and a mouse, around a desk centre. */
function workstation(): Part[] {
  const parts: Part[] = [];
  for (const side of [-1, 1]) {
    const monitor = [
      box(MONITOR.width, MONITOR.height, 0.035, [0, MONITOR.y, 0], P.monitor),
      cyl(0.025, 0.025, MONITOR.y - TOP - 0.12, [0, TOP, -0.03], P.metal, 6),
      block(0.22, 0.015, 0.16, [0, TOP, -0.03], P.metal),
    ];
    parts.push(...place(monitor, side * MONITOR.spread, MONITOR.z, -side * MONITOR.turn));
  }
  parts.push(block(0.44, 0.02, 0.14, [0, TOP, 0.16], P.keyboard), block(0.06, 0.02, 0.1, [0.33, TOP, 0.18], P.keyboard));
  return parts;
}

/** The desk lamp at the back right of each desk: its shade and glow are live (T-406). */
export const LAMP = { x: 0.7, z: -0.3, height: 0.42, reach: 0.16 } as const;

export function lampSpot(seat: Seat): { shade: [number, number, number]; glow: [number, number, number] } {
  const x = seat.desk[0] + LAMP.x - LAMP.reach;
  const z = seat.desk[1] + LAMP.z;
  return { shade: [x, TOP + LAMP.height - 0.05, z], glow: [x, TOP + 0.004, z + 0.08] };
}

/** The approval desk's lamp, on its counter: blinks while someone waits for a decision (T-407). */
const APPROVAL_LAMP = { dx: 1.0, dz: 0.2, lift: 1.1 - TOP } as const;

export function approvalLampSpot(): { shade: [number, number, number]; glow: [number, number, number] } {
  const x = APPROVAL_DESK.center[0] + APPROVAL_LAMP.dx - LAMP.reach;
  const z = APPROVAL_DESK.center[1] + APPROVAL_LAMP.dz;
  return { shade: [x, 1.1 + LAMP.height - 0.05, z], glow: [x, 1.1 + 0.004, z - 0.05] };
}

/** The lamp's fixed parts: base, pole and arm (the shade is drawn live). */
function lampBody(): Part[] {
  return [
    cyl(0.08, 0.08, 0.02, [LAMP.x, TOP, LAMP.z], P.metal, 10),
    cyl(0.012, 0.012, LAMP.height, [LAMP.x, TOP, LAMP.z], P.metal, 6),
    box(LAMP.reach + 0.02, 0.02, 0.02, [LAMP.x - LAMP.reach / 2, TOP + LAMP.height, LAMP.z], P.metal),
  ];
}

function seatParts(seat: Seat): Part[] {
  const accent = ROLE_COLOR[seat.role] ?? ROLE_COLOR.spare;
  const [x, z] = seat.desk;
  const desk = seat.bench ? [] : seat.role === "ceo" ? execDesk() : singleDesk();
  return [
    ...place([...desk, ...workstation(), ...lampBody()], x, z),
    ...place(officeChair(accent, seat.role === "ceo"), seat.chair[0], seat.chair[1]),
  ];
}

// --- decoration --------------------------------------------------------------------------------

function books(width: number, y: number, depth: number, rand: () => number): Part[] {
  const parts: Part[] = [];
  let x = -width / 2 + 0.03;
  while (x < width / 2 - 0.08) {
    const w = 0.03 + rand() * 0.04;
    const h = 0.18 + rand() * 0.1;
    const color = P.books[Math.floor(rand() * P.books.length)];
    parts.push(block(w, h, depth * 0.75, [x + w / 2, y, 0], color, [0, 0, rand() < 0.1 ? 0.25 : 0]));
    x += w + 0.005;
  }
  return parts;
}

function smallPlant(pot: string = P.pot, scale = 1): Part[] {
  const parts: Part[] = [cyl(0.17, 0.13, 0.28, [0, 0, 0], pot, 10), cyl(0.16, 0.16, 0.02, [0, 0.26, 0], P.soil, 10)];
  parts.push(...rosette(8, 0.36, 0.16, (i) => -0.8 + (i % 3) * 0.3, 0.3, (i) => (i % 2 ? P.leafLight : P.leaf)));
  return place(parts.map((p) => ({ ...p, matrix: new Matrix4().makeScale(scale, scale, scale).multiply(p.matrix) })), 0, 0);
}

function palm(): Part[] {
  const parts: Part[] = [cyl(0.3, 0.24, 0.6, [0, 0, 0], P.pot, 12), cyl(0.28, 0.28, 0.02, [0, 0.58, 0], P.soil, 12)];
  const stems: [number, number, number][] = [
    [0.05, 2.1, 0.05],
    [-0.08, 1.7, 0.02],
    [0.06, 1.4, -0.07],
  ];
  for (const [dx, h, dz] of stems) {
    parts.push(cyl(0.035, 0.05, h - 0.6, [dx, 0.6, dz], P.trunk, 6));
    parts.push(...place(rosette(11, 0.95, 0.17, (i) => -0.35 + (i % 3) * 0.3, 0, (i) => (i % 3 ? P.leaf : P.leafLight), h), dx, dz, 0, h));
  }
  return parts;
}

function lowShelf(length: number, seed: number): Part[] {
  const h = 0.9;
  const dpt = 0.4;
  const rand = seeded(seed);
  const parts: Part[] = [
    block(length, 0.04, dpt, [0, 0, 0], P.shelfWood),
    block(length, 0.04, dpt, [0, h - 0.04, 0], P.shelfWood),
    block(length, 0.03, dpt, [0, h / 2 - 0.015, 0], P.shelfWood),
    block(length, h, 0.02, [0, 0, -dpt / 2 + 0.01], P.shelfWood),
  ];
  const cells = Math.round(length / 0.45);
  for (let i = 0; i <= cells; i++) parts.push(block(0.03, h, dpt, [-length / 2 + (i * length) / cells, 0, 0], P.shelfWood));
  for (let i = 0; i < cells; i++) {
    const cx = -length / 2 + ((i + 0.5) * length) / cells;
    if (rand() < 0.55) parts.push(...place(books(length / cells - 0.05, 0.04, dpt, rand), cx, 0));
    if (rand() < 0.35) parts.push(...place(books(length / cells - 0.05, h / 2 + 0.015, dpt, rand), cx, 0));
  }
  parts.push(...place(smallPlant(rand() < 0.5 ? P.pot : P.potTerracotta, 0.8), -length / 3, 0, 0, h));
  parts.push(...place(smallPlant(P.pot, 0.7), length / 3, 0, 0, h));
  return parts;
}

function tallShelf(width: number, seed: number, color: string = P.shelfDark): Part[] {
  const h = 2.0;
  const dpt = 0.45;
  const rand = seeded(seed);
  const parts: Part[] = [
    block(0.04, h, dpt, [-width / 2, 0, 0], color),
    block(0.04, h, dpt, [width / 2, 0, 0], color),
    block(width, h, 0.02, [0, 0, -dpt / 2 + 0.01], color),
  ];
  for (let i = 0; i <= 5; i++) {
    const y = i * (h / 5) - (i === 5 ? 0.04 : 0);
    parts.push(block(width, 0.04, dpt, [0, y, 0], color));
    if (i < 5 && rand() < 0.6) parts.push(...books(width * (0.4 + rand() * 0.5), y + 0.04, dpt, rand).map((p) => ({ ...p })));
  }
  return parts;
}

function sofa(length: number, color: string, cushion: string): Part[] {
  const parts: Part[] = [
    block(length, 0.28, 0.85, [0, 0.08, 0], color),
    block(length, 0.5, 0.2, [0, 0.3, -0.33], color),
    block(0.18, 0.5, 0.85, [-length / 2 + 0.09, 0.08, 0], color),
    block(0.18, 0.5, 0.85, [length / 2 - 0.09, 0.08, 0], color),
  ];
  const seats = Math.max(1, Math.round(length / 0.8));
  const inner = length - 0.36;
  for (let i = 0; i < seats; i++) {
    const x = -inner / 2 + ((i + 0.5) * inner) / seats;
    parts.push(block(inner / seats - 0.04, 0.12, 0.62, [x, 0.36, 0.06], cushion));
    parts.push(box(inner / seats - 0.1, 0.38, 0.12, [x, 0.66, -0.2], cushion, [-0.2, 0, 0]));
  }
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) parts.push(block(0.05, 0.08, 0.05, [sx * (length / 2 - 0.1), 0, sz * 0.35], P.metal));
  return parts;
}

function lounge(): Part[] {
  return [
    ...place(sofa(2.2, P.sofa, P.cushion), 1.15, 0, -Math.PI / 2),
    ...place(sofa(0.95, P.armchair, P.cushion), -1.0, -1.1, Math.PI / 2),
    ...place(sofa(0.95, P.armchair, P.cushion), -1.0, 1.1, Math.PI / 2),
    block(1.0, 0.04, 0.7, [0.05, 0.38, 0], P.glassTable),
    ...[-1, 1].flatMap((sx) => [-1, 1].map((sz) => block(0.04, 0.38, 0.04, [0.05 + sx * 0.45, 0, sz * 0.3], P.metal))),
    ...place(smallPlant(P.potTerracotta, 1.1), 1.3, 1.55),
  ];
}

function meetingSet(): Part[] {
  const parts: Part[] = [
    block(4.4, 0.06, 1.3, [0, TOP - 0.06, 0], P.tableWood),
    block(0.12, TOP - 0.06, 0.9, [-1.5, 0, 0], P.metal),
    block(0.12, TOP - 0.06, 0.9, [1.5, 0, 0], P.metal),
    block(0.5, 0.02, 0.35, [-0.8, TOP, 0.1], P.keyboard),
    block(0.5, 0.02, 0.35, [0.9, TOP, -0.15], P.keyboard),
  ];
  for (const x of [-1.4, 0, 1.4]) {
    parts.push(...place(officeChair(P.cushion), x, 1.0));
    parts.push(...place(officeChair(P.cushion), x, -1.0, Math.PI));
  }
  parts.push(...place(officeChair(P.cushion), -2.55, 0, -Math.PI / 2), ...place(officeChair(P.cushion), 2.55, 0, Math.PI / 2));
  return parts;
}

function whiteboard(): Part[] {
  return [
    box(1.5, 0.95, 0.04, [0, 1.45, 0], P.whiteboard),
    box(1.56, 0.04, 0.06, [0, 1.95, 0], P.pedestal),
    box(1.56, 0.04, 0.08, [0, 0.95, 0.02], P.pedestal),
    ...[-0.7, 0.7].flatMap((x) => [block(0.04, 1.95, 0.04, [x, 0, 0], P.pedestal), block(0.05, 0.03, 0.6, [x, 0, 0], P.pedestal)]),
    box(0.3, 0.2, 0.01, [-0.35, 1.55, 0.025], P.picture[0]),
    box(0.4, 0.02, 0.01, [0.3, 1.6, 0.025], P.books[1]),
    box(0.3, 0.02, 0.01, [0.25, 1.45, 0.025], P.books[0]),
  ];
}

function pantryCounter(): Part[] {
  const length = 4.2;
  const parts: Part[] = [
    block(length, 0.85, 0.6, [0, 0, 0], P.counter),
    block(length + 0.05, 0.05, 0.66, [0, 0.85, 0.02], P.counterTop),
    block(0.6, 0.02, 0.4, [-0.9, 0.9, 0.02], P.fridge),
    block(length, 0.7, 0.35, [0, 1.55, -0.12], P.counter),
    block(0.55, 0.32, 0.4, [1.2, 0.9, -0.05], P.keyboard),
  ];
  const doors = 6;
  for (let i = 0; i < doors; i++) {
    const x = -length / 2 + ((i + 0.5) * length) / doors;
    parts.push(box(length / doors - 0.04, 0.7, 0.02, [x, 0.45, 0.305], P.counterFront));
    parts.push(box(length / doors - 0.04, 0.6, 0.02, [x, 1.9, 0.06], P.counterFront));
  }
  return parts;
}

function fridge(): Part[] {
  return [block(0.85, 2.0, 0.75, [0, 0, 0], P.fridge), box(0.03, 0.6, 0.04, [0.3, 1.35, 0.39], P.metal), box(0.03, 0.4, 0.04, [0.3, 0.6, 0.39], P.metal), box(0.84, 0.01, 0.01, [0, 1.0, 0.38], P.metal)];
}

function vending(): Part[] {
  return [
    block(0.9, 1.9, 0.8, [0, 0, 0], P.vending),
    box(0.55, 1.2, 0.02, [-0.1, 1.15, 0.41], P.vendingGlass),
    box(0.18, 0.5, 0.02, [0.3, 1.3, 0.41], P.metal),
    box(0.55, 0.15, 0.02, [-0.1, 0.35, 0.41], P.metal),
  ];
}

function waterCooler(): Part[] {
  return [block(0.4, 1.0, 0.4, [0, 0, 0], P.cooler), cyl(0.15, 0.15, 0.42, [0, 1.0, 0], P.coolerBottle, 10), box(0.2, 0.06, 0.03, [0, 0.8, 0.21], P.metal)];
}

function stool(): Part[] {
  return [cyl(0.2, 0.2, 0.05, [0, 0.62, 0], P.tableWood, 10), cyl(0.03, 0.03, 0.62, [0, 0, 0], P.metal, 6), cyl(0.18, 0.18, 0.02, [0, 0, 0], P.metal, 10)];
}

function cafeTable(): Part[] {
  return [
    cyl(0.55, 0.55, 0.04, [0, TOP, 0], P.deskTop, 16),
    cyl(0.05, 0.05, TOP, [0, 0, 0], P.metal, 8),
    cyl(0.3, 0.3, 0.02, [0, 0, 0], P.metal, 12),
    ...[0, 2, 4].flatMap((i) => place(stool(), Math.sin((i * Math.PI) / 3) * 0.85, Math.cos((i * Math.PI) / 3) * 0.85)),
  ];
}

/** A long low planter box of greenery: divides two zones without walling them off. */
function planter(length: number): Part[] {
  const parts: Part[] = [block(length, 0.45, 0.36, [0, 0, 0], P.shelfWood), block(length - 0.06, 0.02, 0.3, [0, 0.44, 0], P.soil)];
  const plants = Math.max(2, Math.round(length / 0.4));
  for (let i = 0; i < plants; i++) {
    const x = -length / 2 + ((i + 0.5) * length) / plants;
    parts.push(...place(rosette(7, 0.34, 0.14, (j) => -0.9 + (j % 3) * 0.3, 0, (j) => ((i + j) % 2 ? P.leafLight : P.leaf), i), x, 0, 0, 0.45));
  }
  return parts;
}

function ceoSofa(): Part[] {
  return sofa(1.8, P.armchair, P.cushion);
}

function decorParts(item: Decor, index: number): Part[] {
  const build: Record<Decor["kind"], () => Part[]> = {
    low_shelf: () => lowShelf(Math.max(item.size[0], item.size[1]), 11 + index),
    palm,
    plant: () => smallPlant(index % 2 ? P.potTerracotta : P.pot, 1.2),
    lounge,
    meeting_set: meetingSet,
    whiteboard,
    pantry_counter: pantryCounter,
    fridge,
    vending,
    water_cooler: waterCooler,
    cafe_table: cafeTable,
    ceo_shelf: () => tallShelf(Math.max(item.size[0], item.size[1]), 41, P.execWood),
    ceo_sofa: ceoSofa,
    planter: () => planter(Math.max(item.size[0], item.size[1])),
  };
  return place(build[item.kind](), item.at[0], item.at[1], item.rotY);
}

function approvalDesk(): Part[] {
  const { width, depth } = APPROVAL_DESK;
  // the visitor side faces the corridor (-z); the approver sits behind (+z)
  const parts: Part[] = [
    block(width, 1.05, 0.12, [0, 0, -depth / 2 + 0.06], P.counter),
    block(0.12, 1.05, depth, [-width / 2 + 0.06, 0, 0], P.counter),
    block(0.12, 1.05, depth, [width / 2 - 0.06, 0, 0], P.counter),
    block(width + 0.1, 0.05, 0.35, [0, 1.05, -depth / 2 + 0.1], P.deskTop),
    block(width - 0.24, 0.04, depth - 0.12, [0, TOP - 0.04, 0.05], P.deskTop),
    box(width + 0.01, 0.22, 0.02, [0, 0.7, -depth / 2 - 0.005], P.approvalAccent),
    box(0.35, 0.35, 0.02, [0, 0.35, -depth / 2 - 0.005], P.approvalAccent),
    ...place(workstation(), 0, 0.1),
    ...place(officeChair(P.approvalAccent), 0, depth / 2 + 0.45),
    ...place(lampBody(), APPROVAL_LAMP.dx - LAMP.x, APPROVAL_LAMP.dz - LAMP.z, 0, APPROVAL_LAMP.lift),
  ];
  return place(parts, APPROVAL_DESK.center[0], APPROVAL_DESK.center[1]);
}

// --- architecture ------------------------------------------------------------------------------

const WALL = 0.25;
const CAP = 0.05;
const INNER_WALL_H = 2.8;

/** A window on a wall plane (local +z = into the room): frame and mullions; the glass is separate. */
function windowFrame(width: number, height: number, sill: number): Part[] {
  const t = 0.07;
  const parts: Part[] = [
    box(width, t, 0.08, [0, sill, 0.04], P.frame),
    box(width, t, 0.08, [0, sill + height, 0.04], P.frame),
    box(t, height, 0.08, [-width / 2, sill + height / 2, 0.04], P.frame),
    box(t, height, 0.08, [width / 2, sill + height / 2, 0.04], P.frame),
    box(width + 0.1, 0.05, 0.16, [0, sill - 0.04, 0.08], P.frame),
  ];
  const panes = Math.max(2, Math.round(width / 0.7));
  for (let i = 1; i < panes; i++) parts.push(box(0.05, height, 0.07, [-width / 2 + (i * width) / panes, sill + height / 2, 0.04], P.frame));
  return parts;
}

function picture(width: number, height: number, color: string): Part[] {
  return [box(width, height, 0.03, [0, 0, 0.015], P.metal), box(width - 0.08, height - 0.08, 0.035, [0, 0, 0.02], color), box((width - 0.08) * 0.4, (height - 0.08) * 0.5, 0.04, [0.05, -0.02, 0.022], P.frame)];
}

/** Windows (frames here; glass in windowGlassParts) as [x or z along the wall, width]. */
const BACK_WINDOWS: [number, number][] = [
  [-3.8, 1.4],
  [2.0, 1.4],
  [10.7, 1.6],
];
const LEFT_WINDOWS: [number, number][] = [
  [-5.6, 2.4],
  [4.6, 1.6],
  [6.6, 1.6],
];
const WINDOW = { sill: 1.0, height: 1.5 };

function architecture(): Part[] {
  const width = ROOM.maxX - ROOM.minX;
  const depth = ROOM.maxZ - ROOM.minZ;
  const parts: Part[] = [
    // the slab the diorama stands on, with its white edge
    block(width + 0.8, 0.35, depth + 0.8, [0, -0.35, 0], P.slab),
    // back and left walls, full height, with white caps
    block(width + WALL, ROOM.wallHeight, WALL, [-WALL / 2, 0, ROOM.minZ - WALL / 2], P.wall),
    box(width + WALL + 0.04, CAP, WALL + 0.04, [-WALL / 2, ROOM.wallHeight + CAP / 2, ROOM.minZ - WALL / 2], P.wallCap),
    block(WALL, ROOM.wallHeight, depth, [ROOM.minX - WALL / 2, 0, 0], P.wall),
    box(WALL + 0.04, CAP, depth + 0.04, [ROOM.minX - WALL / 2, ROOM.wallHeight + CAP / 2, 0], P.wallCap),
    // the cut-away front and right edges: a low white rim, open at the entrance
    block(width + WALL * 2, 0.14, WALL, [0, 0, ROOM.maxZ + WALL / 2], P.wallCap),
    block(WALL, 0.14, ENTRANCE.minZ - ROOM.minZ + WALL, [ROOM.maxX + WALL / 2, 0, (ROOM.minZ - WALL + ENTRANCE.minZ) / 2], P.wallCap),
    block(WALL, 0.14, ROOM.maxZ - ENTRANCE.maxZ, [ROOM.maxX + WALL / 2, 0, (ENTRANCE.maxZ + ROOM.maxZ) / 2], P.wallCap),
    // skirting
    block(width, 0.12, 0.02, [0, 0, ROOM.minZ + 0.01], P.frame),
    block(0.02, 0.12, depth, [ROOM.minX + 0.01, 0, 0], P.frame),
  ];

  // the entrance: a slim portal frame on the right edge (its sign is a label) and a threshold
  {
    const x = ENTRANCE.x + WALL / 2;
    const span = ENTRANCE.maxZ - ENTRANCE.minZ;
    const mid = (ENTRANCE.minZ + ENTRANCE.maxZ) / 2;
    for (const z of [ENTRANCE.minZ, ENTRANCE.maxZ]) parts.push(block(0.14, 2.3, 0.14, [x, 0, z], P.door));
    parts.push(box(0.14, 0.12, span + 0.14, [x, 2.36, mid], P.door));
    parts.push(block(WALL, 0.02, span, [x, 0, mid], P.metal));
  }

  // interior walls between the back rooms, with caps
  const innerDepth = BACK_ROOMS_Z - WALL_HALF - ROOM.minZ;
  for (const x of [CEO_OFFICE.maxX, MEETING_ROOM.maxX]) {
    parts.push(block(WALL_HALF * 2, INNER_WALL_H, innerDepth, [x, 0, ROOM.minZ + innerDepth / 2], P.wall));
    parts.push(box(WALL_HALF * 2 + 0.04, CAP, innerDepth, [x, INNER_WALL_H + CAP / 2, ROOM.minZ + innerDepth / 2], P.wallCap));
  }

  // glass fronts: mullions and rails (panes are separate), door frames, open leaves
  const fronts: [number, number][] = [
    [CEO_OFFICE.minX, CEO_OFFICE.maxX],
    [MEETING_ROOM.minX, MEETING_ROOM.maxX],
  ];
  for (const [from, to] of fronts) {
    const len = to - from;
    parts.push(box(len, 0.08, 0.12, [(from + to) / 2, INNER_WALL_H - 0.04, BACK_ROOMS_Z], P.mullion));
    parts.push(box(len, 0.06, 0.12, [(from + to) / 2, 0.03, BACK_ROOMS_Z], P.mullion));
    const posts = Math.ceil(len / 1.4);
    for (let i = 0; i <= posts; i++) parts.push(block(0.06, INNER_WALL_H, 0.12, [from + (i * len) / posts, 0, BACK_ROOMS_Z], P.mullion));
  }
  for (const door of DOORS) {
    for (const side of [-1, 1]) parts.push(block(0.08, 2.2, 0.16, [door.x + (side * door.width) / 2, 0, BACK_ROOMS_Z], P.door));
    parts.push(box(door.width + 0.16, 0.08, 0.16, [door.x, 2.24, BACK_ROOMS_Z], P.door));
  }
  for (const leafRect of doorLeaves()) {
    const cz = (leafRect.minZ + leafRect.maxZ) / 2;
    parts.push(block(0.05, 2.15, leafRect.maxZ - leafRect.minZ, [(leafRect.minX + leafRect.maxX) / 2, 0.02, cz], P.door));
    parts.push(box(0.08, 0.04, 0.14, [(leafRect.minX + leafRect.maxX) / 2, 1.05, leafRect.minZ + 0.12], P.metal));
  }

  // windows and pictures
  for (const [x, w] of BACK_WINDOWS) parts.push(...place(windowFrame(w, WINDOW.height, WINDOW.sill), x, ROOM.minZ));
  for (const [z, w] of LEFT_WINDOWS) parts.push(...place(windowFrame(w, WINDOW.height, WINDOW.sill), ROOM.minX, z, Math.PI / 2));
  parts.push(...place(picture(0.9, 0.7, P.picture[0]), ROOM.minX + 0.01, -0.2, Math.PI / 2, 1.75));
  parts.push(...place(picture(0.7, 0.9, P.picture[1]), CEO_OFFICE.maxX + WALL_HALF, -6.4, Math.PI / 2, 1.6));
  parts.push(...place(picture(0.6, 0.6, P.picture[2]), MEETING_ROOM.maxX - WALL_HALF, -6.2, -Math.PI / 2, 1.7));
  parts.push(...place(picture(0.6, 0.8, P.picture[3]), PANTRY.minX + WALL_HALF, -6.9, Math.PI / 2, 1.8));
  // the meeting room's projection screen
  parts.push(box(3.2, 1.7, 0.03, [-0.8, 1.75, ROOM.minZ + 0.03], P.whiteboard), box(3.4, 0.12, 0.14, [-0.8, 2.65, ROOM.minZ + 0.08], P.metal));
  return parts;
}

/** Neon outlines around the zones' floors, in a theme that has them. */
function zoneTrims(): Part[] {
  const parts: Part[] = [];
  const areas = { ...ZONES, pantry: PANTRY };
  const t = 0.05;
  const y = 0.02;
  for (const [zone, colour] of Object.entries(P.zoneTrim ?? {})) {
    if (!colour) continue;
    const a = areas[zone as keyof typeof areas];
    const w = a.maxX - a.minX;
    const d = a.maxZ - a.minZ;
    const cx = (a.minX + a.maxX) / 2;
    const cz = (a.minZ + a.maxZ) / 2;
    parts.push(
      box(w, 0.01, t, [cx, y, a.minZ + t / 2], colour),
      box(w, 0.01, t, [cx, y, a.maxZ - t / 2], colour),
      box(t, 0.01, d - 2 * t, [a.minX + t / 2, y, cz], colour),
      box(t, 0.01, d - 2 * t, [a.maxX - t / 2, y, cz], colour),
    );
  }
  return parts;
}

// --- the whole office --------------------------------------------------------------------------

const DEFAULT_PALETTE = THEMES[DEFAULT_THEME].palette;

/** Everything static and opaque, merged into one mesh by the scene. */
export function officeParts(palette: Palette = DEFAULT_PALETTE): Part[] {
  return paintedWith(palette, () => [
    ...architecture(),
    ...BENCHES.flatMap((b) => benchTable(b.minX, b.maxX, b.z)),
    ...allSeats().flatMap(seatParts),
    ...DECOR.flatMap(decorParts),
    ...approvalDesk(),
    ...zoneTrims(),
  ]);
}

/** Window panes (a separate, softly glowing mesh). */
export function windowGlassParts(palette: Palette = DEFAULT_PALETTE): Part[] {
  const pane = (w: number) => box(w - 0.08, WINDOW.height - 0.08, 0.02, [0, WINDOW.sill + WINDOW.height / 2, 0.03], palette.windowGlass);
  return [
    ...BACK_WINDOWS.flatMap(([x, w]) => place([pane(w)], x, ROOM.minZ)),
    ...LEFT_WINDOWS.flatMap(([z, w]) => place([pane(w)], ROOM.minX, z, Math.PI / 2)),
  ];
}

/** The glass of the back rooms' fronts (a separate, transparent mesh), doors left open. */
export function partitionGlassParts(palette: Palette = DEFAULT_PALETTE): Part[] {
  const P = palette;
  const h = INNER_WALL_H - 0.12;
  const segments: [number, number][] = [];
  for (const [room, door] of [
    [CEO_OFFICE, DOORS[0]],
    [MEETING_ROOM, DOORS[1]],
  ] as const) {
    segments.push([room.minX, door.x - door.width / 2], [door.x + door.width / 2, room.maxX]);
  }
  const panes = segments.map(([from, to]) => box(to - from, h, 0.02, [(from + to) / 2, 0.06 + h / 2, BACK_ROOMS_Z], P.glass));
  // the transoms above the doors
  for (const door of DOORS) panes.push(box(door.width, INNER_WALL_H - 2.36, 0.02, [door.x, 2.28 + (INNER_WALL_H - 2.36) / 2, BACK_ROOMS_Z], P.glass));
  return panes;
}

/** Floor regions, drawn by material (the procedural textures live in scene/textures.ts). */
export type { FloorKind };

export interface FloorRegion {
  kind: FloorKind;
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  /** Stacking order: higher draws over lower (a few millimetres apart). */
  layer: number;
}

export function floorRegions(): FloorRegion[] {
  const full = { minX: ROOM.minX, maxX: ROOM.maxX };
  const room = (kind: FloorKind, r: { minX: number; maxX: number; minZ: number; maxZ: number }, layer: number): FloorRegion => ({
    kind,
    minX: r.minX,
    maxX: r.maxX,
    minZ: r.minZ,
    maxZ: r.maxZ,
    layer,
  });
  return [
    { kind: "base", ...full, minZ: ROOM.minZ, maxZ: ROOM.maxZ, layer: 0 },
    room("ceo", CEO_OFFICE, 1),
    room("meeting", MEETING_ROOM, 1),
    room("pantry", PANTRY, 1),
    room("research", ZONES.research, 1),
    room("editorial", ZONES.editorial, 1),
    room("growth", ZONES.growth, 1),
    room("spare", ZONES.spare, 1),
    room("lobby", ZONES.lobby, 1),
    { kind: "corridor", ...full, minZ: -3.2, maxZ: -1.3, layer: 2 },
    { kind: "corridor", ...full, minZ: 1.6, maxZ: 3.2, layer: 2 },
    { kind: "corridor", minX: BENCHES[0].maxX + 0.25, maxX: BENCHES[1].minX - 0.25, minZ: -1.3, maxZ: 1.6, layer: 2 },
    { kind: "rugLounge", minX: 8.8, maxX: 11.8, minZ: 4.1, maxZ: 7.5, layer: 3 },
    { kind: "rugCeo", minX: -10.2, maxX: -6.8, minZ: -7.2, maxZ: -4.2, layer: 3 },
    { kind: "entranceMat", minX: ROOM.maxX - 1.0, maxX: ROOM.maxX, minZ: ENTRANCE.minZ + 0.2, maxZ: ENTRANCE.maxZ - 0.2, layer: 3 },
  ];
}
