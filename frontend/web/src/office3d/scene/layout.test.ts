import { describe, expect, it } from "vitest";

import {
  allSeats,
  APPROVAL_DESK,
  assignSeats,
  CEO_OFFICE,
  obstacles,
  ROLES,
  ROOM,
  seatsForRole,
  SLOTS,
  walkPath,
  type Rect,
  type Seat,
  type Vec2,
} from "./layout";

const WALKER = 0.2; // a walker's radius
const WALL_MARGIN = 0.3;

const overlaps = (a: Rect, b: Rect) => a.minX < b.maxX && b.minX < a.maxX && a.minZ < b.maxZ && b.minZ < a.maxZ;
const inside = ([x, z]: Vec2, r: Rect, pad = 0) => x > r.minX - pad && x < r.maxX + pad && z > r.minZ - pad && z < r.maxZ + pad;
const inRoom = ([x, z]: Vec2) =>
  x >= ROOM.minX + WALL_MARGIN && x <= ROOM.maxX - WALL_MARGIN && z >= ROOM.minZ + WALL_MARGIN && z <= ROOM.maxZ - WALL_MARGIN;

/** Points every 5 cm along a polyline. */
function samples(path: Vec2[]): { point: Vec2; segment: number }[] {
  const out: { point: Vec2; segment: number }[] = [];
  for (let i = 1; i < path.length; i++) {
    const [ax, az] = path[i - 1];
    const [bx, bz] = path[i];
    const steps = Math.max(1, Math.ceil(Math.hypot(bx - ax, bz - az) / 0.05));
    for (let s = 0; s <= steps; s++) out.push({ point: [ax + ((bx - ax) * s) / steps, az + ((bz - az) * s) / steps], segment: i - 1 });
  }
  return out;
}

describe("seats", () => {
  it("every role of the MVP office has a seat; single agents get the first desk", () => {
    expect(ROLES.sort()).toEqual(["analyst", "ceo", "editor", "marketing", "researcher", "writer"]);
    for (const role of ROLES) {
      const [seat] = seatsForRole(role, 1);
      expect(seat.role).toBe(role);
      expect(seat.desk).toEqual(SLOTS[role].desks[0]);
    }
  });

  it("seatsForRole gives up to the area's capacity, never more", () => {
    expect(seatsForRole("researcher", 2)).toHaveLength(2);
    expect(seatsForRole("researcher", 5)).toHaveLength(SLOTS.researcher.desks.length);
    expect(seatsForRole("marketing", 3).map((s) => s.key)).toEqual(["growth:marketing:0", "growth:marketing:1", "growth:marketing:2"]);
    expect(seatsForRole("unknown_role", 1)).toEqual([]);
  });

  it("all furniture is inside the room and nothing overlaps", () => {
    const rects = obstacles();
    for (const r of rects) {
      expect(r.minX, r.name).toBeGreaterThanOrEqual(ROOM.minX);
      expect(r.maxX, r.name).toBeLessThanOrEqual(ROOM.maxX);
      expect(r.minZ, r.name).toBeGreaterThanOrEqual(ROOM.minZ);
      expect(r.maxZ, r.name).toBeLessThanOrEqual(ROOM.maxZ);
    }
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        expect(overlaps(rects[i], rects[j]), `${rects[i].name} / ${rects[j].name}`).toBe(false);
      }
    }
  });

  it("the CEO sits inside the glass office; nobody else does", () => {
    const office: Rect = { name: "ceo office", ...CEO_OFFICE };
    for (const seat of allSeats()) expect(inside(seat.chair, office), seat.key).toBe(seat.role === "ceo");
  });

  it("assignment: by role, stable by id, spare desks for unknown roles, overflow listed", () => {
    const agents = [
      { id: "b", role: "researcher" },
      { id: "a", role: "researcher" },
      { id: "c", role: "researcher" },
      { id: "d", role: "fact_checker" },
      { id: "e", role: "writer" },
    ];
    const { seats, unseated } = assignSeats(agents);
    expect(seats.get("a")!.key).toBe("research:researcher:0");
    expect(seats.get("b")!.key).toBe("research:researcher:1");
    expect(unseated).toEqual(["c"]);
    expect(seats.get("d")!.zone).toBe("spare");
    expect(seats.get("e")!.key).toBe("editorial:writer:0");
    // the same agents in another order: the same seats
    const again = assignSeats([...agents].reverse());
    for (const [id, seat] of seats) expect(again.seats.get(id)!.key).toBe(seat.key);
  });
});

describe("courier paths", () => {
  const seats = allSeats();
  const targets: (Seat | "approval")[] = [...seats, "approval"];
  const rects = obstacles();

  it("from every desk to every other desk and to the approval desk: inside the room, around all furniture and glass", () => {
    let checked = 0;
    const problems: string[] = [];
    for (const from of seats) {
      for (const to of targets) {
        if (to === from) continue;
        const name = `${from.key} -> ${to === "approval" ? to : to.key}`;
        for (const { point, segment } of samples(walkPath(from, to))) {
          if (!inRoom(point)) problems.push(`${name} leaves the room at ${point}`);
          for (const rect of rects) {
            // standing up from one's own chair is the one allowed contact
            if (segment === 0 && rect.name === `chair ${from.key}`) continue;
            if (inside(point, rect, WALKER)) problems.push(`${name} hits ${rect.name} at ${point}`);
          }
        }
        checked++;
      }
    }
    expect([...new Set(problems)].slice(0, 10)).toEqual([]);
    expect(checked).toBe(seats.length * seats.length);
  });

  it("starts at the chair, ends next to the target, and only turns at right angles", () => {
    const [researcher] = seatsForRole("researcher", 1);
    const [ceo] = seatsForRole("ceo", 1);
    const toCeo = walkPath(researcher, ceo);
    expect(toCeo[0]).toEqual(researcher.chair);
    expect(toCeo.at(-1)).toEqual(ceo.approach);
    for (let i = 1; i < toCeo.length; i++) {
      const [ax, az] = toCeo[i - 1];
      const [bx, bz] = toCeo[i];
      expect(ax === bx || az === bz).toBe(true);
    }
    // the CEO office is entered through its door
    const door = toCeo.filter(([, z]) => z < CEO_OFFICE.maxZ && z > CEO_OFFICE.maxZ - 1);
    for (const [x] of door) expect(Math.abs(x - CEO_OFFICE.doorX)).toBeLessThan(CEO_OFFICE.doorWidth / 2);
    expect(walkPath(researcher, "approval").at(-1)).toEqual(APPROVAL_DESK.approach);
  });
});
