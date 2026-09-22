import Link from "next/link";

import {
  cycleCost,
  goalProgress,
  PLANNED_BY_LABEL,
  STAGE_LABEL,
  type CycleLine,
} from "./model";
import { formatMoney } from "@/features/dashboard/model";

function Goal({ goal }: { goal: ReturnType<typeof goalProgress> }) {
  const progress =
    goal.target === null ? "—" : `${goal.current ?? 0} / ${goal.target}`;
  return (
    <span
      data-testid="cycle-goal"
      className={`tabular-nums ${goal.reached ? "text-ok" : "text-fg"}`}
      title={goal.label}
    >
      {goal.label}　{progress}
    </span>
  );
}

export function CyclesView({ cycles }: { cycles: readonly CycleLine[] }) {
  if (cycles.length === 0) {
    return (
      <p className="text-muted" data-testid="cycles-empty">
        這間公司還沒有跑過任何一輪。
      </p>
    );
  }
  return (
    <ul aria-label="營運週期" className="flex flex-col gap-3">
      {cycles.map((cycle) => {
        const goal = cycle.goals?.[0] ? goalProgress(cycle.goals[0]) : null;
        const cost = cycleCost(cycle);
        return (
          <li key={cycle.id} data-testid={`cycle-${cycle.seq}`}>
            <Link
              href={`/cycles/${cycle.id}`}
              className="flex flex-col gap-1 rounded-lg border border-line bg-surface p-4 hover:border-fg/30"
            >
              <span className="flex flex-wrap items-baseline gap-3">
                <span className="text-lg font-semibold">第 {cycle.seq} 輪</span>
                <span className="rounded-full border border-line px-2 py-0.5 text-xs text-muted">
                  {STAGE_LABEL[cycle.stage] ?? cycle.stage}
                </span>
                {cycle.planned_by ? (
                  <span
                    data-testid="cycle-planned-by"
                    className={
                      cycle.planned_by === "fallback"
                        ? "text-warn text-xs"
                        : "text-muted text-xs"
                    }
                  >
                    {PLANNED_BY_LABEL[cycle.planned_by] ?? cycle.planned_by}
                  </span>
                ) : null}
              </span>
              {goal ? <Goal goal={goal} /> : null}
              <span className="flex flex-wrap gap-4 text-sm text-muted tabular-nums">
                <span>{cycle.workflows} 條流程</span>
                {cycle.failed_tasks > 0 ? (
                  <span className="text-danger">
                    {cycle.failed_tasks} 個任務失敗
                  </span>
                ) : null}
                {cost !== null ? <span>{formatMoney(cost, cycle.currency)}</span> : null}
              </span>
              {cycle.review_missing ? (
                <span
                  className="text-warn text-sm"
                  data-testid="cycle-review-missing"
                >
                  沒有覆盤：{cycle.review_missing}
                </span>
              ) : cycle.review ? (
                <span className="text-sm text-muted">{cycle.review}</span>
              ) : null}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
