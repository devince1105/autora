// Trace viewer (T-311, 3d-office/01 §3.8): every row is a real event or a real step of the run;
// nothing is synthesised on the client. Known event types get a readable line; any other type
// (newer server, other domain) is still shown, with its raw payload.
import type { Schemas } from "@/api/client";
import { describeEvent, type Tone } from "@/events/describe";

export type { Tone };

export type Trace = Schemas["Trace"];
export type TraceEntry = Schemas["TraceEntry"];
export type Step = Schemas["StepView"];

export interface Row {
  key: string;
  kind: "event" | "step";
  seq: number | null;
  at: string;
  label: string;
  summary: string | null;
  tone: Tone;
  known: boolean;
  payload: Record<string, unknown> | null;
  step: Step | null;
}

export function eventRow(entry: TraceEntry): Row {
  const { label, tone, summary, known } = describeEvent(entry.event_type, entry.payload);
  return {
    key: `e${entry.seq}`,
    kind: "event",
    seq: entry.seq,
    at: entry.occurred_at,
    label,
    summary,
    tone,
    known,
    payload: (entry.payload ?? {}) as Record<string, unknown>,
    step: entry.step ?? null,
  };
}

function stepRow(step: Step): Row {
  return {
    key: `s${step.seq}`,
    kind: "step",
    seq: null,
    at: step.created_at,
    label: `步驟 #${step.seq}（${step.kind}）`,
    summary: step.summary,
    tone: "neutral",
    known: true,
    payload: null,
    step,
  };
}

/**
 * Events in seq order; a step no event refers to (e.g. an evaluate step) is placed by time.
 * The trace endpoint already joined each event to its step (payload.step_seq).
 */
export function traceRows(trace: Trace | undefined): Row[] {
  if (!trace) return [];
  const rows = trace.entries.map(eventRow);
  // Several events share one step (WORKING, TOOL_CALLED, TOOL_COMPLETED): show it once, under
  // the first of them.
  const shown = new Set<number>();
  for (const row of rows) {
    if (!row.step) continue;
    if (shown.has(row.step.seq)) row.step = null;
    else shown.add(row.step.seq);
  }
  const referenced = shown;
  const orphans = trace.steps.filter((step) => !referenced.has(step.seq)).map(stepRow);
  for (const orphan of orphans) {
    const at = Date.parse(orphan.at);
    const index = rows.findIndex((row) => Date.parse(row.at) > at);
    rows.splice(index === -1 ? rows.length : index, 0, orphan);
  }
  return rows;
}

export interface TraceSummary {
  events: number;
  steps: number;
  toolCalls: number;
  failures: number;
  unknownTypes: string[];
}

export function traceSummary(trace: Trace | undefined, rows: Row[]): TraceSummary {
  return {
    events: trace?.entries.length ?? 0,
    steps: trace?.steps.length ?? 0,
    toolCalls: rows.filter((r) => r.kind === "event" && r.label === "呼叫工具").length,
    failures: rows.filter((r) => r.tone === "danger").length,
    unknownTypes: [...new Set(rows.filter((r) => !r.known).map((r) => r.label))],
  };
}
