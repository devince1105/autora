// @vitest-environment jsdom
// The company's days as a page (T-608): what the plan said, what was counted, and — on the
// days nobody planned — that nobody planned them.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CycleDetailView } from "./CycleDetailView";
import { CyclesView } from "./CyclesView";
import { cycleCost, goalProgress, todaysGoal, type CycleDetail, type CycleLine } from "./model";

afterEach(cleanup);

const line = (over: Partial<CycleLine> = {}): CycleLine => ({
  id: "01a0b900-0000-7000-8000-00000000000" + (over.seq ?? 1),
  seq: 1,
  stage: "DONE",
  started_at: "2026-09-19T00:00:00Z",
  ended_at: "2026-09-19T23:00:00Z",
  stage_deadline: null,
  planned_by: "ceo",
  goals: [{ metric: "published_articles", title: "發布 5 篇雙語文章", target: 5, current: 5 }],
  review: "產量達標，成本偏高。",
  review_missing: null,
  workflows: 4,
  failed_tasks: 0,
  cost_usd: "1.250000",
  ...over,
});

describe("what a day is worth reading", () => {
  it("the target comes from the plan, the progress from the count (AC-12)", () => {
    const goal = goalProgress({ metric: "published_articles", title: "發布 5 篇", target: 5, current: 3 });
    expect(goal).toEqual({ label: "發布 5 篇", target: 5, current: 3, reached: false });
    expect(goalProgress({ metric: "x", target: 2, current: 2 }).reached).toBe(true);
    // a metric with no words of its own is named by the metric
    expect(goalProgress({ metric: "revenue_usd", target: null, current: null }).label).toBe("revenue_usd");
  });

  it("today's goal is the open day's, not the last finished one's", () => {
    const yesterday = line({ seq: 1, stage: "DONE" });
    const today = line({
      seq: 2,
      stage: "EXECUTING",
      goals: [{ metric: "published_articles", title: "發布 3 篇", target: 3, current: 1 }],
    });
    expect(todaysGoal([today, yesterday])).toMatchObject({ label: "發布 3 篇", current: 1, target: 3 });
    // every day closed: the newest one is still what to show
    expect(todaysGoal([yesterday])).toMatchObject({ target: 5 });
    expect(todaysGoal([])).toBeNull();
    expect(todaysGoal([line({ goals: [] })])).toBeNull();
  });

  it("a cost that is not a number is no cost at all", () => {
    expect(cycleCost(line())).toBeCloseTo(1.25);
    expect(cycleCost(line({ cost_usd: null }))).toBeNull();
    expect(cycleCost(line({ cost_usd: "nonsense" }))).toBeNull();
  });
});

describe("the list of days", () => {
  it("shows each day's stage, goal, work and review", () => {
    render(<CyclesView cycles={[line({ seq: 2, stage: "EXECUTING" }), line({ seq: 1 })]} />);
    const today = screen.getByTestId("cycle-2");
    expect(within(today).getByText("第 2 輪")).toBeTruthy();
    expect(within(today).getByText("執行")).toBeTruthy();
    expect(within(today).getByTestId("cycle-goal").textContent).toContain("5 / 5");
    expect(within(today).getByText("4 條流程")).toBeTruthy();
    expect(within(today).getByText("產量達標，成本偏高。")).toBeTruthy();
    expect(screen.getByTestId("cycle-1")).toBeTruthy();
  });

  it("a day nobody planned says so, and so does a day nobody reviewed (T-607)", () => {
    render(
      <CyclesView
        cycles={[
          line({
            seq: 3,
            planned_by: "fallback",
            review: null,
            review_missing: "the CEO's review failed: TimeoutError",
            failed_tasks: 2,
          }),
        ]}
      />,
    );
    expect(screen.getByTestId("cycle-planned-by").textContent).toContain("無人規劃");
    expect(screen.getByTestId("cycle-review-missing").textContent).toContain("TimeoutError");
    expect(screen.getByText("2 個任務失敗")).toBeTruthy();
  });

  it("a company that has never run a day is told so, not shown an empty list", () => {
    render(<CyclesView cycles={[]} />);
    expect(screen.getByTestId("cycles-empty")).toBeTruthy();
  });
});

describe("one day in full", () => {
  const detail = (over: Partial<CycleDetail> = {}): CycleDetail => ({
    ...line(),
    plan: { by: "ceo", goals: [{ metric: "published_articles", target: 5 }] },
    review_detail: { summary: "產量達標，成本偏高。", projects: [] },
    kpis: { cost_usd: "1.250000", "newsroom.published_articles": 5 },
    summary: "The CEO aimed at published_articles 5.\nIt cost $1.25 across 12 model call(s).",
    timeline: [],
    ...over,
  });

  it("shows the summary, the goals and the review", () => {
    render(<CycleDetailView cycle={detail()} />);
    expect(screen.getByTestId("cycle-summary").textContent).toContain("across 12 model call(s)");
    expect(screen.getByTestId("cycle-goal").textContent).toContain("5 / 5");
    expect(screen.getByText("4 條流程")).toBeTruthy();
  });

  it("a day with no plan and no review shows both holes", () => {
    render(
      <CycleDetailView
        cycle={detail({
          goals: [],
          planned_by: "fallback",
          review: null,
          review_missing: "the CEO's review failed: TimeoutError",
        })}
      />,
    );
    expect(screen.getByText("這一輪沒有設定目標。")).toBeTruthy();
    expect(screen.getByTestId("cycle-review-missing").textContent).toContain("TimeoutError");
  });
});
