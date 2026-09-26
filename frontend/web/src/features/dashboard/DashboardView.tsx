import Link from "next/link";
import type { ReactNode } from "react";

import { withCompany } from "@/features/company/CompanyScope";
import { STAGE_LABEL } from "@/features/cycles/model";

import { formatMoney, type DashboardModel, type RevenueModel } from "./model";

const CONNECTION_LABEL: Record<DashboardModel["connection"]["status"], string> = {
  idle: "未連線",
  connecting: "連線中",
  live: "即時",
  reconnecting: "重新連線中",
  offline: "離線",
  unauthorized: "權杖無效",
  not_found: "找不到公司",
};

const DOT: Record<DashboardModel["connection"]["status"], string> = {
  idle: "bg-neutral",
  connecting: "bg-warn",
  live: "bg-ok",
  reconnecting: "bg-warn",
  offline: "bg-danger",
  unauthorized: "bg-danger",
  not_found: "bg-danger",
};

export function ConnectionBadge({ connection }: { connection: DashboardModel["connection"] }) {
  const stale = connection.staleSeconds;
  return (
    <span
      role="status"
      data-status={connection.status}
      className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-sm text-muted"
    >
      <span aria-hidden className={`size-2 rounded-full ${DOT[connection.status]}`} />
      {CONNECTION_LABEL[connection.status]}
      {stale !== null && connection.status !== "live" ? `・資料可能已過期 ${stale} 秒` : null}
    </span>
  );
}

/** The progress line under 今日目標: the plan's numbers, and which stage the day is in. */
function goalDetail(goal: NonNullable<DashboardModel["goal"]>, stage: string | null): string {
  const progress = `進度 ${goal.current ?? 0} / ${goal.target ?? "—"}`;
  if (goal.source === "cycle") {
    return progress + (stage ? `・${STAGE_LABEL[stage] ?? stage}` : "");
  }
  return progress + (goal.deadline ? `・期限 ${new Date(goal.deadline).toLocaleString("zh-TW")}` : "");
}

function Tile({
  label,
  value,
  detail,
  testId,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  testId: string;
}) {
  return (
    <section
      data-testid={testId}
      aria-label={label}
      className="min-h-30 rounded-xl border border-line bg-surface px-5 py-4"
    >
      <h2 className="text-sm font-medium text-muted">{label}</h2>
      <p className="mt-2 text-3xl leading-tight font-semibold tabular-nums break-words">{value}</p>
      {detail ? <p className="mt-2 text-xs text-muted">{detail}</p> : null}
    </section>
  );
}

const PENDING = <span className="font-normal text-muted">—</span>;

const INTERVAL: Record<string, string> = { year: "年", month: "月" };

/**
 * Revenue per day, one bar each, oldest on the left. Plain SVG — a chart library for thirty bars
 * would be most of the page. A day with no money still gets a one-pixel stub, so a gap reads as
 * "nothing that day" and not as missing data; each bar says its day and amount on hover.
 */
function RevenueBars({ daily, currency }: { daily: RevenueModel["daily"]; currency: string }) {
  const top = Math.max(...daily.map((d) => d.amount));
  const best = daily.reduce((a, b) => (b.amount > a.amount ? b : a), daily[0]);
  const total = daily.reduce((sum, d) => sum + d.amount, 0);
  const step = 10;
  const height = 56;
  return (
    <figure className="mt-4 rounded-xl border border-line bg-surface px-5 py-4" data-testid="revenue-chart">
      <svg
        role="img"
        aria-label={`近 ${daily.length} 天每日營收，合計 ${formatMoney(total, currency)}，最高 ${best.day} ${formatMoney(best.amount, currency)}`}
        viewBox={`0 0 ${daily.length * step} ${height + 2}`}
        preserveAspectRatio="none"
        className="h-24 w-full"
      >
        {daily.map((d, i) => {
          const h = top > 0 ? Math.max(1, Math.round((d.amount / top) * height)) : 1;
          return (
            <rect
              key={d.day}
              data-testid="revenue-bar"
              data-amount={d.amount}
              x={i * step + 1}
              y={height - h}
              width={step - 2}
              height={h}
              className={d.amount > 0 ? "fill-accent" : "fill-line"}
            >
              <title>{`${d.day}：${formatMoney(d.amount, currency)}`}</title>
            </rect>
          );
        })}
      </svg>
      <figcaption className="mt-1 flex justify-between text-xs text-muted">
        <span>{daily[0].day}</span>
        <span>{daily[daily.length - 1].day}</span>
      </figcaption>
    </figure>
  );
}

/** Money and members over the last 30 days (T-707): what the memberships add up to. */
function RevenueSection({ revenue }: { revenue: RevenueModel | null }) {
  const days = revenue?.days ?? 30;
  const money = (amount: number) => (revenue ? formatMoney(amount, revenue.currency) : "");
  return (
    <section aria-labelledby="revenue-heading" data-testid="revenue-section" className="mt-8">
      <h2 id="revenue-heading" className="mb-3 text-lg font-semibold">
        營收<span className="ml-2 text-sm font-normal text-muted">近 {days} 天</span>
      </h2>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-4">
        <Tile
          testId="revenue-total"
          label="營收"
          value={revenue ? money(revenue.total) : PENDING}
          detail={
            revenue
              ? revenue.payments
                ? `${revenue.payments} 筆付款・平均 ${money(revenue.averagePayment ?? 0)}`
                : "這段時間沒有付款"
              : null
          }
        />
        <Tile
          testId="members"
          label="會員"
          value={revenue ? revenue.members : PENDING}
          detail={revenue ? (revenue.expiring ? `30 天內到期 ${revenue.expiring} 位` : "近期沒有人到期") : null}
        />
        <Tile
          testId="new-members"
          label="新會員"
          value={revenue ? revenue.newMembers : PENDING}
          detail={revenue ? `續約 ${revenue.renewals}` : null}
        />
        <Tile
          testId="lapsed"
          label="流失"
          value={revenue ? revenue.lapsed : PENDING}
          detail={revenue ? "到期後沒有續約" : null}
        />
        <Tile
          testId="offer"
          label="目前售價"
          value={
            revenue?.offer ? (
              <>
                {formatMoney(revenue.offer.amount, revenue.offer.currency)}
                <span className="text-lg font-medium text-muted">／{INTERVAL[revenue.offer.interval] ?? revenue.offer.interval}</span>
              </>
            ) : revenue ? (
              <span className="font-normal text-muted">尚未開賣</span>
            ) : (
              PENDING
            )
          }
        />
      </div>
      {revenue && revenue.total > 0 ? (
        <RevenueBars daily={revenue.daily} currency={revenue.currency} />
      ) : revenue ? (
        <p className="mt-4 rounded-xl border border-line bg-surface px-5 py-4 text-sm text-muted" data-testid="revenue-chart-empty">
          近 {days} 天沒有收入。
        </p>
      ) : null}
    </section>
  );
}

export function DashboardView({
  companyId,
  companyName,
  model,
  pendingApprovals = null,
}: {
  companyId: string;
  companyName: string;
  model: DashboardModel;
  /** From GET /api/approvals; null while unknown. */
  pendingApprovals?: number | null;
}) {
  const { money, agents, tasks, goal } = model;
  return (
    <main className="mx-auto max-w-6xl px-4 pt-8 pb-12">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs tracking-widest text-muted uppercase">Dashboard</p>
          <h1 className="mt-1 text-2xl font-semibold">{companyName}</h1>
        </div>
        <div className="flex items-center gap-4">
          <Link
            href={withCompany("/admin/approvals", companyId)}
            className="text-sm text-accent underline"
            data-testid="pending-approvals"
          >
            審批收件匣
            {pendingApprovals ? (
              <span className="ml-1 rounded-full bg-warn px-1.5 text-xs font-medium text-canvas no-underline">
                {pendingApprovals}
              </span>
            ) : null}
          </Link>
          <Link href={withCompany("/admin/office", companyId)} className="text-sm text-accent underline">
            辦公室
          </Link>
          <Link href={withCompany("/admin/newsroom/articles", companyId)} className="text-sm text-accent underline">
            新聞室
          </Link>
          <Link href={withCompany("/admin/agents", companyId)} className="text-sm text-accent underline">
            代理
          </Link>
          <Link href={withCompany("/admin/cycles", companyId)} className="text-sm text-accent underline">
            每日週期
          </Link>
          <Link href={withCompany("/admin/timeline", companyId)} className="text-sm text-accent underline">
            事件時間軸
          </Link>
          <ConnectionBadge connection={model.connection} />
        </div>
      </header>

      {model.connection.status === "offline" ? (
        <p
          role="alert"
          className="mb-5 rounded-lg border border-danger-line bg-danger-soft px-4 py-3"
        >
          與伺服器的連線中斷，正在重試。畫面保留最後的狀態。
        </p>
      ) : null}

      <div className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-4">
        <Tile
          testId="cash"
          label="現金"
          value={money ? formatMoney(money.cash, money.currency) : PENDING}
        />
        <Tile
          testId="revenue"
          label="今日營收"
          value={money ? formatMoney(money.revenueToday, money.currency) : PENDING}
        />
        <Tile
          testId="expenses"
          label="今日支出"
          value={money ? formatMoney(money.expensesToday, money.currency) : PENDING}
          detail={money ? `其中模型費用 ${formatMoney(money.modelCostToday, money.currency)}` : null}
        />
        <Tile
          testId="agents"
          label="工作中代理"
          value={
            <>
              {agents.busy}
              <span className="text-lg font-medium text-muted"> / {agents.total}</span>
            </>
          }
          detail={
            [
              agents.waiting ? `等待 ${agents.waiting}` : null,
              agents.paused ? `暫停 ${agents.paused}` : null,
              agents.failed ? `失敗 ${agents.failed}` : null,
            ]
              .filter(Boolean)
              .join("・") || "其餘閒置"
          }
        />
        <Tile
          testId="tasks"
          label="進行中任務"
          value={tasks.active}
          detail={[
            `執行中 ${tasks.running}`,
            tasks.waitingApproval ? `待審批 ${tasks.waitingApproval}` : null,
            tasks.blocked ? `預算受阻 ${tasks.blocked}` : null,
            tasks.doneRecently ? `剛完成 ${tasks.doneRecently}` : null,
          ]
            .filter(Boolean)
            .join("・")}
        />
        <Tile
          testId="published"
          label="今日發布"
          value={model.publishedToday ?? PENDING}
          detail={model.publishedToday === null ? "這間公司沒有新聞室" : null}
        />
        <Tile
          testId="goal"
          label="今日目標"
          value={goal ? goal.title : <span className="font-normal text-muted">尚未設定</span>}
          detail={goal ? goalDetail(goal, model.cycleStage) : null}
        />
      </div>

      <RevenueSection revenue={model.revenue} />
    </main>
  );
}
