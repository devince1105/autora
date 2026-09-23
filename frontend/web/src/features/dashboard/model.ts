// What the dashboard shows, computed from real data only (T-309 AC: no hardcoded numbers):
// agents and tasks from the realtime store, money from the KPIs query, and the goal from the
// day the company planned (T-608 AC-12) — its words from the CEO's plan, its progress from
// what was actually counted. A company with no cycle yet falls back to the standing goal the
// KPIs carry, so the tile is never empty for the wrong reason. The revenue section (T-707) is the
// last 30 days of money and members, as the backend counted them from the payments.
import type { Schemas } from "@/api/client";
import { todaysGoal, type CycleLine } from "@/features/cycles/model";
import { effectiveState, isTaskVisible, type RealtimeState } from "@/realtime/reducer";
import type { ActivityState } from "@/realtime/snapshot";
import type { Connection } from "@/stores/realtime";

export interface KpisData {
  as_of: string;
  currency: string;
  cash: string;
  revenue_today: string;
  expenses_today: string;
  model_cost_today: string;
  /** Today's numbers from each domain, prefixed by the domain that defined them (T-603). */
  domain_metrics?: Record<string, unknown> | null;
  goal?: {
    title: string;
    metric: string;
    target: string;
    current: string | null;
    deadline: string | null;
  } | null;
  /** The last 30 days of money and members, and the memberships as they stand now (T-707). */
  revenue?: Schemas["RevenueView"] | null;
}

const BUSY: ReadonlySet<ActivityState> = new Set(["THINKING", "WORKING", "REVIEWING"]);
const UNFINISHED = new Set(["PENDING", "READY", "RUNNING", "WAITING_APPROVAL", "BLOCKED_BUDGET"]);

export interface DashboardModel {
  money: {
    currency: string;
    cash: number;
    revenueToday: number;
    expensesToday: number;
    modelCostToday: number;
  } | null;
  agents: { total: number; busy: number; waiting: number; paused: number; failed: number };
  tasks: { active: number; running: number; waitingApproval: number; blocked: number; doneRecently: number };
  publishedToday: number | null;
  goal: {
    title: string;
    current: number | null;
    target: number | null;
    deadline: string | null;
    /** "cycle": today's plan. "kpi": the standing goal, when there is no cycle yet. */
    source: "cycle" | "kpi";
  } | null;
  /** Which stage the company's current day is in (PLANNING…DONE), from the stream. */
  cycleStage: string | null;
  revenue: RevenueModel | null;
  connection: { status: Connection["status"]; staleSeconds: number | null };
}

/** Money and members over the last ``days`` days, as numbers the view can draw. */
export interface RevenueModel {
  currency: string;
  days: number;
  total: number;
  daily: { day: string; amount: number }[];
  payments: number;
  newMembers: number;
  renewals: number;
  lapsed: number;
  /** Holding access now. */
  members: number;
  /** Members now whose access runs out within 30 days: who a renewal reminder is for. */
  expiring: number;
  /** Null when nobody paid: an average of nothing is not zero. */
  averagePayment: number | null;
  /** What a membership costs today; null when nothing is for sale. */
  offer: { amount: number; currency: string; interval: string } | null;
}

export function revenueModel(kpis: KpisData | undefined): RevenueModel | null {
  const view = kpis?.revenue;
  if (!kpis || !view) return null;
  return {
    currency: kpis.currency,
    days: view.days,
    total: Number(view.total),
    daily: view.daily.map((d) => ({ day: d.day, amount: Number(d.amount) })),
    payments: view.payments,
    newMembers: view.new_members,
    renewals: view.renewals,
    lapsed: view.lapsed_members,
    members: view.members,
    expiring: view.expiring_members,
    averagePayment: view.average_payment === null || view.average_payment === undefined ? null : Number(view.average_payment),
    offer: view.offer ? { amount: Number(view.offer.amount), currency: view.offer.currency, interval: view.offer.interval } : null,
  };
}

function toCount(value: unknown): number | null {
  if (value === undefined || value === null) return null;
  const count = Number(value);
  return Number.isFinite(count) ? count : null;
}

export function dashboardModel(
  company: RealtimeState | null,
  kpis: KpisData | undefined,
  connection: Connection,
  now: Date,
  cycles: readonly CycleLine[] = [],
): DashboardModel {
  const agents = { total: 0, busy: 0, waiting: 0, paused: 0, failed: 0 };
  for (const agent of Object.values(company?.agents ?? {})) {
    if (!agent.activity) continue;
    agents.total += 1;
    const state = effectiveState(agent.activity, now);
    if (BUSY.has(state)) agents.busy += 1;
    else if (state === "WAITING") agents.waiting += 1;
    else if (state === "PAUSED") agents.paused += 1;
    else if (state === "FAILED") agents.failed += 1;
  }

  const tasks = { active: 0, running: 0, waitingApproval: 0, blocked: 0, doneRecently: 0 };
  for (const task of Object.values(company?.tasks ?? {})) {
    if (UNFINISHED.has(task.state)) {
      tasks.active += 1;
      if (task.state === "RUNNING") tasks.running += 1;
      if (task.state === "WAITING_APPROVAL") tasks.waitingApproval += 1;
      if (task.state === "BLOCKED_BUDGET") tasks.blocked += 1;
    } else if (task.state === "SUCCEEDED" && isTaskVisible(task, now)) {
      tasks.doneRecently += 1;
    }
  }

  return {
    money: kpis
      ? {
          currency: kpis.currency,
          cash: Number(kpis.cash),
          revenueToday: Number(kpis.revenue_today),
          expensesToday: Number(kpis.expenses_today),
          modelCostToday: Number(kpis.model_cost_today),
        }
      : null,
    agents,
    tasks,
    // the company layer stores numbers without knowing what they mean; this tile is the
    // newsroom's, so naming the newsroom belongs here rather than in the backend
    publishedToday: toCount(kpis?.domain_metrics?.["newsroom.published_articles"]),
    goal: goalModel(kpis, cycles),
    cycleStage: company?.cycle?.stage ?? null,
    connection: connectionModel(connection, company !== null, now),
    revenue: revenueModel(kpis),
  };
}

/**
 * The goal tile: today's plan when the company has a day, the standing goal otherwise.
 *
 * The plan is preferred even when its progress is unknown — a target nobody has measured yet
 * is still what the company said it was doing today.
 */
function goalModel(kpis: KpisData | undefined, cycles: readonly CycleLine[]): DashboardModel["goal"] {
  const planned = todaysGoal(cycles);
  if (planned) {
    return {
      title: planned.label,
      current: planned.current,
      target: planned.target,
      deadline: null,
      source: "cycle",
    };
  }
  if (!kpis?.goal) return null;
  return {
    title: kpis.goal.title,
    current: kpis.goal.current === null ? null : Number(kpis.goal.current),
    target: Number(kpis.goal.target),
    deadline: kpis.goal.deadline,
    source: "kpi",
  };
}

/** Connection badge data; stale seconds only when not live and there is data to be stale. */
export function connectionModel(connection: Connection, hasData: boolean, now: Date): DashboardModel["connection"] {
  const staleSeconds =
    connection.status !== "live" && hasData && connection.lastEventAt !== null
      ? Math.max(0, Math.round((now.getTime() - connection.serverOffsetMs - connection.lastEventAt) / 1000))
      : null;
  return { status: connection.status, staleSeconds };
}

export function formatMoney(amount: number, currency: string): string {
  return new Intl.NumberFormat("zh-TW", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: Math.abs(amount) < 1 && amount !== 0 ? 4 : 2,
  }).format(amount);
}
