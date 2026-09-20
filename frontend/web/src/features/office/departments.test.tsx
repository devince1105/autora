// @vitest-environment jsdom
// T-600 batch 3: the office is the organisation, so the way into a part of it is its department.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { realtimeStore, type AgentState } from "@/stores/realtime";
import { uiStore } from "@/stores/ui";

import { DepartmentStrip, departmentsOf } from "./Departments";

afterEach(() => {
  cleanup();
  realtimeStore.getState().reset();
  uiStore.getState().reset();
});

const agent = (over: Partial<AgentState> & { id: string }): AgentState => ({
  role: "researcher",
  display_name: over.id,
  avatar_key: "default",
  department_id: null,
  department_key: null,
  office_zone_key: null,
  business_unit_key: null,
  activity: null,
  liveProgress: null,
  ...over,
});

/** The strip asks the org chart for the departments' names; here nobody answers, so keys show. */
const strip = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DepartmentStrip companyId="c1" />
    </QueryClientProvider>,
  );

const hydrate = (agents: AgentState[]) =>
  realtimeStore.setState({
    company: {
      companyId: "c1",
      lastSeq: 1,
      agents: Object.fromEntries(agents.map((a) => [a.id, a])),
      tasks: {},
      recentEvents: [],
      cycle: null,
    },
  });

describe("the departments a company actually has", () => {
  it("one entry per department, with its headcount and its room", () => {
    const entries = departmentsOf([
      { department_key: "newsroom_research", office_zone_key: "research", business_unit_key: "ai_media", role: "researcher" },
      { department_key: "newsroom_research", office_zone_key: "research", business_unit_key: "ai_media", role: "analyst" },
      { department_key: "executive", office_zone_key: "ceo", business_unit_key: null, role: "ceo" },
    ]);
    expect(entries.map((d) => [d.key, d.headcount, d.zone])).toEqual([
      ["executive", 1, "ceo"], // the company's own function first: it belongs to no business
      ["newsroom_research", 2, "research"],
    ]);
  });

  it("a company with no org chart shows the parts of the floor its agents sit in", () => {
    const entries = departmentsOf([
      { department_key: null, office_zone_key: "research", business_unit_key: null, role: "researcher" },
      { department_key: null, office_zone_key: null, business_unit_key: null, role: "writer" },
    ]);
    // the one with a room is a room to enter; the one with nowhere is not invented
    expect(entries.map((d) => d.key)).toEqual(["research"]);
  });
});

describe("entering a department", () => {
  it("a click enters it, another leaves, and the whole floor is always one click away", () => {
    hydrate([
      agent({ id: "a", department_key: "newsroom_research", office_zone_key: "research" }),
      agent({ id: "b", role: "writer", department_key: "newsroom_writing", office_zone_key: "editorial" }),
    ]);
    strip();

    fireEvent.click(screen.getByTestId("department-newsroom_research"));
    expect(uiStore.getState().focusedDepartment).toEqual({
      key: "newsroom_research",
      zone: "research",
    });
    expect(screen.getByTestId("department-newsroom_research").getAttribute("aria-pressed")).toBe("true");

    // the same button again steps back out
    fireEvent.click(screen.getByTestId("department-newsroom_research"));
    expect(uiStore.getState().focusedDepartment).toBeNull();

    fireEvent.click(screen.getByTestId("department-newsroom_writing"));
    fireEvent.click(screen.getByTestId("department-all"));
    expect(uiStore.getState().focusedDepartment).toBeNull();
  });

  it("entering does not change who is selected", () => {
    hydrate([agent({ id: "a", department_key: "newsroom_research", office_zone_key: "research" })]);
    uiStore.getState().selectAgent("a");
    strip();
    fireEvent.click(screen.getByTestId("department-newsroom_research"));
    expect(uiStore.getState().selectedAgentId).toBe("a");
  });

  it("a company with nobody placed anywhere shows no strip at all", () => {
    hydrate([agent({ id: "a" })]);
    const { container } = strip();
    expect(container.firstChild).toBeNull();
  });
});
