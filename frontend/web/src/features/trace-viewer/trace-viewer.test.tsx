// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { traceRows, traceSummary, type Trace } from "./model";
import { TraceView } from "./TraceView";

// A real trace: an echo analyst run fetched from GET /api/runs/{id}/trace on the dev stack.
const real = JSON.parse(
  readFileSync(join(process.cwd(), "src/features/trace-viewer/__fixtures__/echo-trace.json"), "utf8"),
) as Trace;

afterEach(cleanup);

describe("rows: every real event and step, nothing invented", () => {
  it("keeps event order and places the evaluate step no event refers to", () => {
    const rows = traceRows(real);
    const events = rows.filter((r) => r.kind === "event");
    expect(events.map((r) => r.seq)).toEqual(real.entries.map((e) => e.seq));
    const orphan = rows.filter((r) => r.kind === "step");
    expect(orphan.map((r) => r.step?.kind)).toEqual(["evaluate"]);
    // placed by time: after the event before it, before the event after it
    const index = rows.indexOf(orphan[0]);
    expect(Date.parse(rows[index - 1].at)).toBeLessThanOrEqual(Date.parse(orphan[0].at));
    expect(rows.length).toBe(real.entries.length + 1);
  });

  it("shows a shared step once, under the first event that refers to it", () => {
    const rows = traceRows(real);
    const withStep = rows.filter((r) => r.step).map((r) => r.step!.seq);
    expect(withStep).toEqual([...new Set(withStep)]);
    expect(new Set(withStep)).toEqual(new Set(real.steps.map((s) => s.seq)));
  });

  it("readable lines for known types", () => {
    const byType = Object.fromEntries(
      traceRows(real).filter((r) => r.kind === "event").map((r, i) => [real.entries[i].event_type, r]),
    );
    expect(byType.AGENT_WORKING).toMatchObject({ label: "使用工具", summary: "echo_note", tone: "work" });
    expect(byType.TOOL_COMPLETED.summary).toMatch(/^echo_note・\d+ ms・wrote note .+・產出 1$/);
    expect(byType.AGENT_REVIEWING).toMatchObject({ label: "檢查", summary: "沒有問題" });
    expect(byType.TASK_SUCCEEDED.summary).toContain("解鎖 1 個下游任務");
    expect(traceSummary(real, traceRows(real))).toMatchObject({
      events: 10, steps: 4, toolCalls: 1, failures: 0, unknownTypes: [],
    });
  });

  it("an unknown event type is still shown, with its raw payload", () => {
    const newer = {
      ...real,
      entries: [...real.entries, { ...real.entries[0], seq: 99_999, event_type: "AGENT_DANCED", payload: { moves: 3 } }],
    } as Trace;
    const rows = traceRows(newer);
    const last = rows[rows.length - 1];
    expect(last).toMatchObject({ label: "AGENT_DANCED", known: false, payload: { moves: 3 } });
    expect(traceSummary(newer, rows).unknownTypes).toEqual(["AGENT_DANCED"]);
  });
});

describe("view", () => {
  function renderReal(loadBlob = vi.fn(async () => ({ request: { system: "..." } }))) {
    const rows = traceRows(real);
    render(
      <TraceView trace={real} rows={rows} summary={traceSummary(real, rows)} taskName="Echo: analyse" loadBlob={loadBlob} />,
    );
    return loadBlob;
  }

  it("header, one row per real item, and the link to the task", () => {
    renderReal();
    expect(screen.getByRole("heading", { name: "Echo: analyse" })).toBeTruthy();
    const list = screen.getByRole("list", { name: "軌跡" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(real.entries.length + 1);
    expect(screen.getByRole("link", { name: "任務與其他嘗試" }).getAttribute("href")).toBe(`/tasks/${real.task_id}`);
    expect(screen.getByText(/10 個事件・4 個步驟・1 次工具呼叫/)).toBeTruthy();
  });

  it("loads a step's full prompt and response on demand", async () => {
    const loadBlob = renderReal();
    const step = screen.getByTestId("step-0");
    fireEvent.click(within(step).getByRole("button", { name: "顯示完整提示與回應" }));
    await waitFor(() => expect(within(step).getByText(/"system": "\.\.\."/)).toBeTruthy());
    expect(loadBlob).toHaveBeenCalledWith(0);
  });

  it("raw payload of a known event opens on request; an unknown one is open", () => {
    const newer = {
      ...real,
      entries: [...real.entries, { ...real.entries[0], seq: 99_999, event_type: "AGENT_DANCED", payload: { moves: 3 } }],
    } as Trace;
    const rows = traceRows(newer);
    render(<TraceView trace={newer} rows={rows} summary={traceSummary(newer, rows)} taskName={null} loadBlob={vi.fn()} />);
    expect(screen.getByText(/"moves": 3/)).toBeTruthy();
    expect(screen.getByText(/這個畫面不認得的事件（AGENT_DANCED）/)).toBeTruthy();
    const working = screen.getByTestId(`row-e${real.entries[3].seq}`);
    fireEvent.click(within(working).getByRole("button", { name: "原始資料" }));
    expect(within(working).getByText(/"tool": "echo_note"/)).toBeTruthy();
  });
});
