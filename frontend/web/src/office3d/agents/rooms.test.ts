// T-600: inside a department the office draws that department's people and nobody else (§14.7).
import { describe, expect, it } from "vitest";

import { membersInRoom } from "./Agents";
import type { Member } from "./roster";

const member = (id: string, department: string | null, zone: string | null): Member => ({
  id,
  role: "researcher",
  name: id,
  character: "character-male-a",
  department,
  office_zone_key: zone,
  business_unit: null,
});

const ROSTER: Member[] = [
  member("rae", "newsroom_research", "research"),
  member("ana", "newsroom_research", "research"),
  member("wren", "newsroom_writing", "editorial"),
  member("cyra", "executive", "ceo"),
  member("nobody", null, null),
];

describe("who is on screen", () => {
  it("on the whole floor: everybody, including whoever has no department", () => {
    expect(membersInRoom(ROSTER, null).map((m) => m.id)).toEqual([
      "rae",
      "ana",
      "wren",
      "cyra",
      "nobody",
    ]);
  });

  it("inside a room: its people, and nobody else", () => {
    const inside = membersInRoom(ROSTER, { key: "newsroom_research", zone: "research" });
    expect(inside.map((m) => m.id)).toEqual(["rae", "ana"]);
    expect(membersInRoom(ROSTER, { key: "executive", zone: "ceo" }).map((m) => m.id)).toEqual([
      "cyra",
    ]);
  });

  it("a company with no org chart is entered by the part of the floor people sit in", () => {
    const plain = [member("a", null, "research"), member("b", null, "editorial")];
    expect(membersInRoom(plain, { key: "research", zone: "research" }).map((m) => m.id)).toEqual([
      "a",
    ]);
  });

  it("a room nobody works in is empty, not a crash", () => {
    expect(membersInRoom(ROSTER, { key: "warehouse", zone: "spare" })).toEqual([]);
  });
});
