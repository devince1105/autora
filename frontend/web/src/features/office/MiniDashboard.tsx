// The office's dashboard strip (T-411): the Dashboard's numbers in one line over the office —
// the same model (dashboardModel) from the same sources, so the two pages never disagree.
import type { DashboardModel } from "@/features/dashboard/model";
import { formatMoney } from "@/features/dashboard/model";

function Item({ label, value, testId }: { label: string; value: string; testId: string }) {
  return (
    <span className="flex items-baseline gap-1.5 whitespace-nowrap" data-testid={testId}>
      <span className="text-xs text-muted">{label}</span>
      <span className="font-semibold tabular-nums">{value}</span>
    </span>
  );
}

export function MiniDashboardView({ model, pendingApprovals }: { model: DashboardModel; pendingApprovals: number | null }) {
  const { money, agents, tasks } = model;
  const cash = money ? formatMoney(money.cash, money.currency) : "—";
  return (
    <div
      aria-label="公司概況"
      className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b border-line bg-surface/80 px-4 py-2 text-sm"
    >
      <Item testId="mini-cash" label="現金" value={cash} />
      <Item testId="mini-revenue" label="今日營收" value={money ? formatMoney(money.revenueToday, money.currency) : "—"} />
      <Item testId="mini-expenses" label="今日支出" value={money ? formatMoney(money.expensesToday, money.currency) : "—"} />
      <Item testId="mini-agents" label="工作中代理" value={`${agents.busy} / ${agents.total}`} />
      <Item testId="mini-tasks" label="進行中任務" value={String(tasks.active)} />
      <Item testId="mini-approvals" label="待審批" value={pendingApprovals === null ? "—" : String(pendingApprovals)} />
    </div>
  );
}
