import type { components } from "@/api/schema.gen";

export type CycleLine = components["schemas"]["CycleLine"];
export type CycleDetail = components["schemas"]["CycleDetail"];
export type CycleGoal = components["schemas"]["CycleGoal"];

export const STAGE_LABEL: Record<string, string> = {
  PLANNING: "規劃",
  EXECUTING: "執行",
  MEASURING: "量測",
  REVIEWING: "覆盤",
  DONE: "結束",
};

/** Who decided the day. A day nobody planned is not the same as a day planned to do nothing. */
export const PLANNED_BY_LABEL: Record<string, string> = {
  ceo: "執行長規劃",
  fallback: "無人規劃（沿用）",
};

export interface GoalProgress {
  /** The words come from the plan; the numbers come from what was counted (AC-12). */
  label: string;
  target: number | null;
  current: number | null;
  reached: boolean;
}

export function goalProgress(goal: CycleGoal): GoalProgress {
  const target = goal.target ?? null;
  const current = goal.current ?? null;
  return {
    label: goal.title ?? goal.metric,
    target,
    current,
    reached: target !== null && current !== null && current >= target,
  };
}

/** The goal a person should see on the dashboard: the current cycle's first one. */
export function todaysGoal(cycles: readonly CycleLine[]): GoalProgress | null {
  const today = cycles.find((cycle) => cycle.stage !== "DONE") ?? cycles[0];
  const goal = today?.goals?.[0];
  return goal ? goalProgress(goal) : null;
}

export function cycleCost(cycle: CycleLine): number | null {
  if (cycle.cost_usd === null || cycle.cost_usd === undefined) return null;
  const cost = Number(cycle.cost_usd);
  return Number.isFinite(cost) ? cost : null;
}
