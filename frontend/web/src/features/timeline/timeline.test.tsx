// @vitest-environment jsdom
import { parseEvent, type EventEnvelope } from "@autora/event-schema";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { applyEvent, hydrate, RECENT_EVENTS_KEPT, type RealtimeState } from "@/realtime/reducer";
import { RealtimeSnapshot } from "@/realtime/snapshot";
import { uiStore } from "@/stores/ui";

import { countNewer, filterOptions, timelineItems } from "./model";
import { Timeline } from "./Timeline";

// A randomised real runtime history written by the Python contract test (make realtime-fixture).
const fixture = JSON.parse(
  readFileSync(join(process.cwd(), "src/realtime/__fixtures__/contract.json"), "utf8"),
) as { snapshot_before: unknown; events: unknown[] };
const events: EventEnvelope[] = fixture.events.map((raw) => {
  const parsed = parseEvent(raw);
  if (!parsed.ok) throw new Error(parsed.error);
  return parsed.event;
});
const replayed: RealtimeState = events.reduce(applyEvent, hydrate(RealtimeSnapshot.parse(fixture.snapshot_before)));
const recent = replayed.recentEvents;
const NONE = { agentIds: [], eventTypes: [] };

afterEach(() => {
  cleanup();
  uiStore.getState().reset();
});

describe("model", () => {
  it("every buffered event, newest first by seq", () => {
    const items = timelineItems(recent, replayed.agents, NONE);
    expect(items.map((i) => i.seq)).toEqual(recent.map((e) => e.seq).sort((a, b) => b! - a!));
    expect(items.length).toBe(recent.length);
    const withAgent = items.find((i) => i.agentId && replayed.agents[i.agentId]);
    expect(withAgent?.actor).toBe(replayed.agents[withAgent!.agentId!].display_name);
  });

  it("filters by agent, by type, and both", () => {
    const agentId = recent.find((e) => e.agent_id)!.agent_id!;
    const byAgent = timelineItems(recent, replayed.agents, { agentIds: [agentId], eventTypes: [] });
    expect(byAgent.length).toBe(recent.filter((e) => e.agent_id === agentId).length);
    expect(byAgent.every((i) => i.agentId === agentId)).toBe(true);

    const byType = timelineItems(recent, replayed.agents, { agentIds: [], eventTypes: ["TASK_CREATED", "AGENT_THINKING"] });
    expect(byType.length).toBe(recent.filter((e) => ["TASK_CREATED", "AGENT_THINKING"].includes(e.event_type)).length);
    expect(new Set(byType.map((i) => i.eventType))).toEqual(new Set(["TASK_CREATED", "AGENT_THINKING"].filter((t) => recent.some((e) => e.event_type === t))));

    const both = timelineItems(recent, replayed.agents, { agentIds: [agentId], eventTypes: ["AGENT_THINKING"] });
    expect(both.every((i) => i.agentId === agentId && i.eventType === "AGENT_THINKING")).toBe(true);
  });

  it("options are the company's agents and the types in the buffer", () => {
    const options = filterOptions(recent, replayed.agents, NONE);
    expect(options.agents.map((a) => a.id).sort()).toEqual(Object.keys(replayed.agents).sort());
    expect(options.types).toEqual([...new Set(recent.map((e) => e.event_type))].sort());
  });

  it("keeps at most 500: the oldest leave the buffer", () => {
    const template = events.find((e) => e.event_type === "AGENT_THINKING")!;
    let state = hydrate(RealtimeSnapshot.parse(fixture.snapshot_before));
    const start = state.lastSeq;
    for (let i = 1; i <= 600; i++) {
      state = applyEvent(state, { ...template, seq: start + i, event_id: crypto.randomUUID() });
    }
    const items = timelineItems(state.recentEvents, state.agents, NONE);
    expect(items).toHaveLength(RECENT_EVENTS_KEPT);
    expect(items[0].seq).toBe(start + 600);
    expect(items.at(-1)!.seq).toBe(start + 101);
    expect(countNewer(state.recentEvents, start + 590, NONE)).toBe(10);
  });
});

describe("view", () => {
  const half = Math.floor(recent.length / 2);

  it("pause freezes the list, counts what arrives, and resume shows it", () => {
    const { rerender } = render(<Timeline companyId={replayed.companyId} events={recent.slice(0, half)} agents={replayed.agents} />);
    const list = () => within(screen.getByRole("list", { name: "事件" })).getAllByRole("listitem");
    expect(list()).toHaveLength(half);

    fireEvent.click(screen.getByRole("button", { name: "暫停" }));
    expect(uiStore.getState().timelinePaused).toBe(true);
    rerender(<Timeline companyId={replayed.companyId} events={recent} agents={replayed.agents} />);
    expect(list()).toHaveLength(half);
    expect(screen.getByRole("status").textContent).toContain(`有 ${recent.length - half} 則新事件`);

    fireEvent.click(screen.getByRole("button", { name: "繼續" }));
    expect(list()).toHaveLength(recent.length);
    expect(list()[0].getAttribute("data-testid")).toBe(`event-${recent.at(-1)!.seq}`);
  });

  it("filter chips narrow the list; clearing restores it", () => {
    render(<Timeline companyId={replayed.companyId} events={recent} agents={replayed.agents} />);
    const type = recent[0].event_type;
    fireEvent.click(screen.getByRole("button", { name: type }));
    expect(uiStore.getState().filters.eventTypes).toEqual([type]);
    const rows = within(screen.getByRole("list", { name: "事件" })).getAllByRole("listitem");
    expect(rows).toHaveLength(recent.filter((e) => e.event_type === type).length);
    fireEvent.click(screen.getByRole("button", { name: "清除篩選" }));
    expect(within(screen.getByRole("list", { name: "事件" })).getAllByRole("listitem")).toHaveLength(recent.length);
  });

  it("a run's event links to its trace and task", () => {
    render(<Timeline companyId={replayed.companyId} events={recent} agents={replayed.agents} />);
    const event = [...recent].reverse().find((e) => e.run_id && e.task_id)!;
    const row = screen.getByTestId(`event-${event.seq}`);
    expect(within(row).getByRole("link", { name: "軌跡" }).getAttribute("href")).toBe(`/trace/${event.run_id}`);
    expect(within(row).getByRole("link", { name: "任務" }).getAttribute("href")).toBe(`/tasks/${event.task_id}`);
  });

  it("a filter set elsewhere (the store) applies too", () => {
    render(<Timeline companyId={replayed.companyId} events={recent} agents={replayed.agents} />);
    act(() => uiStore.getState().setFilters({ eventTypes: ["NO_SUCH_TYPE"] }));
    expect(screen.getByText("沒有符合篩選的事件。")).toBeTruthy();
  });
});
