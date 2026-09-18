import type { ReactNode } from "react";

import styles from "./dashboard.module.css";
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

export function ConnectionBadge({ connection }: { connection: DashboardModel["connection"] }) {
  const stale = connection.staleSeconds;
  return (
    <span className={styles.connection} data-status={connection.status} role="status">
      <span className={styles.dot} aria-hidden />
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
    <section className={styles.tile} data-testid={testId} aria-label={label}>
      <h2 className={styles.label}>{label}</h2>
      <p className={styles.value}>{value}</p>
      {detail ? <p className={styles.detail}>{detail}</p> : null}
    </section>
  );
}

const PENDING = <span className={styles.muted}>—</span>;

export function DashboardView({ companyName, model }: { companyName: string; model: DashboardModel }) {
  const { money, agents, tasks, goal } = model;
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Dashboard</p>
          <h1 className={styles.title}>{companyName}</h1>
        </div>
        <ConnectionBadge connection={model.connection} />
      </header>

      {model.connection.status === "offline" ? (
        <p className={styles.banner} role="alert">
          與伺服器的連線中斷，正在重試。畫面保留最後的狀態。
        </p>
      ) : null}

      <div className={styles.grid}>
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
              <span className={styles.of}> / {agents.total}</span>
            </>
          }
          detail={[
            agents.waiting ? `等待 ${agents.waiting}` : null,
            agents.paused ? `暫停 ${agents.paused}` : null,
            agents.failed ? `失敗 ${agents.failed}` : null,
          ]
            .filter(Boolean)
            .join("・") || "其餘閒置"}
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
          detail={model.publishedToday === null ? "文章功能於階段 5 上線" : null}
        />
        <Tile
          testId="goal"
          label="今日目標"
          value={goal ? goal.title : <span className={styles.muted}>尚未設定</span>}
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
