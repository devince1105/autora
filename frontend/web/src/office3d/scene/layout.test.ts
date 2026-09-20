import { describe, expect, it } from "vitest";

import {
  allSeats,
  APPROVAL_DESK,
  assignSeats,
  CEO_OFFICE,
  CORRIDORS,
  doorOf,
  DECOR,
  ENTRANCE,
  LABELS,
  obstacles,
  ROLES,
  ROOM,
  seatsForRole,
  SLOTS,
  walkPath,
  ZONES,
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

  it("assignment: the desk built for the role first, then any desk in the same room", () => {
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
    // a third researcher is not homeless: research has other desks, and it takes one
    expect(seats.get("c")!.zone).toBe("research");
    expect(unseated).toEqual([]);
    expect(seats.get("d")!.zone).toBe("spare"); // a role the floor plan does not know
    expect(seats.get("e")!.key).toBe("editorial:writer:0");
    // the same agents in another order: the same seats
    const again = assignSeats([...agents].reverse());
    for (const [id, seat] of seats) expect(again.seats.get(id)!.key).toBe(seat.key);
  });

  it("the department decides the room, not the role (T-600 batch 3)", () => {
    // the same writer, once with no org chart and once working for the research desk
    const [alone] = [...assignSeats([{ id: "w", role: "writer" }]).seats.values()];
    const [moved] = [
      ...assignSeats([{ id: "w", role: "writer", office_zone_key: "research" }]).seats.values(),
    ];
    expect(alone.zone).toBe("editorial"); // v1's answer: the role's own zone
    expect(moved.zone).toBe("research"); // v2's: where its department sits
    expect(moved.role).toBe("writer"); // it is still a writer, at a research desk
  });

  it("a zone nobody drew is not a hole: the agent takes a flex desk", () => {
    const { seats, unseated } = assignSeats([
      { id: "x", role: "support", office_zone_key: "warehouse" },
    ]);
    expect(seats.get("x")!.zone).toBe("spare");
    expect(unseated).toEqual([]);
  });

  it("a room fills up and the rest overflow, in a stable order", () => {
    const crowd = Array.from({ length: 12 }, (_, i) => ({
      id: `a${i}`,
      role: "researcher",
      office_zone_key: "research",
    }));
    const { seats, unseated } = assignSeats(crowd);
    const zones = [...seats.values()].map((s) => s.zone);
    expect(zones.filter((z) => z === "research")).toHaveLength(4); // research has four desks
    expect(zones.filter((z) => z === "spare")).toHaveLength(3); // then the flex desks
    expect(unseated).toHaveLength(5);
    expect(assignSeats([...crowd].reverse()).unseated).toEqual(unseated);
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

describe("zones, entrance and labels (T-413)", () => {
  const contains = (outer: Omit<Rect, "name">, inner: Omit<Rect, "name">) =>
    inner.minX >= outer.minX && inner.maxX <= outer.maxX && inner.minZ >= outer.minZ && inner.maxZ <= outer.maxZ;
  const zones = Object.entries(ZONES);

  it("the zones do not overlap each other, the walkways or the back rooms; each holds its desks", () => {
    for (let i = 0; i < zones.length; i++) {
      for (let j = i + 1; j < zones.length; j++) expect(overlaps({ name: "", ...zones[i][1] }, { name: "", ...zones[j][1] }), `${zones[i][0]} / ${zones[j][0]}`).toBe(false);
    }
    for (const [name, zone] of zones) {
      expect(zone.minZ, name).toBeGreaterThanOrEqual(CORRIDORS.back.maxZ);
      expect(overlaps({ name, ...zone }, { name: "front walkway", minX: ROOM.minX, maxX: ROOM.maxX, ...CORRIDORS.front }), name).toBe(false);
    }
    for (const seat of allSeats()) {
      if (seat.zone === "ceo") continue;
      const zone = ZONES[seat.zone as keyof typeof ZONES];
      expect(contains(zone, rectAroundSeat(seat)), seat.key).toBe(true);
    }
  });

  it("the entrance opens the right edge onto the front walkway, next to the lobby with the reception and lounge", () => {
    expect(ENTRANCE.x).toBe(ROOM.maxX);
    expect([ENTRANCE.minZ, ENTRANCE.maxZ]).toEqual([CORRIDORS.front.minZ, CORRIDORS.front.maxZ]);
    const desk = { minX: APPROVAL_DESK.center[0] - APPROVAL_DESK.width / 2, maxX: APPROVAL_DESK.center[0] + APPROVAL_DESK.width / 2, minZ: APPROVAL_DESK.center[1] - APPROVAL_DESK.depth / 2, maxZ: APPROVAL_DESK.center[1] + APPROVAL_DESK.depth / 2 };
    expect(contains(ZONES.lobby, desk)).toBe(true);
    const lounge = DECOR.find((item) => item.kind === "lounge")!;
    expect(inside(lounge.at, { name: "lobby", ...ZONES.lobby })).toBe(true);
    // nothing stands in the doorway
    const doorway: Rect = { name: "doorway", minX: ROOM.maxX - 1.2, maxX: ROOM.maxX, minZ: ENTRANCE.minZ, maxZ: ENTRANCE.maxZ };
    for (const rect of obstacles()) expect(overlaps(rect, doorway), rect.name).toBe(false);
  });

  it("floor labels lie in the open (not under furniture) and signs sit on the fronts", () => {
    const rects = obstacles();
    for (const label of LABELS) {
      const [x, y, z] = label.at;
      if (label.kind === "floor") {
        expect(y).toBe(0);
        const area: Rect = { name: label.text, minX: x - label.width / 2, maxX: x + label.width / 2, minZ: z - label.width / 8, maxZ: z + label.width / 8 };
        expect(inRoom([area.minX, area.minZ]) && inRoom([area.maxX, area.maxZ]), label.text).toBe(true);
        for (const rect of rects) expect(overlaps(area, rect), `${label.text} / ${rect.name}`).toBe(false);
      } else {
        expect(y - label.width / 8, label.text).toBeGreaterThan(2.2); // above the doors
      }
    }
    expect(new Set(LABELS.map((l) => l.text)).size).toBe(LABELS.length);
  });
});

function rectAroundSeat(seat: Seat): Omit<Rect, "name"> {
  return { minX: Math.min(seat.desk[0], seat.chair[0]) - 0.75, maxX: Math.max(seat.desk[0], seat.chair[0]) + 0.75, minZ: seat.desk[1] - 0.4, maxZ: seat.chair[1] + 0.3 };
}

describe("the door of a department (T-600 batch 4)", () => {
  it("every open-plan room is entered from the front corridor, the CEO's through its door", () => {
    for (const zone of ["research", "editorial", "growth", "spare", "lobby"] as const) {
      const { point, lane } = doorOf(zone);
      const area = ZONES[zone];
      expect(lane).toBe("front");
      expect(point[0]).toBeGreaterThan(area.minX);
      expect(point[0]).toBeLessThan(area.maxX);
      // just outside the room, in the walkway, and clear of the furniture
      expect(point[1]).toBeGreaterThan(CORRIDORS.front.minZ - 0.1);
      expect(point[1]).toBeLessThan(CORRIDORS.front.maxZ + 0.1);
      expect(obstacles().some((o) => inside(point, o))).toBe(false);
    }
    const ceo = doorOf("ceo");
    expect(ceo.lane).toBe("back");
    expect(Math.abs(ceo.point[0] - CEO_OFFICE.doorX)).toBeLessThan(0.01);
  });

  it("a walk to a door ends there, and the path stays out of the furniture", () => {
    const [seat] = seatsForRole("researcher", 1);
    const path = walkPath(seat, { door: "editorial" });
    expect(path.at(-1)).toEqual(doorOf("editorial").point);
    for (const { point } of samples(path)) {
      expect(inRoom(point)).toBe(true);
    }
  });
});
