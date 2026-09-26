import Link from "next/link";
import type { ReactNode } from "react";

import type { PanelTab } from "@/stores/ui";

import {
  formatDuration,
  ROLE_LABEL,
  type CardModel,
  type NextStep,
  type Run,
  type ToolStats,
  type Trace,
} from "./model";
import { ProgressBar, StateBadge } from "./StateBadge";

export interface PanelData {
  card: CardModel;
  runId: string | null;
  nextSteps: NextStep[];
  links: { label: string; href: string }[];
  run: Run | undefined;
  trace: Trace | undefined;
  produced: { type: string; label: string; count: number }[];
  tools: ToolStats[];
  loading: boolean;
  error: string | null;
}

const TABS: { id: PanelTab; label: string }[] = [
  { id: "live", label: "即時" },
  { id: "steps", label: "步驟" },
  { id: "tools", label: "工具" },
  { id: "output", label: "輸出" },
];

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[6rem_1fr] gap-2 py-1.5 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

const muted = (text: string) => <span className="text-muted">{text}</span>;

function Live({ data }: { data: PanelData }) {
  const { card, run } = data;
  return (
    <dl className="divide-y divide-line">
      <Row label="目前任務">{card.taskName ?? muted("沒有")}</Row>
      <Row label="工具">{card.tool ?? muted("—")}</Row>
      <Row label="已進行">{formatDuration(card.sinceMs)}</Row>
      {card.progress ? (
        <Row label="進度">
          <ProgressBar {...card.progress} />
        </Row>
      ) : null}
      {card.tokens !== null ? <Row label="權杖">{card.tokens.toLocaleString("zh-TW")}</Row> : null}
      <Row label="產出">
        {data.produced.length
          ? data.produced.map((p) => (
              <span key={p.type} className="mr-3" data-testid={`produced-${p.type}`}>
                {p.label} {p.count}
              </span>
            ))
          : muted(data.runId ? "還沒有" : "—")}
      </Row>
      <Row label="下一步">
        {data.nextSteps.length
          ? data.nextSteps.map((step) => (
              <span key={step.taskId} className="block">
                {step.name}（{ROLE_LABEL[step.role] ?? step.role}）
              </span>
            ))
          : muted("—")}
      </Row>
      {run ? (
        <Row label="本次執行">
          第 {run.attempt} 次嘗試・{run.steps_count} 步・US${Number(run.cost_usd).toFixed(4)}
          <Link href={`/admin/trace/${run.id}`} className="ml-3 text-accent underline">
            完整軌跡
          </Link>
        </Row>
      ) : null}
      {data.links.length ? (
        <Row label="連結">
          {data.links.map((link) => (
            <a key={link.href} href={link.href} className="mr-3 text-accent underline">
              {link.label}
            </a>
          ))}
        </Row>
      ) : null}
    </dl>
  );
}

function Steps({ trace }: { trace: Trace | undefined }) {
  if (!trace?.steps.length) return <p className="text-sm text-muted">還沒有步驟。</p>;
  return (
    <ol className="grid gap-2">
      {trace.steps.map((step) => (
        <li key={step.seq} className="rounded-lg border border-line p-3 text-sm">
          <p className="flex justify-between text-xs text-muted">
            <span>
              #{step.seq}・{step.kind}
            </span>
            <span className="tabular-nums">US${Number(step.cost_usd).toFixed(4)}</span>
          </p>
          <p className="mt-1 break-words">{step.summary ?? muted("（沒有摘要）")}</p>
        </li>
      ))}
    </ol>
  );
}

function Tools({ tools }: { tools: ToolStats[] }) {
  if (!tools.length) return <p className="text-sm text-muted">這次執行沒有呼叫工具。</p>;
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs text-muted">
        <tr>
          <th className="py-1 font-medium">工具</th>
          <th className="py-1 font-medium">呼叫</th>
          <th className="py-1 font-medium">成功</th>
          <th className="py-1 font-medium">失敗</th>
          <th className="py-1 font-medium">平均</th>
        </tr>
      </thead>
      <tbody className="tabular-nums">
        {tools.map((row) => (
          <tr key={row.tool} className="border-t border-line">
            <td className="py-1.5">{row.tool}</td>
            <td>{row.calls}</td>
            <td>{row.ok}</td>
            <td className={row.failed ? "text-danger" : undefined}>{row.failed}</td>
            <td>{row.avgMs === null ? "—" : `${row.avgMs} ms`}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Output({ run }: { run: Run | undefined }) {
  if (!run) return <p className="text-sm text-muted">沒有執行紀錄。</p>;
  const blocks: [string, unknown][] = [
    ["輸出", run.output],
    ["評估", run.evaluation],
    ["錯誤", run.error],
  ];
  const shown = blocks.filter(([, value]) => value !== null && value !== undefined);
  if (!shown.length) return <p className="text-sm text-muted">執行尚未產生輸出。</p>;
  return (
    <div className="grid gap-3">
      {shown.map(([label, value]) => (
        <section key={label}>
          <h3 className="mb-1 text-xs font-medium text-muted">{label}</h3>
          <pre className="overflow-x-auto rounded-lg border border-line bg-canvas p-3 text-xs">
            {JSON.stringify(value, null, 2)}
          </pre>
        </section>
      ))}
    </div>
  );
}

export function AgentPanelView({
  data,
  tab,
  onTab,
  onClose,
}: {
  data: PanelData;
  tab: PanelTab;
  onTab: (tab: PanelTab) => void;
  onClose: () => void;
}) {
  const { card } = data;
  return (
    <aside
      role="dialog"
      aria-label={`${card.name} 的詳細資訊`}
      className="fixed inset-y-0 right-0 z-20 flex w-full max-w-md flex-col border-l border-line bg-surface shadow-xl"
    >
      <header className="flex items-start justify-between gap-3 border-b border-line p-4">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold">{card.name}</h2>
          <p className="text-xs text-muted">{ROLE_LABEL[card.role] ?? card.role}</p>
        </div>
        <div className="flex items-center gap-2">
          <StateBadge state={card.state} label={card.stateLabel} />
          <button
            type="button"
            onClick={onClose}
            aria-label="關閉"
            className="rounded-md px-2 py-1 text-muted hover:bg-canvas"
          >
            ✕
          </button>
        </div>
      </header>
      <nav className="flex gap-1 border-b border-line px-2" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => onTab(t.id)}
            className={`border-b-2 px-3 py-2 text-sm ${
              tab === t.id ? "border-accent font-medium" : "border-transparent text-muted"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="flex-1 overflow-y-auto p-4" role="tabpanel">
        {data.error ? (
          <p role="alert" className="mb-3 text-sm text-danger">
            無法載入明細：{data.error}
          </p>
        ) : null}
        {tab === "live" ? <Live data={data} /> : null}
        {tab === "steps" ? <Steps trace={data.trace} /> : null}
        {tab === "tools" ? <Tools tools={data.tools} /> : null}
        {tab === "output" ? <Output run={data.run} /> : null}
        {data.loading && tab !== "live" ? <p className="text-sm text-muted">載入中…</p> : null}
      </div>
    </aside>
  );
}
