// The office floor plan (T-402, 3d-office/04 §2): the only source of coordinates. Desks, avatars,
// courier walks (T-408) and camera focus (T-409) all read it. Keyed by role, not agent: agents
// are company data, the floor plan is a visual setting.
//
// Units are metres; x runs left to right, z from the back wall (-z) to the front (+z); the camera
// looks from the front-right corner, so only the back and left walls are built.
//
//   z=-7 ┌──────────────────────────── back wall ─────────────────────────────┐
//        │  CEO office (glass, door)          meeting table (decoration)      │
//   z=-2.5├────────door──┐                                                    │
//        │ ── back lane ─┴───────────────── z=-1.7 ────────────────────────── │
//   z=0  │ [res][res]  [ana][ana]   spine x=0   [wri][wri]  [edi][edi]       │  work row
//        │ ── front lane ───────────────── z=2.4 ───────────────────────────── │
//   z=4.5│ [mkt][mkt][mkt]  [spare][spare][spare]      [approval desk]        │  front row
//   z=7  └────────────────────────────────────────────────────────────────────┘
//      x=-10                                                               x=10

export type Vec2 = readonly [x: number, z: number];
export type Vec3 = readonly [x: number, y: number, z: number];
export type ZoneId = "ceo" | "research" | "editorial" | "growth" | "spare";
export type Lane = "front" | "back";

export const ROOM = { minX: -10, maxX: 10, minZ: -7, maxZ: 7, wallHeight: 3 } as const;

export const DESK = { width: 1.5, depth: 0.8, height: 0.75 } as const;
export const CHAIR = { size: 0.6 } as const;
/** How far behind the desk the chair stands; the seated agent faces -z (the monitor). */
const CHAIR_OFFSET = 0.95;
/** Where a visitor stands: beside the seated agent, to its right (left against the right wall). */
const APPROACH_OFFSET: Vec2 = [1.1, 0];

export const LANES: Record<Lane, number> = { back: -1.7, front: 2.4 };
/** The one aisle between the lanes: nothing stands on it between the work-row desks. */
export const SPINE_X = 0;

export const CEO_OFFICE = { minX: -10, maxX: -3, minZ: -7, maxZ: -2.5, doorX: -5.6, doorWidth: 1.2 } as const;
export const MEETING_TABLE = { center: [5.5, -4.8] as Vec2, width: 4, depth: 1.6 } as const;
export const APPROVAL_DESK = { center: [5.5, 4.5] as Vec2, width: 2.2, depth: 0.9, approach: [5.5, 3.5] as Vec2 } as const;

interface RoleSlots {
  zone: ZoneId;
  lane: Lane;
  /** Desk centres; the first is the seat a single agent of this role gets. */
  desks: Vec2[];
}

const WORK_Z = 0;
const FRONT_Z = 4.5;

/** Desks per role, in the order they are filled. Capacity = number of desks. */
export const SLOTS: Record<string, RoleSlots> = {
  researcher: { zone: "research", lane: "front", desks: [[-6.6, WORK_Z], [-8.8, WORK_Z]] },
  analyst: { zone: "research", lane: "front", desks: [[-3.3, WORK_Z], [-1.1, WORK_Z]] },
  writer: { zone: "editorial", lane: "front", desks: [[3.3, WORK_Z], [1.1, WORK_Z]] },
  editor: { zone: "editorial", lane: "front", desks: [[6.6, WORK_Z], [8.8, WORK_Z]] },
  marketing: { zone: "growth", lane: "front", desks: [[-6.6, FRONT_Z], [-8.8, FRONT_Z], [-4.4, FRONT_Z]] },
  ceo: { zone: "ceo", lane: "back", desks: [[-6.5, -5]] },
};

/** Desks for roles the floor plan does not know (a new domain's roles), shared first come first served. */
export const SPARE: RoleSlots = { zone: "spare", lane: "front", desks: [[-2.0, FRONT_Z], [0.2, FRONT_Z], [2.4, FRONT_Z]] };

export const ROLES = Object.keys(SLOTS);

export interface Seat {
  key: string;
  role: string;
  zone: ZoneId;
  lane: Lane;
  desk: Vec2;
  chair: Vec2;
  /** Rotation about y for the seated avatar: it faces the monitor (-z). */
  facing: number;
  /** Centre of the monitor's screen. */
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
    desk: [x, z],
    chair,
    facing: Math.PI,
    screen: [x, DESK.height + 0.35, z - 0.15],
    approach: [chair[0] + side * APPROACH_OFFSET[0], chair[1] + APPROACH_OFFSET[1]],
  };
}

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

/** Every agent gets a seat by role; within a role, by id, so the result is stable. */
export function assignSeats(agents: readonly { id: string; role: string }[]): Assignment {
  const seats = new Map<string, Seat>();
  const unseated: string[] = [];
  const byRole = new Map<string, string[]>();
  for (const agent of [...agents].sort((a, b) => a.id.localeCompare(b.id))) {
    byRole.set(agent.role, [...(byRole.get(agent.role) ?? []), agent.id]);
  }
  let spareUsed = 0;
  for (const [role, ids] of [...byRole].sort(([a], [b]) => a.localeCompare(b))) {
    ids.forEach((id, i) => {
      const slots = SLOTS[role];
      if (slots && i < slots.desks.length) seats.set(id, seatAt(role, slots, i));
      else if (!slots && spareUsed < SPARE.desks.length) seats.set(id, seatAt(role, SPARE, spareUsed++));
      else unseated.push(id);
    });
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

export type WalkTarget = Seat | "approval";

function approachOf(target: WalkTarget): { point: Vec2; lane: Lane } {
  return target === "approval" ? { point: APPROVAL_DESK.approach, lane: "front" } : { point: target.approach, lane: target.lane };
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

export function obstacles(): Rect[] {
  const furniture = allSeats().flatMap((seat) => [
    rectAround(seat.desk, DESK.width, DESK.depth, `desk ${seat.key}`),
    rectAround(seat.chair, CHAIR.size, CHAIR.size, `chair ${seat.key}`),
  ]);
  const { minX, maxX, maxZ, doorX, doorWidth } = CEO_OFFICE;
  const t = 0.05; // glass thickness
  return [
    ...furniture,
    rectAround(MEETING_TABLE.center, MEETING_TABLE.width, MEETING_TABLE.depth, "meeting table"),
    rectAround(APPROVAL_DESK.center, APPROVAL_DESK.width, APPROVAL_DESK.depth, "approval desk"),
    { name: "ceo glass (left of door)", minX, maxX: doorX - doorWidth / 2, minZ: maxZ - t, maxZ: maxZ + t },
    { name: "ceo glass (right of door)", minX: doorX + doorWidth / 2, maxX: maxX - t, minZ: maxZ - t, maxZ: maxZ + t },
    { name: "ceo glass (side)", minX: maxX - t, maxX: maxX + t, minZ: CEO_OFFICE.minZ, maxZ },
  ];
}
