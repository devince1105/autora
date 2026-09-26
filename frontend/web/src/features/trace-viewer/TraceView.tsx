import Link from "next/link";
import { useState } from "react";

import type { Schemas } from "@/api/client";
import { TONE_DOT } from "@/events/describe";

import type { Row, Trace, TraceSummary } from "./model";

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString("zh-TW", { hour12: false, fractionalSecondDigits: 3 } as Intl.DateTimeFormatOptions);
}

function Raw({ value }: { value: unknown }) {
  return (
    <pre className="mt-2 overflow-x-auto rounded-lg border border-line bg-canvas p-3 text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function StepDetail({
  row,
  loadBlob,
}: {
  row: Row;
  loadBlob: (seq: number) => Promise<unknown>;
}) {
  const [blob, setBlob] = useState<unknown>(undefined);
  const [error, setError] = useState<string | null>(null);
  const step = row.step;
  if (!step) return null;
  return (
    <div className="mt-2 rounded-lg border border-line p-3 text-xs" data-testid={`step-${step.seq}`}>
      <p className="flex flex-wrap justify-between gap-2 text-muted">
        <span>
          步驟 #{step.seq}・{step.kind}
        </span>
        <span className="tabular-nums">US${Number(step.cost_usd).toFixed(4)}</span>
      </p>
      {step.summary ? <p className="mt-1 text-sm break-words text-ink">{step.summary}</p> : null}
      {step.tool_calls?.length ? <Raw value={step.tool_calls} /> : null}
      {step.has_blob ? (
        blob === undefined ? (
          <button
            type="button"
            className="mt-2 text-accent underline"
            onClick={() =>
              loadBlob(step.seq)
                .then(setBlob)
                .catch((e: Error) => setError(e.message))
            }
          >
            顯示完整提示與回應
          </button>
        ) : (
          <Raw value={blob} />
        )
      ) : null}
      {error ? <p className="mt-2 text-danger">{error}</p> : null}
    </div>
  );
}

function TraceRow({ row, loadBlob }: { row: Row; loadBlob: (seq: number) => Promise<unknown> }) {
  const [open, setOpen] = useState(!row.known);
  return (
    <li className="relative pl-6" data-testid={`row-${row.key}`}>
      <span aria-hidden className={`absolute top-2 left-0 size-2.5 rounded-full ${TONE_DOT[row.tone]}`} />
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-xs text-muted tabular-nums">{time(row.at)}</span>
        {row.seq !== null ? <span className="text-xs text-muted tabular-nums">#{row.seq}</span> : null}
        <span className="font-medium">{row.label}</span>
        {!row.known ? (
          <span className="rounded bg-warn/15 px-1.5 text-xs text-warn">未知類型</span>
        ) : null}
        {row.summary ? <span className="min-w-0 break-words text-sm text-muted">{row.summary}</span> : null}
        {row.payload && row.known ? (
          <button type="button" className="text-xs text-accent" onClick={() => setOpen(!open)}>
            {open ? "收起" : "原始資料"}
          </button>
        ) : null}
      </div>
      {open && row.payload ? <Raw value={row.payload} /> : null}
      {row.step ? <StepDetail row={row} loadBlob={loadBlob} /> : null}
    </li>
  );
}

export function TraceView({
  trace,
  rows,
  summary,
  taskName,
  loadBlob,
}: {
  trace: Trace;
  rows: Row[];
  summary: TraceSummary;
  taskName: string | null;
  loadBlob: (seq: number) => Promise<unknown>;
}) {
  return (
    <main className="mx-auto max-w-4xl px-4 pt-8 pb-12">
      <p className="text-xs tracking-widest text-muted uppercase">Trace</p>
      <h1 className="mt-1 text-2xl font-semibold break-words">{taskName ?? trace.task_id}</h1>
      <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted">
        <span>第 {trace.attempt} 次嘗試</span>
        <span>狀態 {trace.state}</span>
        <span className="tabular-nums">US${Number(trace.cost_usd).toFixed(4)}</span>
        <span>
          {summary.events} 個事件・{summary.steps} 個步驟・{summary.toolCalls} 次工具呼叫
        </span>
        <Link className="text-accent underline" href={`/admin/tasks/${trace.task_id}`}>
          任務與其他嘗試
        </Link>
      </p>
      {summary.unknownTypes.length ? (
        <p className="mt-3 text-sm text-warn">
          有 {summary.unknownTypes.length} 種這個畫面不認得的事件（{summary.unknownTypes.join("、")}），以原始資料顯示。
        </p>
      ) : null}
      {rows.length ? (
        <ol className="mt-6 grid gap-4 border-l border-line pl-4" aria-label="軌跡">
          {rows.map((row) => (
            <TraceRow key={row.key} row={row} loadBlob={loadBlob} />
          ))}
        </ol>
      ) : (
        <p className="mt-6 text-muted">這次執行還沒有任何事件。</p>
      )}
    </main>
  );
}

type TaskOut = Schemas["TaskDetailOut"];

export function TaskView({ task }: { task: TaskOut }) {
  return (
    <main className="mx-auto max-w-4xl px-4 pt-8 pb-12">
      <p className="text-xs tracking-widest text-muted uppercase">Task</p>
      <h1 className="mt-1 text-2xl font-semibold break-words">{task.display_name}</h1>
      <p className="mt-2 flex flex-wrap gap-x-4 text-sm text-muted">
        <span>{task.name}</span>
        <span>角色 {task.required_role}</span>
        <span>狀態 {task.state}</span>
        <span>
          嘗試 {task.attempt} / {task.max_attempts}
        </span>
      </p>
      <h2 className="mt-6 mb-2 font-semibold">每一次嘗試</h2>
      {task.runs.length ? (
        <ul className="grid gap-2" aria-label="嘗試">
          {task.runs.map((run) => (
            <li key={run.id}>
              <Link
                href={`/admin/trace/${run.id}`}
                className="flex flex-wrap justify-between gap-2 rounded-lg border border-line bg-surface px-4 py-3 hover:border-accent"
              >
                <span>
                  第 {run.attempt} 次・{run.state}
                </span>
                <span className="text-sm text-muted tabular-nums">US${Number(run.cost_usd).toFixed(4)}</span>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted">還沒有執行過。</p>
      )}
      <h2 className="mt-6 mb-2 font-semibold">輸入</h2>
      <Raw value={task.input} />
      {task.output ? (
        <>
          <h2 className="mt-6 mb-2 font-semibold">輸出</h2>
          <Raw value={task.output} />
        </>
      ) : null}
    </main>
  );
}
