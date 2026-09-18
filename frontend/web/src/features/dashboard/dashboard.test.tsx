// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { getToken } from "@/api/auth";
import { storeToken, TokenGate } from "@/features/auth/TokenGate";
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
  published_today: null,
  goal: { title: "發布 3 篇雙語文章", metric: "published_articles", target: "3", current: "1", deadline: null },
};
const live = { status: "live" as const, serverOffsetMs: 0, lastEventAt: Date.now() };

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

  it("takes money and the goal from the KPIs, nothing when they are not loaded", () => {
    const now = new Date();
    const model = dashboardModel(company([]), kpis, live, now);
    expect(model.money).toEqual({
      currency: "USD", cash: 103, revenueToday: 12.5, expensesToday: 2.4, modelCostToday: 0.4,
    });
    expect(model.goal).toEqual({ title: "發布 3 篇雙語文章", current: 1, target: 3, deadline: null });
    const empty = dashboardModel(null, undefined, { ...live, status: "connecting" }, now);
    expect(empty.money).toBeNull();
    expect(empty.agents.total).toBe(0);
    expect(empty.goal).toBeNull();
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
    render(<DashboardView companyName="Echo Demo" model={model} />);
    expect(screen.getByRole("heading", { name: "Echo Demo" })).toBeTruthy();
    expect(within(screen.getByTestId("cash")).getByText("US$103.00")).toBeTruthy();
    expect(within(screen.getByTestId("expenses")).getByText(/其中模型費用 US\$0\.40/)).toBeTruthy();
    expect(within(screen.getByTestId("agents")).getByText(`/ ${model.agents.total}`, { exact: false })).toBeTruthy();
    expect(within(screen.getByTestId("tasks")).getByText(String(model.tasks.active))).toBeTruthy();
    expect(within(screen.getByTestId("published")).getByText("文章功能於階段 5 上線")).toBeTruthy();
    expect(within(screen.getByTestId("goal")).getByText("發布 3 篇雙語文章")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("即時");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("marks offline data as stale instead of blanking it", () => {
    const now = new Date();
    const offline = { status: "offline" as const, serverOffsetMs: 0, lastEventAt: now.getTime() - 30_000 };
    render(<DashboardView companyName="Echo Demo" model={dashboardModel(company(), kpis, offline, now)} />);
    expect(screen.getByRole("status").textContent).toContain("資料可能已過期 30 秒");
    expect(screen.getByRole("alert").textContent).toContain("畫面保留最後的狀態");
    expect(within(screen.getByTestId("cash")).getByText("US$103.00")).toBeTruthy();
  });

  it("shows placeholders, not zeros, before the KPIs arrive", () => {
    render(<DashboardView companyName="Echo Demo" model={dashboardModel(null, undefined, live, new Date())} />);
    expect(within(screen.getByTestId("cash")).getByText("—")).toBeTruthy();
    expect(within(screen.getByTestId("goal")).getByText("尚未設定")).toBeTruthy();
  });
});

describe("token gate", () => {
  it("asks for the operator token, then shows the page", () => {
    storeToken(null);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <TokenGate>
          <p>inside</p>
        </TokenGate>
      </QueryClientProvider>,
    );
    expect(screen.queryByText("inside")).toBeNull();
    fireEvent.change(screen.getByLabelText("操作者權杖"), { target: { value: " secret " } });
    fireEvent.click(screen.getByRole("button", { name: "進入" }));
    expect(screen.getByText("inside")).toBeTruthy();
    expect(getToken()).toBe("secret");
    act(() => storeToken(null));
    expect(screen.queryByText("inside")).toBeNull();
  });
});
