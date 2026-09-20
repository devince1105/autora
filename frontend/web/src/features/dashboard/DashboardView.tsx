import Link from "next/link";
import type { ReactNode } from "react";

import { withCompany } from "@/features/company/CompanyScope";

import { formatMoney, type DashboardModel } from "./model";

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
            href={withCompany("/approvals", companyId)}
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
          <Link href={withCompany("/office", companyId)} className="text-sm text-accent underline">
            辦公室
          </Link>
          <Link href={withCompany("/newsroom/articles", companyId)} className="text-sm text-accent underline">
            新聞室
          </Link>
          <Link href={withCompany("/agents", companyId)} className="text-sm text-accent underline">
            代理
          </Link>
          <Link href={withCompany("/timeline", companyId)} className="text-sm text-accent underline">
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
          detail={
            goal
              ? `進度 ${goal.current ?? 0} / ${goal.target}${
                  goal.deadline ? `・期限 ${new Date(goal.deadline).toLocaleString("zh-TW")}` : ""
                }`
              : null
          }
        />
      </div>
    </main>
  );
}
