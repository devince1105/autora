"use client";

import Link from "next/link";
import { useState } from "react";

import { ApiError } from "@/api/client";

import { STATES, type ApprovalCard, type ApprovalState, type Decision } from "./model";

const SENT_LABEL: Record<Decision, string> = {
  approve: "已送出核准",
  reject: "已送出駁回",
  revise: "已退回修改",
};

function time(iso: string): string {
  return new Date(iso).toLocaleString("zh-TW", { hour12: false });
}

function Card({
  card,
  sent,
  error,
  busy,
  onDecide,
}: {
  card: ApprovalCard;
  sent: Decision | undefined;
  error: string | undefined;
  busy: boolean;
  onDecide: (decision: Decision, reason: string | null) => void;
}) {
  const [reason, setReason] = useState("");
  const pending = card.state === "PENDING";
  return (
    <li className="rounded-xl border border-line bg-surface p-4" data-testid={`approval-${card.id}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs text-muted">
          {card.kind}
          {card.action ? `・${card.action}` : null}・由 {card.requester} 提出
        </p>
        <p className="text-xs text-muted tabular-nums">
          {pending ? `已等待 ${card.waiting}` : null}
          {pending && card.expires?.in ? (
            <span className={card.expires.soon ? "text-warn" : undefined}>・{card.expires.in}後過期</span>
          ) : null}
        </p>
      </div>
      <h3 className="mt-1 font-medium break-words">{card.summary}</h3>
      <pre className="mt-2 overflow-x-auto rounded-lg border border-line bg-canvas p-3 text-xs">
        {JSON.stringify(card.details, null, 2)}
      </pre>
      <p className="mt-2 flex gap-3 text-sm">
        {card.runId ? (
          <Link href={`/trace/${card.runId}`} className="text-accent underline">
            執行軌跡
          </Link>
        ) : null}
        {card.taskId ? (
          <Link href={`/tasks/${card.taskId}`} className="text-accent underline">
            任務
          </Link>
        ) : null}
      </p>

      {card.decision ? (
        <p className="mt-3 text-sm text-muted">
          {card.decision.by} 於 {time(card.decision.at)} 決定
          {card.decision.reason ? `：${card.decision.reason}` : null}
        </p>
      ) : null}

      {pending ? (
        sent ? (
          <p className="mt-3 text-sm text-muted" role="status">
            {SENT_LABEL[sent]}，等待更新…
          </p>
        ) : (
          <div className="mt-3 grid gap-2">
            <label className="grid gap-1 text-sm">
              <span className="text-muted">
                {card.canSendBack ? "意見（退回修改時必填：寫手會照這段修改）" : "理由（選填，駁回時建議填寫）"}
              </span>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                maxLength={2000}
                rows={2}
                className="rounded-lg border border-line bg-canvas px-3 py-2"
              />
            </label>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => onDecide("approve", reason.trim() || null)}
                className="rounded-lg bg-ok px-4 py-1.5 text-sm font-medium text-canvas disabled:opacity-50"
              >
                核准
              </button>
              {card.canSendBack ? (
                <button
                  type="button"
                  disabled={busy || !reason.trim()}
                  title={reason.trim() ? undefined : "先寫下要改什麼"}
                  onClick={() => onDecide("revise", reason.trim())}
                  className="rounded-lg border border-line px-4 py-1.5 text-sm disabled:opacity-50"
                >
                  退回修改
                </button>
              ) : null}
              <button
                type="button"
                disabled={busy}
                onClick={() => onDecide("reject", reason.trim() || null)}
                className="rounded-lg border border-danger-line px-4 py-1.5 text-sm text-danger disabled:opacity-50"
              >
                {card.canSendBack ? "駁回（放棄這則）" : "駁回"}
              </button>
            </div>
          </div>
        )
      ) : null}
      {error ? (
        <p className="mt-2 text-sm text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </li>
  );
}

export interface ApprovalInboxProps {
  state: ApprovalState;
  onState: (state: ApprovalState) => void;
  cards: ApprovalCard[] | undefined;
  loadError: string | null;
  /** POST /api/approvals/{id}/decide. */
  decide: (id: string, decision: Decision, reason: string | null) => Promise<unknown>;
  /** Is the event stream live? If not, no APPROVAL_* event will refresh the list. */
  live: boolean;
  refresh: () => void;
}

/**
 * The inbox. A decision is sent over REST and the card then says so; the list itself changes
 * when the APPROVAL_* event arrives and invalidates it (refetched right away when the stream is
 * down, since no event would come).
 */
export function ApprovalInbox({ state, onState, cards, loadError, decide, live, refresh }: ApprovalInboxProps) {
  const [sent, setSent] = useState<Record<string, Decision>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  async function onDecide(id: string, decision: Decision, reason: string | null) {
    setBusy(id);
    setErrors((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    try {
      await decide(id, decision, reason);
      setSent((prev) => ({ ...prev, [id]: decision }));
      if (!live) refresh();
    } catch (error) {
      const gone = error instanceof ApiError && (error.status === 409 || error.status === 404);
      setErrors((prev) => ({
        ...prev,
        [id]: gone ? "這筆審批已經被處理或不存在，已重新載入列表。" : `送出失敗：${(error as Error).message}`,
      }));
      if (gone) refresh();
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <nav className="mb-4 flex gap-1 border-b border-line" role="tablist">
        {STATES.map((s) => (
          <button
            key={s.id}
            type="button"
            role="tab"
            aria-selected={state === s.id}
            onClick={() => onState(s.id)}
            className={`border-b-2 px-3 py-2 text-sm ${
              state === s.id ? "border-accent font-medium" : "border-transparent text-muted"
            }`}
          >
            {s.label}
          </button>
        ))}
      </nav>
      {loadError ? (
        <p role="alert" className="text-danger">
          無法載入審批：{loadError}
        </p>
      ) : !cards ? (
        <p className="text-muted">載入中…</p>
      ) : cards.length ? (
        <ul className="grid gap-3" aria-label="審批">
          {cards.map((card) => (
            <Card
              key={card.id}
              card={card}
              sent={sent[card.id]}
              error={errors[card.id]}
              busy={busy === card.id}
              onDecide={(decision, reason) => onDecide(card.id, decision, reason)}
            />
          ))}
        </ul>
      ) : (
        <p className="text-muted">{state === "PENDING" ? "目前沒有等待審批的項目。" : "沒有資料。"}</p>
      )}
    </>
  );
}
