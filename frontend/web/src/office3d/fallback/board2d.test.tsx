// @vitest-environment jsdom
import { parseEvent, type EventEnvelope } from "@autora/event-schema";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createRealtimeStore, realtimeStore, type RealtimeState } from "@/stores/realtime";
import { uiStore } from "@/stores/ui";

import { arcBetween, boardModel, floorPlan, handoffsAfter } from "./board";
import { buildScene, hitTest, TILE } from "./tiles";
import { assignSeats } from "../scene/layout";
import { HANDOFF_MS, OfficeBoard2D } from "./OfficeBoard2D";

// A randomised real runtime history (the T-302 contract fixture): six agents, three roles.
const fixture = JSON.parse(readFileSync(join(process.cwd(), "src/realtime/__fixtures__/contract.json"), "utf8")) as {
  snapshot_before: unknown;
  events: { event_type: string; payload: Record<string, unknown>; occurred_at: string }[];
};
const events: EventEnvelope[] = fixture.events.map((raw) => {
  const parsed = parseEvent(raw);
  if (!parsed.ok) throw new Error(parsed.error);
  return parsed.event;
});
const handoffRoles = (e: { payload: Record<string, unknown> }) =>
  ((e.payload.handoff as { to_role: string }[] | undefined) ?? []).map((h) => h.to_role);
// the first hand-off to a role somebody on the roster holds: a hand-off to "human" is the
// approval desk and draws no arrow between cards
const rosterRoles = new Set(
  (fixture.snapshot_before as { agents: { role: string }[] }).agents.map((a) => a.role),
);
const handoffIndex = fixture.events.findIndex(
  (e) => e.event_type === "AGENT_RUN_COMPLETED" && handoffRoles(e).some((r) => rosterRoles.has(r)),
);

function replay(until = events.length): RealtimeState {
  const store = createRealtimeStore();
  store.getState().hydrate(fixture.snapshot_before);
  store.getState().applyEvents(fixture.events.slice(0, until));
  return store.getState().company!;
}

function plan() {
  const company = replay(0);
  const agents = Object.values(company.agents);
  return floorPlan(agents.map((agent) => agent.id), assignSeats(agents).seats);
}

function cards() {
  return new Map(
    boardModel(replay(0), new Date()).flatMap((row) => row.cards).map((card) => [card.id, card]),
  );
}

afterEach(() => {
  cleanup();
  realtimeStore.getState().reset();
  uiStore.getState().reset();
  vi.useRealTimers();
});

describe("board model", () => {
  const company = replay();
  const now = new Date(events.at(-1)!.occurred_at);

  it("one row per department, in floor order, left to right inside each", () => {
    // this company is not on an org chart, so each agent's row is the part of the floor it
    // sits in — which is what a company without departments honestly has (T-600 batch 3)
    const rows = boardModel(company, now);
    expect(rows.map((r) => r.id)).toEqual(["research", "editorial"]);
    expect(rows.flatMap((r) => r.cards)).toHaveLength(6);
    const research = rows[0].cards.map((c) => c.roleLabel);
    expect(research.every((label) => label === "研究員" || label === "分析師")).toBe(true);
    expect(rows[1].cards.every((c) => c.roleLabel === "寫手")).toBe(true);
  });

  it("an agent whose department the chart names gets a row of its own", () => {
    const withDesk = {
      ...company,
      agents: Object.fromEntries(
        Object.entries(company.agents).map(([id, agent], i) => [
          id,
          i === 0 ? { ...agent, department_key: "newsroom_research" } : agent,
        ]),
      ),
    };
    const rows = boardModel(withDesk, now);
    expect(rows.map((r) => r.id)).toContain("newsroom_research");
    expect(rows.find((r) => r.id === "newsroom_research")!.cards).toHaveLength(1);
  });

  it("each card: role colour, the mapping's badge, task, time in state, last completion", () => {
    const cards = boardModel(company, now).flatMap((r) => r.cards);
    for (const card of cards) {
      expect(card.color).toMatch(/^#[0-9a-f]{6}$/);
      expect(card.visual.badge.text.length).toBeGreaterThan(0);
      expect(card.sinceMs).toBeGreaterThanOrEqual(0);
    }
    const done = cards.filter((c) => c.lastDone);
    expect(done.length).toBeGreaterThan(0);
    const completedIds = new Set(events.filter((e) => e.event_type === "AGENT_RUN_COMPLETED").map((e) => e.agent_id));
    for (const c of done) expect(completedIds.has(c.id)).toBe(true);
  });

  it("no company, no rows", () => {
    expect(boardModel(null, now)).toEqual([]);
  });

  it("a hand-off goes from the finishing agent to the agents of the next role", () => {
    const handoffs = handoffsAfter(company.recentEvents, 0, company.agents);
    expect(handoffs.length).toBeGreaterThan(0);
    for (const handoff of handoffs) {
      const event = company.recentEvents.find((e) => e.seq === handoff.seq)!;
      const roles = new Set(handoffRoles(event as unknown as { payload: Record<string, unknown> }));
      expect(handoff.to.length).toBeGreaterThan(0);
      for (const id of handoff.to) {
        expect(roles.has(company.agents[id].role)).toBe(true);
        expect(id).not.toBe(handoff.from); // nobody hands work to themselves
      }
    }
    expect(handoffsAfter(company.recentEvents, handoffs.at(-1)!.seq, company.agents)).toEqual([]);
  });
});

describe("the floor as tiles", () => {
  const scene = () => buildScene(plan(), cards());

  it("every room is on the map, walled ones with walls around them", () => {
    const { map } = scene();
    const zones = new Set(map.zone.filter(Boolean));
    for (const room of ["research", "editorial", "growth", "spare", "lobby", "ceo", "meeting", "pantry"]) {
      expect(zones.has(room)).toBe(true);
    }
    // the three rooms at the back have walls; the open-plan zones are carpet
    const kindsOf = (zone: string) =>
      new Set(map.tiles.filter((_, i) => map.zone[i] === zone));
    expect(kindsOf("ceo").has("wall")).toBe(true);
    expect(kindsOf("meeting").has("wall")).toBe(true);
    expect(kindsOf("research").has("wall")).toBe(false);
    expect(kindsOf("research").has("carpet")).toBe(true);
  });

  it("a desk for every seat, a person at the taken ones, and each knows its room", () => {
    const built = scene();
    const desks = built.props.filter((prop) => prop.kind === "desk");
    expect(desks.length).toBeGreaterThan(built.npcs.length);
    expect(built.npcs).toHaveLength(6);
    expect(built.npcs.every((npc) => npc.zone !== null)).toBe(true);
    expect(new Set(built.npcs.map((npc) => npc.color)).size).toBeGreaterThan(1);
  });

  it("each room has the furniture that makes it that room", () => {
    const kinds = (zone: string) =>
      new Set(scene().props.filter((prop) => prop.zone === zone).map((prop) => prop.kind));
    expect(kinds("lobby")).toContain("counter"); // reception, which is also the approval desk
    expect(kinds("lobby")).toContain("sofa");
    expect(kinds("pantry")).toContain("fridge");
    expect(kinds("pantry")).toContain("stove");
    expect(kinds("meeting")).toContain("whiteboard");
    expect(kinds("ceo")).toContain("shelf");
    expect(scene().props.some((prop) => prop.kind === "door")).toBe(true);
  });

  it("clicking the floor is not clicking a person", () => {
    const built = scene();
    const someone = built.npcs[0];
    expect(hitTest(built, someone.x + 4, someone.y + 6)).toBe(someone.agentId);
    expect(hitTest(built, 1, 1)).toBeNull();
  });
});

describe("hand-off arrow geometry", () => {
  it("arcs from the top of one card to the top of the other, above both", () => {
    const a = { left: 0, top: 100, width: 200, height: 100 };
    const b = { left: 212, top: 100, width: 200, height: 100 };
    const { d, peakY } = arcBetween(a, b, 36);
    expect(d.startsWith("M 100 96 Q 206 ")).toBe(true);
    expect(d.endsWith(" 312 96")).toBe(true);
    expect(peakY).toBeLessThan(96 - 36); // rises above the cards, more for farther ones
    expect(arcBetween(a, { ...b, left: 800 }).peakY).toBeLessThan(peakY);
  });
});

describe("OfficeBoard2D", () => {
  it("shows every agent; a click selects it (as picking an avatar does)", () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    render(<OfficeBoard2D />);
    const cards = screen.getAllByTestId(/^board-agent-/);
    expect(cards).toHaveLength(6);
    fireEvent.click(cards[2]);
    expect(uiStore.getState().selectedAgentId).toBe(cards[2].getAttribute("data-testid")!.slice("board-agent-".length));
    expect(cards[2].getAttribute("aria-pressed")).toBe("true");
    const floor = screen.getByRole("region", { name: "樓層" });
    expect(within(floor).getAllByTestId(/^board-agent-/)).toHaveLength(6);
  });

  it("the roster column lists everybody, and picking one there selects it too", () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    render(<OfficeBoard2D />);
    const roster = screen.getByRole("complementary", { name: "人員" });
    const people = within(roster).getAllByTestId(/^roster-/);

    expect(people).toHaveLength(6);
    fireEvent.click(people[1]);

    const id = people[1].getAttribute("data-testid")!.slice("roster-".length);
    expect(uiStore.getState().selectedAgentId).toBe(id);
    expect(people[1].getAttribute("aria-pressed")).toBe("true");
  });

  it("a room tab shows that room only; the floor shows them all", () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    render(<OfficeBoard2D />);
    const floor = screen.getByRole("region", { name: "樓層" });
    const rooms = boardModel(replay(0), new Date());
    expect(rooms.length).toBeGreaterThan(1);

    fireEvent.click(screen.getByTestId(`room-tab-${rooms[0].id}`));

    const shown = within(floor).getAllByTestId(/^board-agent-/);
    expect(shown).toHaveLength(rooms[0].cards.length);
    expect(shown.length).toBeLessThan(6);
    // the roster still has everybody: the tab narrows the floor, not the company
    expect(within(screen.getByRole("complementary", { name: "人員" })).getAllByTestId(/^roster-/)).toHaveLength(6);

    fireEvent.click(screen.getByTestId("room-tab-all"));
    expect(within(floor).getAllByTestId(/^board-agent-/)).toHaveLength(6);
  });

  it("the floor is one canvas of pixels, and clicking a person selects them", () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    render(<OfficeBoard2D />);
    const floor = screen.getByTestId("room-plan") as HTMLCanvasElement;

    // its own low resolution: one tile is one metre of the floor the 3D office is built from
    expect(floor.tagName).toBe("CANVAS");
    expect(floor.width % TILE).toBe(0);
    expect(floor.width).toBeGreaterThan(floor.height); // the floor is wider than it is deep

    const scene = buildScene(plan(), cards());
    const someone = scene.npcs[0];
    floor.getBoundingClientRect = () => ({ left: 0, top: 0, width: scene.width, height: scene.height }) as DOMRect;
    fireEvent.click(floor, { clientX: someone.x + 5, clientY: someone.y + 7 });

    expect(uiStore.getState().selectedAgentId).toBe(someone.agentId);
  });

  it("the ticker says what the store knows, and nothing it does not", () => {
    realtimeStore.setState({ company: replay(30) });
    render(<OfficeBoard2D />);
    const ticker = screen.getByTestId("office-ticker");

    expect(ticker.textContent).toContain("代理");
    expect(ticker.textContent).toContain("任務");
    expect(ticker.textContent).toContain("待審批");
    // no money: the ledger's numbers are the page's, and a second copy could disagree with it
    expect(ticker.textContent).not.toMatch(/\$|NT/);
  });

  it("the log column says what just happened, newest first", () => {
    const company = replay(20);
    realtimeStore.setState({ company });
    render(<OfficeBoard2D />);

    const log = screen.getByRole("complementary", { name: "即時紀錄" });
    const lines = within(log).getAllByRole("listitem");

    expect(lines.length).toBeGreaterThan(1);
    const times = lines.map((line) => within(line).getByRole("time").getAttribute("datetime")!);
    expect([...times]).toEqual([...times].sort().reverse());
  });

  it("a hand-off that arrives flashes an arrow between the cards, then it goes", () => {
    vi.useFakeTimers({ now: new Date(fixture.events[handoffIndex].occurred_at) });
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    act(() => void realtimeStore.getState().applyEvents(fixture.events.slice(0, handoffIndex)));
    render(<OfficeBoard2D />);
    expect(screen.queryAllByTestId("handoff-arrow")).toHaveLength(0);

    act(() => void realtimeStore.getState().applyEvents([fixture.events[handoffIndex]]));
    const arrows = screen.getAllByTestId("handoff-arrow");
    const company = realtimeStore.getState().company!;
    const to = new Set(handoffRoles(fixture.events[handoffIndex]));
    const from = fixture.events[handoffIndex] as unknown as { agent_id: string };
    expect(arrows.length).toBeGreaterThan(0);
    for (const arrow of arrows) {
      expect(arrow.getAttribute("data-from")).toBe(from.agent_id);
      expect(to.has(company.agents[arrow.getAttribute("data-to")!].role)).toBe(true);
    }
    act(() => void vi.advanceTimersByTime(HANDOFF_MS + 10));
    expect(screen.queryAllByTestId("handoff-arrow")).toHaveLength(0);
  });

  it("hand-offs already in the buffer when the board opens are not replayed", () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    realtimeStore.getState().applyEvents(fixture.events);
    render(<OfficeBoard2D />);
    expect(screen.queryAllByTestId("handoff-arrow")).toHaveLength(0);
  });

  it("no agents yet", () => {
    render(<OfficeBoard2D />);
    expect(screen.getByText("這間公司還沒有代理。")).toBeTruthy();
  });
});

describe("the board says which business each room works for (T-600 batch 4)", () => {
  const withBusiness = (agents: RealtimeState["agents"]) =>
    Object.fromEntries(
      Object.entries(agents).map(([id, agent], i) => [
        id,
        {
          ...agent,
          department_key: i < 2 ? "newsroom_research" : "newsroom_writing",
          office_zone_key: i < 2 ? "research" : "editorial",
          business_unit_key: i < 4 ? "ai_media" : "ai_edu",
        },
      ]),
    );

  it("rooms of one business share a colour; another business gets its own", () => {
    const company = replay();
    const rows = boardModel({ ...company, agents: withBusiness(company.agents) }, new Date(events.at(-1)!.occurred_at));
    const colours = new Map(rows.map((r) => [r.business, r.businessColor]));
    expect(colours.get("ai_media")).toBeTruthy();
    expect(colours.get("ai_edu")).toBeTruthy();
    expect(colours.get("ai_media")).not.toBe(colours.get("ai_edu"));
  });

  it("a room with nobody's business shows no colour at all", () => {
    const company = replay();
    const rows = boardModel(company, new Date(events.at(-1)!.occurred_at));
    expect(rows.every((r) => r.businessColor === null)).toBe(true);
  });
});
