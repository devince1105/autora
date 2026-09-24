"use client";

// Work that ended badly, and the one thing a person can do about it: start it again (AC-9).
//
// This sits in the inbox because it is the same job — the list of things waiting on a human.
// An approval waits for a decision; a failed run waits for somebody to say "try that again".
//
// **A restart is not a repair.** The old run keeps its history (what failed, and why, is the
// reason to keep it), and the new one starts from the top with everything the company already
// learned: the story, the evidence and the draft are still there. It costs the model calls
// again, which is why it is a person's decision and not an automatic retry.
//
// The company may still say no — the cap on new work per cycle applies to a person's restart
// as much as to the CEO's — so the result of the command is shown rather than assumed.
import { useState } from "react";

import type { components } from "@/api/schema.gen";

export type FailedRun = components["schemas"]["FailedRunOut"];
export type RestartResult = components["schemas"]["RestartOut"];

/** What to tell the operator about what the company decided. */
export function restartMessage(result: RestartResult): string {
  if (result.outcome === "done") return "已重新啟動";
  if (result.outcome === "awaiting_approval") return "等待有人核准";
  return `公司拒絕了：${result.reason ?? "沒有說原因"}`;
}

export function FailedRuns({
  runs,
  onRestart,
}: {
  runs: FailedRun[] | undefined;
  onRestart: (runId: string) => Promise<RestartResult>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [said, setSaid] = useState<Record<string, string>>({});

  if (!runs?.length) return null;
  return (
    <section aria-labelledby="failed-runs" className="mt-10">
      <h2 id="failed-runs" className="mb-3 text-lg font-semibold">
        失敗的流程
      </h2>
      <ul className="flex flex-col gap-3">
        {runs.map((run) => (
          <li
            key={run.id}
            data-testid={`failed-run-${run.id}`}
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface p-4"
          >
            <div className="min-w-0">
              <p className="font-medium">
                {typeof run.params?.title === "string"
                  ? run.params.title
                  : run.template_name}
              </p>
              <p className="text-sm text-muted">
                {run.failed_tasks.length
                  ? `失敗的步驟：${run.failed_tasks.join("、")}`
                  : "沒有單一步驟失敗，整條流程停在失敗狀態"}
              </p>
              <p className="mt-1 text-xs text-muted">
                {new Date(run.created_at).toLocaleString("zh-TW")}
                {run.restarted ? "・已經有人重新啟動過" : ""}
              </p>
            </div>
            <div className="flex items-center gap-3">
              {said[run.id] ? (
                <span
                  data-testid={`restart-said-${run.id}`}
                  className="text-sm text-muted"
                >
                  {said[run.id]}
                </span>
              ) : null}
              {run.superseded_by ? (
                <span
                  data-testid={`superseded-${run.id}`}
                  className="text-sm text-muted"
                >
                  同一份工作已經有新的執行，不需要重新啟動
                </span>
              ) : (
                <button
                  type="button"
                  data-testid={`restart-${run.id}`}
                  disabled={busy === run.id}
                  onClick={async () => {
                    setBusy(run.id);
                    try {
                      const result = await onRestart(run.id);
                      setSaid((now) => ({
                        ...now,
                        [run.id]: restartMessage(result),
                      }));
                    } catch (failure) {
                      setSaid((now) => ({
                        ...now,
                        [run.id]:
                          failure instanceof Error
                            ? failure.message
                            : String(failure),
                      }));
                    } finally {
                      setBusy(null);
                    }
                  }}
                  className="rounded-md border border-line px-3 py-1 text-sm text-accent disabled:opacity-50"
                >
                  {busy === run.id ? "重新啟動中…" : "重新啟動"}
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
