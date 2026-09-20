import {
  goalProgress,
  PLANNED_BY_LABEL,
  STAGE_LABEL,
  type CycleDetail,
} from "./model";

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 border-b border-line py-3 last:border-0">
      <span className="text-xs text-muted">{label}</span>
      <div className="text-sm">{children}</div>
    </div>
  );
}

export function CycleDetailView({ cycle }: { cycle: CycleDetail }) {
  return (
    <article className="flex flex-col gap-6">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-2xl font-semibold">第 {cycle.seq} 輪</h1>
        <span className="rounded-full border border-line px-2 py-0.5 text-xs text-muted">
          {STAGE_LABEL[cycle.stage] ?? cycle.stage}
        </span>
        {cycle.planned_by ? (
          <span
            className={
              cycle.planned_by === "fallback"
                ? "text-warn text-sm"
                : "text-muted text-sm"
            }
          >
            {PLANNED_BY_LABEL[cycle.planned_by] ?? cycle.planned_by}
          </span>
        ) : null}
      </header>

      {cycle.summary ? (
        <section
          data-testid="cycle-summary"
          className="rounded-lg border border-line bg-surface p-4"
        >
          <h2 className="mb-2 text-sm text-muted">當天摘要</h2>
          <p className="whitespace-pre-line text-sm">{cycle.summary}</p>
        </section>
      ) : null}

      <section className="rounded-lg border border-line bg-surface px-4">
        <Row label="目標">
          {cycle.goals.length === 0 ? (
            <span className="text-muted">這一輪沒有設定目標。</span>
          ) : (
            <ul className="flex flex-col gap-1">
              {cycle.goals.map((raw) => {
                const goal = goalProgress(raw);
                return (
                  <li
                    key={raw.metric}
                    data-testid="cycle-goal"
                    className="tabular-nums"
                  >
                    {goal.label}　{goal.current ?? 0} / {goal.target ?? "—"}
                    {goal.reached ? <span className="text-ok"> ✓</span> : null}
                  </li>
                );
              })}
            </ul>
          )}
        </Row>
        <Row label="工作">
          <span className="tabular-nums">{cycle.workflows} 條流程</span>
          {cycle.failed_tasks > 0 ? (
            <span className="text-danger">
              {" "}
              ・{cycle.failed_tasks} 個任務失敗
            </span>
          ) : null}
        </Row>
        <Row label="覆盤">
          {cycle.review_missing ? (
            <span className="text-warn" data-testid="cycle-review-missing">
              沒有覆盤：{cycle.review_missing}
            </span>
          ) : (
            (cycle.review ?? <span className="text-muted">尚未覆盤。</span>)
          )}
        </Row>
      </section>

      <section>
        <h2 className="mb-2 text-sm text-muted">事件</h2>
        <ol aria-label="事件" className="flex flex-col gap-1 text-sm">
          {cycle.timeline.map((event) => (
            <li
              key={String(event.event_id)}
              className="flex gap-3 tabular-nums"
            >
              <span className="text-muted">
                {String(event.occurred_at).slice(11, 19)}
              </span>
              <span>{String(event.event_type)}</span>
            </li>
          ))}
        </ol>
      </section>
    </article>
  );
}
