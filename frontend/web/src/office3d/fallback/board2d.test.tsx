// @vitest-environment jsdom
import { parseEvent, type EventEnvelope } from "@autora/event-schema";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createRealtimeStore, realtimeStore, type RealtimeState } from "@/stores/realtime";
import { uiStore } from "@/stores/ui";

import { arcBetween, boardModel, handoffsAfter } from "./board";
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
    const rooms = screen.getAllByRole("region");
    expect(rooms.flatMap((room) => within(room).getAllByRole("button"))).toHaveLength(6);
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
