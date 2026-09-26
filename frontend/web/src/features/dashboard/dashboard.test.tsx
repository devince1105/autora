// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { applyEvent, hydrate } from "@/realtime/reducer";
import { RealtimeSnapshot } from "@/realtime/snapshot";
import { parseEvent } from "@autora/event-schema";

import { DashboardView } from "./DashboardView";
import { dashboardModel, formatMoney, type KpisData } from "./model";

const fixture = JSON.parse(
  // jsdom's import.meta.url is not a file URL; vitest runs from the web package root.
  readFileSync(join(process.cwd(), "src/realtime/__fixtures__/contract.json"), "utf8"),
) as { snapshot_before: unknown; events: unknown[]; snapshot_after: { server_time: string } };

function company(events = fixture.events) {
  let state = hydrate(RealtimeSnapshot.parse(fixture.snapshot_before));
  for (const raw of events) {
    const parsed = parseEvent(raw);
    if (parsed.ok) state = applyEvent(state, parsed.event);
  }
  return state;
}

const kpis: KpisData = {
  as_of: "2026-09-18T12:00:00Z",
  currency: "USD",
  cash: "103.000000",
  revenue_today: "12.500000",
  expenses_today: "2.400000",
  model_cost_today: "0.400000",
  domain_metrics: { "newsroom.published_articles": 3 },
  goal: { title: "發布 3 篇雙語文章", metric: "published_articles", target: "3", current: "1", deadline: null },
};
const live = { status: "live" as const, serverOffsetMs: 0, lastEventAt: Date.now() };

/** A day the CEO planned: the words are the plan's, the count is what was measured (AC-12). */
const planned = (goals: { metric: string; title?: string; target?: number; current?: number }[], stage = "EXECUTING") =>
  [{
    id: "01a0b900-0000-7000-8000-000000000001", seq: 7, stage, started_at: "2026-09-20T00:00:00Z",
    ended_at: null, stage_deadline: null, planned_by: "ceo", goals, review: null,
    review_missing: null, workflows: 2, failed_tasks: 0, cost: "1.500000", currency: "TWD",
  }] as Parameters<typeof dashboardModel>[4];

afterEach(cleanup);

describe("dashboard model: only real data", () => {
  it("counts agents and tasks from the realtime state", () => {
    const now = new Date(fixture.snapshot_after.server_time);
    const state = company();
    const model = dashboardModel(state, kpis, live, now);
    const agents = Object.values(state.agents);
    expect(model.agents.total).toBe(agents.length);
    expect(model.agents.busy + model.agents.waiting + model.agents.paused + model.agents.failed)
      .toBeLessThanOrEqual(agents.length);
    const unfinished = Object.values(state.tasks).filter((t) =>
      ["PENDING", "READY", "RUNNING", "WAITING_APPROVAL", "BLOCKED_BUDGET"].includes(t.state),
    );
    expect(model.tasks.active).toBe(unfinished.length);
  });

  it("takes money from the KPIs, nothing when they are not loaded", () => {
    const now = new Date();
    const model = dashboardModel(company([]), kpis, live, now);
    expect(model.money).toEqual({
      currency: "USD", cash: 103, revenueToday: 12.5, expensesToday: 2.4, modelCostToday: 0.4,
    });
    const empty = dashboardModel(null, undefined, { ...live, status: "connecting" }, now);
    expect(empty.money).toBeNull();
    expect(empty.agents.total).toBe(0);
    expect(empty.goal).toBeNull();
  });

  it("today's goal is the day's plan; its progress is what was counted (AC-12)", () => {
    const now = new Date();
    const cycles = planned([
      { metric: "published_articles", title: "發布 5 篇雙語文章", target: 5, current: 3 },
    ]);
    const model = dashboardModel(company([]), kpis, live, now, cycles);
    // the plan says 5 and the count says 3 — not the standing goal's 3 / 1
    expect(model.goal).toEqual({
      title: "發布 5 篇雙語文章", current: 3, target: 5, deadline: null, source: "cycle",
    });
  });

  it("a goal nobody has measured yet still shows what the day is for", () => {
    const cycles = planned([{ metric: "published_articles", title: "發布 5 篇雙語文章", target: 5 }]);
    const model = dashboardModel(company([]), kpis, live, new Date(), cycles);
    expect(model.goal).toMatchObject({ current: null, target: 5, source: "cycle" });
  });

  it("with no cycle at all, the standing goal from the KPIs is shown instead", () => {
    const model = dashboardModel(company([]), kpis, live, new Date(), []);
    expect(model.goal).toEqual({
      title: "發布 3 篇雙語文章", current: 1, target: 3, deadline: null, source: "kpi",
    });
  });

  it("reports how stale the data is when not live", () => {
    const now = new Date();
    const offline = { status: "offline" as const, serverOffsetMs: 0, lastEventAt: now.getTime() - 42_000 };
    expect(dashboardModel(company([]), kpis, offline, now).connection.staleSeconds).toBe(42);
    expect(dashboardModel(company([]), kpis, live, now).connection.staleSeconds).toBeNull();
  });

  it("formats money for Taiwan, keeping small model costs readable", () => {
    expect(formatMoney(103, "USD")).toBe("US$103.00");
    expect(formatMoney(0.0035, "USD")).toBe("US$0.0035");
  });
});

describe("dashboard view", () => {
  it("shows every tile from the model", () => {
    const model = dashboardModel(company(), kpis, live, new Date(fixture.snapshot_after.server_time));
    render(<DashboardView companyId="c1" companyName="Echo Demo" model={model} />);
    expect(screen.getByRole("heading", { name: "Echo Demo" })).toBeTruthy();
    expect(within(screen.getByTestId("cash")).getByText("US$103.00")).toBeTruthy();
    expect(within(screen.getByTestId("expenses")).getByText(/其中模型費用 US\$0\.40/)).toBeTruthy();
    expect(within(screen.getByTestId("agents")).getByText(`/ ${model.agents.total}`, { exact: false })).toBeTruthy();
    expect(within(screen.getByTestId("tasks")).getByText(String(model.tasks.active))).toBeTruthy();
    expect(within(screen.getByTestId("published")).getByText("3")).toBeTruthy();
    expect(within(screen.getByTestId("goal")).getByText("發布 3 篇雙語文章")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("即時");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("marks offline data as stale instead of blanking it", () => {
    const now = new Date();
    const offline = { status: "offline" as const, serverOffsetMs: 0, lastEventAt: now.getTime() - 30_000 };
    render(<DashboardView companyId="c1" companyName="Echo Demo" model={dashboardModel(company(), kpis, offline, now)} />);
    expect(screen.getByRole("status").textContent).toContain("資料可能已過期 30 秒");
    expect(screen.getByRole("alert").textContent).toContain("畫面保留最後的狀態");
    expect(within(screen.getByTestId("cash")).getByText("US$103.00")).toBeTruthy();
  });

  it("shows placeholders, not zeros, before the KPIs arrive", () => {
    render(<DashboardView companyId="c1" companyName="Echo Demo" model={dashboardModel(null, undefined, live, new Date())} />);
    expect(within(screen.getByTestId("cash")).getByText("—")).toBeTruthy();
    expect(within(screen.getByTestId("goal")).getByText("尚未設定")).toBeTruthy();
  });
});

describe("dashboard links", () => {
  it("links to the approval inbox with the pending count", () => {
    render(
      <DashboardView
        companyId="c1"
        companyName="Echo Demo"
        model={dashboardModel(null, undefined, live, new Date())}
        pendingApprovals={1}
      />,
    );
    const link = screen.getByTestId("pending-approvals");
    expect(link.getAttribute("href")).toBe("/admin/approvals?company=c1");
    expect(link.textContent).toBe("審批收件匣1");
  });
});
