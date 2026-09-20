// What the dashboard shows, computed from real data only (T-309 AC: no hardcoded numbers):
// agents and tasks from the realtime store, money and the goal from the KPIs query.
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
  goal: { title: string; current: number | null; target: number; deadline: string | null } | null;
  connection: { status: Connection["status"]; staleSeconds: number | null };
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
    goal: kpis?.goal
      ? {
          title: kpis.goal.title,
          current: kpis.goal.current === null ? null : Number(kpis.goal.current),
          target: Number(kpis.goal.target),
          deadline: kpis.goal.deadline,
        }
      : null,
    connection: connectionModel(connection, company !== null, now),
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
