// The office floor plan (T-402, 3d-office/04 §2, D-010): the only source of coordinates. Desks,
// avatars, decoration, courier walks (T-408) and camera focus (T-409) all read it. The desks are
// a visual setting; who sits at one is company data.
//
// **A department decides the room, not a role** (T-600 batch 3, ARCHITECTURE_V2 §14.7). An agent
// is seated in the zone its department names (`departments.office_zone_key`, carried on the
// stream as `office_zone_key`); within that zone it prefers the desk built for its role, so a
// newsroom looks exactly as it was drawn while the rule underneath is organisational. A company
// with no org chart yet falls back to the role's own zone, which is where v1 put it.
//
// Units are metres; x runs left to right, z from the back wall (-z) to the front (+z). The camera
// looks from the front right, so the back and left walls are full height and the front and right
// edges are a low rim (a cut-away diorama).
//
//   z=-8 ┌── CEO office ──┬──── meeting room ────┬──── pantry ─────┐
//        │ (glass front)  │ (glass front)        │ (open)          │
//   z=-3.2├──door─────────┴──────door────────────┘                 │
//        │ ═══════════════ back corridor (lane z=-2.25) ══════════ │
//   z=0  │ [res][res][ana][ana]  spine x=0  [wri][wri][edi][edi]   │  bench desks
//        │ ═══════════════ front corridor (lane z=2.4) ═══════════ │
//        │                                                 entrance ◁ (T-413)
//   z=4.6│ [mkt][mkt][mkt] ▒ [spare][spare][spare]  [reception] lounge│  single desks; lobby
//   z=8  └──────────────────────────────────────────────────────────┘
//      x=-12                                                     x=12

export type Vec2 = readonly [x: number, z: number];
export type Vec3 = readonly [x: number, y: number, z: number];
export type ZoneId = "ceo" | "research" | "editorial" | "growth" | "spare";
export type Lane = "front" | "back";

export const ROOM = { minX: -12, maxX: 12, minZ: -8, maxZ: 8, wallHeight: 3 } as const;

export const DESK = { width: 1.5, depth: 0.8, height: 0.75 } as const;
export const CHAIR = { size: 0.6 } as const;
/** How far behind the desk the chair stands; the seated agent faces -z (its monitors). */
const CHAIR_OFFSET = 0.95;
/** Where a visitor stands: beside the seated agent, to its right (left against the right wall). */
const APPROACH_OFFSET: Vec2 = [1.1, 0];

export const LANES: Record<Lane, number> = { back: -2.25, front: 2.4 };
/** The aisle between the two benches that joins the lanes. */
export const SPINE_X = 0;

/** Rooms along the back wall; their fronts are at z = BACK_ROOMS_Z. */
export const BACK_ROOMS_Z = -3.2;
export const CEO_OFFICE = { minX: -12, maxX: -5, minZ: -8, maxZ: BACK_ROOMS_Z, doorX: -7.4, doorWidth: 1.2 } as const;
export const MEETING_ROOM = { minX: -5, maxX: 4, minZ: -8, maxZ: BACK_ROOMS_Z, doorX: 2.6, doorWidth: 1.2 } as const;
export const PANTRY = { minX: 4, maxX: 12, minZ: -8, maxZ: BACK_ROOMS_Z } as const;
/** The walkways between the zones. */
export const CORRIDORS = {
  back: { minZ: BACK_ROOMS_Z, maxZ: -1.3 },
  front: { minZ: 1.6, maxZ: 3.2 },
} as const;

/** The way in (T-413): an opening in the right edge where the front corridor meets it. */
export const ENTRANCE = { x: ROOM.maxX, minZ: CORRIDORS.front.minZ, maxZ: CORRIDORS.front.maxZ } as const;

export interface Area {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
}

/**
 * The open-plan zones, each with its own floor (T-413): a carpet per department, and the lobby —
 * reception (the approval desk) and the waiting lounge — by the entrance.
 */
export const ZONES: Record<"research" | "editorial" | "growth" | "spare" | "lobby", Area> = {
  research: { minX: -10.6, maxX: -1.4, minZ: CORRIDORS.back.maxZ, maxZ: CORRIDORS.front.minZ },
  editorial: { minX: 1.4, maxX: 10.6, minZ: CORRIDORS.back.maxZ, maxZ: CORRIDORS.front.minZ },
  growth: { minX: -10.4, maxX: -3.2, minZ: CORRIDORS.front.maxZ, maxZ: 6.6 },
  spare: { minX: -2.6, maxX: 3.9, minZ: CORRIDORS.front.maxZ, maxZ: 6.6 },
  lobby: { minX: 4.2, maxX: ROOM.maxX, minZ: CORRIDORS.front.maxZ, maxZ: ROOM.maxZ },
};

/** The approval desk doubles as the reception counter in the lobby. */
export const APPROVAL_DESK = { center: [6.4, 4.9] as Vec2, width: 2.6, depth: 0.9, approach: [6.4, 3.8] as Vec2 } as const;

interface RoleSlots {
  zone: ZoneId;
  lane: Lane;
  /** Desks joined into one bench table (drawn and walked around as one). */
  bench: boolean;
  /** Desk centres; the first is the seat a single agent of this role gets. */
  desks: Vec2[];
}

const WORK_Z = 0;
const FRONT_Z = 4.6;

/** Desks per role, in the order they are filled. Capacity = number of desks. */
export const SLOTS: Record<string, RoleSlots> = {
  researcher: { zone: "research", lane: "front", bench: true, desks: [[-6.8, WORK_Z], [-9.0, WORK_Z]] },
  analyst: { zone: "research", lane: "front", bench: true, desks: [[-2.4, WORK_Z], [-4.6, WORK_Z]] },
  writer: { zone: "editorial", lane: "front", bench: true, desks: [[2.4, WORK_Z], [4.6, WORK_Z]] },
  editor: { zone: "editorial", lane: "front", bench: true, desks: [[6.8, WORK_Z], [9.0, WORK_Z]] },
  marketing: { zone: "growth", lane: "front", bench: false, desks: [[-6.8, FRONT_Z], [-9.0, FRONT_Z], [-4.6, FRONT_Z]] },
  ceo: { zone: "ceo", lane: "back", bench: false, desks: [[-8.5, -6]] },
};

/** Desks for roles the floor plan does not know (a new domain's roles), first come first served. */
export const SPARE: RoleSlots = { zone: "spare", lane: "front", bench: false, desks: [[-1.6, FRONT_Z], [0.6, FRONT_Z], [2.8, FRONT_Z]] };

export const ROLES = Object.keys(SLOTS);

/** The two bench tables of the work row (left: research, right: editorial). */
export const BENCHES = [
  { name: "research bench", minX: -9.75, maxX: -1.65, z: WORK_Z },
  { name: "editorial bench", minX: 1.65, maxX: 9.75, z: WORK_Z },
] as const;

export interface Seat {
  key: string;
  role: string;
  zone: ZoneId;
  lane: Lane;
  bench: boolean;
  desk: Vec2;
  chair: Vec2;
  /** Rotation about y for the seated avatar: it faces its monitors (-z). */
  facing: number;
  /** Centre between the seat's two monitors. */
  screen: Vec3;
  /** Where a visitor stops next to this seat. */
  approach: Vec2;
}

function seatAt(role: string, slots: RoleSlots, index: number): Seat {
  const [x, z] = slots.desks[index];
  const chair: Vec2 = [x, z + CHAIR_OFFSET];
  const side = x + APPROACH_OFFSET[0] > ROOM.maxX - 1 ? -1 : 1;
  return {
    key: `${slots.zone}:${role}:${index}`,
    role,
    zone: slots.zone,
    lane: slots.lane,
    bench: slots.bench,
    desk: [x, z],
    chair,
    facing: Math.PI,
    screen: [x, DESK.height + 0.3, z - 0.18],
    approach: [chair[0] + side * APPROACH_OFFSET[0], chair[1] + APPROACH_OFFSET[1]],
  };
}

/** Every seat of a zone, in the order they are filled: the desks built for its roles first. */
export function seatsInZone(zone: ZoneId): Seat[] {
  const own = Object.entries(SLOTS)
    .filter(([, slots]) => slots.zone === zone)
    .sort(([a], [b]) => a.localeCompare(b))
    .flatMap(([role, slots]) => slots.desks.map((_, i) => seatAt(role, slots, i)));
  return zone === SPARE.zone ? [...own, ...SPARE.desks.map((_, i) => seatAt("spare", SPARE, i))] : own;
}

export const ZONE_OF_ROLE: Record<string, ZoneId> = Object.fromEntries(
  Object.entries(SLOTS).map(([role, slots]) => [role, slots.zone]),
);

/** Up to `n` seats for a role (fewer when its area is full). */
export function seatsForRole(role: string, n: number): Seat[] {
  const slots = SLOTS[role];
  if (!slots) return [];
  return Array.from({ length: Math.min(n, slots.desks.length) }, (_, i) => seatAt(role, slots, i));
}

export interface Assignment {
  seats: Map<string, Seat>;
  /** Agents with no desk left (shown on the 2D board and in lists, not in the room). */
  unseated: string[];
}

/** Anyone the office can seat: the runtime's role, and the department's place on the floor. */
export interface Seatable {
  id: string;
  role: string;
  office_zone_key?: string | null;
}

const ZONE_IDS = new Set<string>(Object.values(SLOTS).map((s) => s.zone).concat(SPARE.zone));

/** Which part of the floor an agent belongs in: its department's, or its role's, or spare. */
export function zoneOf(agent: Seatable): ZoneId {
  const named = agent.office_zone_key;
  if (named && ZONE_IDS.has(named)) return named as ZoneId;
  return ZONE_OF_ROLE[agent.role] ?? SPARE.zone;
}

/**
 * Seat every agent in its department's zone. Stable: the same roster gives the same desks.
 *
 * Within a zone the desk built for the agent's role is taken first, so a company whose
 * departments match the drawn floor looks exactly as it was designed. Anyone left over takes
 * another free desk in the same zone, then a flex desk, and only then goes unseated — which is
 * a real state, not a bug: it is shown in the lists and on the 2D board.
 */
export function assignSeats(agents: readonly Seatable[]): Assignment {
  const seats = new Map<string, Seat>();
  const unseated: string[] = [];
  const taken = new Set<string>();
  const free = new Map<ZoneId, Seat[]>();
  const zoneSeats = (zone: ZoneId): Seat[] => {
    if (!free.has(zone)) free.set(zone, seatsInZone(zone));
    return free.get(zone)!;
  };
  const claim = (zone: ZoneId, role: string): Seat | undefined => {
    const available = zoneSeats(zone).filter((seat) => !taken.has(seat.key));
    const seat = available.find((s) => s.role === role) ?? available[0];
    if (seat) taken.add(seat.key);
    return seat;
  };

  const ordered = [...agents].sort(
    (a, b) => a.role.localeCompare(b.role) || a.id.localeCompare(b.id),
  );
  for (const agent of ordered) {
    const seat = claim(zoneOf(agent), agent.role) ?? claim(SPARE.zone, agent.role);
    if (seat) seats.set(agent.id, { ...seat, role: agent.role });
    else unseated.push(agent.id);
  }
  return { seats, unseated };
}

/** Every desk the room has, for drawing furniture (occupied or not). */
export function allSeats(): Seat[] {
  return [
    ...Object.entries(SLOTS).flatMap(([role, slots]) => slots.desks.map((_, i) => seatAt(role, slots, i))),
    ...SPARE.desks.map((_, i) => seatAt("spare", SPARE, i)),
  ];
}

// --- decoration -------------------------------------------------------------------------------

export type DecorKind =
  | "low_shelf"
  | "palm"
  | "plant"
  | "lounge"
  | "meeting_set"
  | "whiteboard"
  | "pantry_counter"
  | "fridge"
  | "vending"
  | "water_cooler"
  | "cafe_table"
  | "ceo_shelf"
  | "ceo_sofa"
  | "planter";

export interface Decor {
  kind: DecorKind;
  at: Vec2;
  /** Rotation about y (radians). */
  rotY: number;
  /** Footprint on the floor (x/z after rotation), for walking around it. */
  size: Vec2;
}

const d = (kind: DecorKind, at: Vec2, size: Vec2, rotY = 0): Decor => ({ kind, at, size, rotY });
const QUARTER = Math.PI / 2;

/** Fixed pieces that carry no state. Anything a walker could bump into is listed here. */
export const DECOR: Decor[] = [
  // work area: low shelves with plants along the left wall, palms and plants at the ends
  d("low_shelf", [-11.6, -0.2], [0.45, 2.2], QUARTER),
  d("palm", [-11.2, 2.4], [0.8, 0.8]),
  d("plant", [11.4, -0.4], [0.6, 0.6]),
  // front area: a planter between marketing and the flex desks; the lobby by the entrance
  d("low_shelf", [-11.6, 6.9], [0.45, 1.8], QUARTER),
  d("plant", [-11.4, 3.7], [0.6, 0.6]),
  d("planter", [-2.85, 4.9], [0.36, 1.6], QUARTER),
  d("lounge", [10.3, 5.8], [3.2, 3.6]),
  d("plant", [11.5, 3.6], [0.6, 0.6]),
  // CEO office
  d("ceo_shelf", [-10.6, -7.6], [2.2, 0.5]),
  d("ceo_sofa", [-5.7, -5.8], [0.9, 2.0], -QUARTER),
  d("plant", [-11.4, -3.8], [0.6, 0.6]),
  // meeting room
  d("meeting_set", [-0.8, -5.6], [5.4, 2.6]),
  d("whiteboard", [-4.3, -4.4], [0.6, 1.6], QUARTER),
  d("plant", [3.5, -7.5], [0.6, 0.6]),
  // pantry
  d("pantry_counter", [6.4, -7.65], [4.2, 0.7]),
  d("fridge", [9.0, -7.55], [0.9, 0.8]),
  d("vending", [4.55, -4.4], [0.9, 1.0], QUARTER),
  d("water_cooler", [4.45, -5.6], [0.45, 0.45], QUARTER),
  d("cafe_table", [8.6, -5.4], [2.4, 2.4]),
  d("plant", [11.5, -3.7], [0.6, 0.6]),
];

/** Words on the floor and signs on the glass fronts that name the zones and rooms (T-413). */
export interface Label {
  text: string;
  /** A second, smaller line (English). */
  sub: string;
  /** Centre; y is the height of a sign (0: painted on the floor). */
  at: Vec3;
  /** Width in metres (height is a quarter of it). */
  width: number;
  /** Painted on the floor, or a sign facing +z (the glass fronts) or +x (the entrance). */
  kind: "floor" | "sign";
  facing?: "z" | "x";
}

export const LABELS: Label[] = [
  { text: "研究部", sub: "RESEARCH", at: [-6.0, 0, 2.0], width: 3.0, kind: "floor" },
  { text: "編輯部", sub: "EDITORIAL", at: [6.0, 0, 2.0], width: 3.0, kind: "floor" },
  // behind the front desks' chairs: in front of the desks the desks would hide them
  { text: "行銷部", sub: "MARKETING", at: [-6.8, 0, 6.3], width: 2.4, kind: "floor" },
  { text: "彈性座位", sub: "FLEX DESKS", at: [0.6, 0, 6.3], width: 2.4, kind: "floor" },
  { text: "接待區", sub: "RECEPTION", at: [6.4, 0, 7.05], width: 2.6, kind: "floor" },
  { text: "茶水間", sub: "PANTRY", at: [8.2, 0, -3.72], width: 2.4, kind: "floor" },
  { text: "總經理室", sub: "CEO OFFICE", at: [-5.95, 2.5, BACK_ROOMS_Z + 0.08], width: 1.6, kind: "sign" },
  { text: "會議室", sub: "MEETING ROOM", at: [0.6, 2.5, BACK_ROOMS_Z + 0.08], width: 1.6, kind: "sign" },
  { text: "AUTORA", sub: "入口 ENTRANCE", at: [ROOM.maxX + 0.21, 2.6, (CORRIDORS.front.minZ + CORRIDORS.front.maxZ) / 2], width: 1.4, kind: "sign", facing: "x" },
];

/** The doors in the glass fronts (open, the leaf swung into the room on the hinge side). */
export const DOORS = [
  { name: "ceo door", x: CEO_OFFICE.doorX, width: CEO_OFFICE.doorWidth },
  { name: "meeting door", x: MEETING_ROOM.doorX, width: MEETING_ROOM.doorWidth },
] as const;

// --- walking ----------------------------------------------------------------------------------

export type WalkTarget = Seat | "approval" | { door: string };

/**
 * Where a department is entered from the corridor: the middle of its edge, on the lane that
 * runs past it (T-600 batch 4).
 *
 * The key comes from the server (a department's ``office_zone_key``), so it may name a room
 * this floor does not draw; that falls back to the middle of the walkway, which is where
 * somebody with nowhere to go would in fact stand.
 *
 * A courier carrying work to another department stops here rather than at somebody's desk.
 * That is not only an animation choice: the work goes to whichever colleague claims it next,
 * so walking to one particular desk would draw a hand-over that is not what happened.
 */
export function doorOf(zone: string): { point: Vec2; lane: Lane } {
  if (zone === "ceo") {
    // the one room with an actual door: the opening in its glass front
    return { point: [CEO_OFFICE.doorX, BACK_ROOMS_Z + 0.5], lane: "back" };
  }
  const area = ZONES[zone as keyof typeof ZONES];
  if (!area) return { point: [SPINE_X, LANES.front], lane: "front" };
  const middle = (area.minX + area.maxX) / 2;
  // every open-plan zone opens onto the front corridor: the work row from its front edge, the
  // rest from their back edge, both stopping just inside the walkway
  const inCorridor = area.maxZ <= CORRIDORS.front.minZ ? area.maxZ + 0.6 : area.minZ - 0.6;
  return { point: [middle, inCorridor], lane: "front" };
}

function approachOf(target: WalkTarget): { point: Vec2; lane: Lane } {
  if (target === "approval") return { point: APPROVAL_DESK.approach, lane: "front" };
  if ("door" in target) return doorOf(target.door);
  return { point: target.approach, lane: target.lane };
}

/**
 * The courier's route (T-408): up from the chair, out to the lane, along the lanes (changing
 * lanes on the spine), and in to the target's approach point. Walk it backwards to return.
 */
export function walkPath(from: Seat, to: WalkTarget): Vec2[] {
  const start = approachOf(from);
  const end = approachOf(to);
  const points: Vec2[] = [from.chair, start.point, [start.point[0], LANES[start.lane]]];
  if (start.lane !== end.lane) points.push([SPINE_X, LANES[start.lane]], [SPINE_X, LANES[end.lane]]);
  points.push([end.point[0], LANES[end.lane]], end.point);
  return points.filter((p, i) => i === 0 || p[0] !== points[i - 1][0] || p[1] !== points[i - 1][1]);
}

/** Axis-aligned footprints of everything a walker must go around (x/z rectangles). */
export interface Rect {
  name: string;
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
}

export function rectAround([x, z]: Vec2, width: number, depth: number, name: string): Rect {
  return { name, minX: x - width / 2, maxX: x + width / 2, minZ: z - depth / 2, maxZ: z + depth / 2 };
}

/** Half the thickness of interior walls and glass fronts. */
export const WALL_HALF = 0.1;

/** Interior walls and glass fronts, with their door openings left out. */
export function partitions(): Rect[] {
  const z = BACK_ROOMS_Z;
  const t = WALL_HALF;
  const glass = (from: number, to: number, name: string): Rect => ({ name, minX: from, maxX: to, minZ: z - t, maxZ: z + t });
  const opening = (door: { doorX: number; doorWidth: number }) => [door.doorX - door.doorWidth / 2, door.doorX + door.doorWidth / 2];
  const [ceoL, ceoR] = opening(CEO_OFFICE);
  const [meetL, meetR] = opening(MEETING_ROOM);
  const wall = (x: number, name: string): Rect => ({ name, minX: x - t, maxX: x + t, minZ: ROOM.minZ, maxZ: z - t });
  return [
    glass(CEO_OFFICE.minX, ceoL, "ceo glass (left of door)"),
    glass(ceoR, CEO_OFFICE.maxX - t, "ceo glass (right of door)"),
    glass(CEO_OFFICE.maxX + t, meetL, "meeting glass (left of door)"),
    glass(meetR, MEETING_ROOM.maxX - t, "meeting glass (right of door)"),
    wall(CEO_OFFICE.maxX, "wall ceo | meeting"),
    wall(MEETING_ROOM.maxX, "wall meeting | pantry"),
  ];
}

/** Open door leaves: inside the room, along the hinge side of the opening. */
export function doorLeaves(): Rect[] {
  return DOORS.map((door) => {
    const hinge = door.x - door.width / 2 - 0.05;
    return {
      name: `${door.name} leaf`,
      minX: hinge - 0.03,
      maxX: hinge + 0.03,
      minZ: BACK_ROOMS_Z - WALL_HALF - door.width,
      maxZ: BACK_ROOMS_Z - WALL_HALF - 0.01,
    };
  });
}

export function obstacles(): Rect[] {
  const furniture = allSeats().flatMap((seat) => [
    ...(seat.bench ? [] : [rectAround(seat.desk, DESK.width, DESK.depth, `desk ${seat.key}`)]),
    rectAround(seat.chair, CHAIR.size, CHAIR.size, `chair ${seat.key}`),
  ]);
  const benches = BENCHES.map((b) => ({ name: b.name, minX: b.minX, maxX: b.maxX, minZ: b.z - DESK.depth / 2, maxZ: b.z + DESK.depth / 2 }));
  const decor = DECOR.map((item, i) => rectAround(item.at, item.size[0], item.size[1], `${item.kind} #${i}`));
  return [
    ...furniture,
    ...benches,
    ...decor,
    rectAround(APPROVAL_DESK.center, APPROVAL_DESK.width, APPROVAL_DESK.depth, "approval desk"),
    ...partitions(),
    ...doorLeaves(),
  ];
}
