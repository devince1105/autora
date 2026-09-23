// The strip along the bottom of the 2D office (T-410 stage 3).
//
// Only what the store already knows — who is working, what is running, which day the company is
// on. No money: the office is a read-only view of the realtime store and does not call the API,
// and the numbers above the page already say what the ledger says. A figure repeated from
// somewhere else is a figure that can disagree with it.
"use client";

import { useRealtime } from "@/stores/realtime";

import { CONSOLE } from "./console";

const STAGE: Record<string, string> = {
  PLANNING: "規劃",
  EXECUTING: "執行",
  MEASURING: "量測",
  REVIEWING: "覆盤",
  DONE: "完成",
};

const WORKING = new Set(["THINKING", "WORKING", "REVIEWING"]);

export interface TickerItem {
  label: string;
  value: string;
  tone?: "ok" | "warn";
}

export function Ticker() {
  const company = useRealtime((s) => s.company);
  const agents = Object.values(company?.agents ?? {});
  const tasks = Object.values(company?.tasks ?? {});
  const busy = agents.filter((agent) => WORKING.has(agent.activity?.state ?? "")).length;
  const waiting = tasks.filter((task) => task.state === "WAITING_APPROVAL").length;
  const running = tasks.filter((task) => task.state === "RUNNING" || task.state === "READY").length;
  const items: TickerItem[] = [
    { label: "代理", value: `${busy} / ${agents.length}`, tone: busy ? "ok" : undefined },
    { label: "任務", value: String(running) },
    { label: "待審批", value: String(waiting), tone: waiting ? "warn" : undefined },
    {
      label: "今天",
      value: company?.cycle ? `第 ${company.cycle.seq} 輪・${STAGE[company.cycle.stage] ?? company.cycle.stage}` : "—",
    },
    { label: "事件", value: String(company?.lastSeq ?? 0) },
  ];
  return (
    <div
      data-testid="office-ticker"
      aria-label="辦公室概況"
      className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t-2 border-[color:var(--console-edge-dim)] bg-[color:var(--console-panel-dim)] px-3 py-1.5 text-[11px] uppercase tracking-[0.15em]"
    >
      {items.map((item) => (
        <span key={item.label} className="flex items-baseline gap-2">
          <span className="text-[color:var(--console-text-dim)]">{item.label}</span>
          <span
            className="tabular-nums"
            style={{ color: item.tone === "ok" ? CONSOLE.ok : item.tone === "warn" ? CONSOLE.warn : CONSOLE.text }}
          >
            {item.value}
          </span>
        </span>
      ))}
    </div>
  );
}
