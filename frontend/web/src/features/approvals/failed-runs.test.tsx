// @vitest-environment jsdom
// AC-9's last line: a person can start a failed workflow again, from the inbox.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FailedRuns, restartMessage, type FailedRun, type RestartResult } from "./FailedRuns";

afterEach(cleanup);

const run = (over: Partial<FailedRun> = {}): FailedRun => ({
  id: "01a0b900-0000-7000-8000-000000000001",
  template_name: "newsroom.story_to_article_v2",
  project_id: "01a0b900-0000-7000-8000-0000000000aa",
  state: "FAILED",
  created_at: "2026-09-21T09:00:00Z",
  params: { title: "Lumen City's microgrid" },
  failed_tasks: ["審稿：Lumen City's microgrid"],
  restarted: false,
  ...over,
});

const done = (over: Partial<RestartResult> = {}): RestartResult => ({
  decision: "allow",
  outcome: "done",
  reason: null,
  workflow_run_id: "01a0b900-0000-7000-8000-0000000000bb",
  ...over,
});

describe("what the company said about the restart", () => {
  it("says what happened, including when the answer was no", () => {
    expect(restartMessage(done())).toBe("已重新啟動");
    expect(restartMessage(done({ outcome: "awaiting_approval" }))).toContain("核准");
    const refused = done({
      outcome: "refused",
      decision: "deny",
      reason: "5 workflows already started this cycle (cap 5)",
      workflow_run_id: null,
    });
    expect(restartMessage(refused)).toContain("cap 5");
    // a refusal with no reason still says the company refused, not nothing
    expect(restartMessage(done({ outcome: "refused", reason: null }))).toContain("拒絕");
  });
});

describe("the failed runs in the inbox", () => {
  it("shows what failed and starts it again when asked", async () => {
    const onRestart = vi.fn().mockResolvedValue(done());
    render(<FailedRuns runs={[run()]} onRestart={onRestart} />);

    expect(screen.getByText("Lumen City's microgrid")).toBeTruthy();
    expect(screen.getByText(/審稿：Lumen City's microgrid/)).toBeTruthy();

    fireEvent.click(screen.getByTestId("restart-01a0b900-0000-7000-8000-000000000001"));
    await waitFor(() =>
      expect(screen.getByTestId("restart-said-01a0b900-0000-7000-8000-000000000001").textContent)
        .toBe("已重新啟動"),
    );
    expect(onRestart).toHaveBeenCalledWith("01a0b900-0000-7000-8000-000000000001");
  });

  it("a refusal is shown, not swallowed", async () => {
    const onRestart = vi.fn().mockResolvedValue(
      done({ outcome: "refused", reason: "that run is still RUNNING", workflow_run_id: null }),
    );
    render(<FailedRuns runs={[run()]} onRestart={onRestart} />);

    fireEvent.click(screen.getByTestId("restart-01a0b900-0000-7000-8000-000000000001"));
    await waitFor(() =>
      expect(
        screen.getByTestId("restart-said-01a0b900-0000-7000-8000-000000000001").textContent,
      ).toContain("still RUNNING"),
    );
  });

  it("a request that never arrived says so too", async () => {
    const onRestart = vi.fn().mockRejectedValue(new Error("網路斷了"));
    render(<FailedRuns runs={[run()]} onRestart={onRestart} />);

    fireEvent.click(screen.getByTestId("restart-01a0b900-0000-7000-8000-000000000001"));
    await waitFor(() =>
      expect(
        screen.getByTestId("restart-said-01a0b900-0000-7000-8000-000000000001").textContent,
      ).toBe("網路斷了"),
    );
  });

  it("says when somebody already restarted it, and a run with no failed step is still listed", () => {
    render(
      <FailedRuns
        runs={[run({ restarted: true, failed_tasks: [] })]}
        onRestart={vi.fn()}
      />,
    );
    expect(screen.getByText(/已經有人重新啟動過/)).toBeTruthy();
    expect(screen.getByText(/沒有單一步驟失敗/)).toBeTruthy();
  });

  it("nothing failed: the section is not there at all", () => {
    const { container } = render(<FailedRuns runs={[]} onRestart={vi.fn()} />);
    expect(container.firstChild).toBeNull();
    expect(render(<FailedRuns runs={undefined} onRestart={vi.fn()} />).container.firstChild).toBeNull();
  });
});

describe("work that already ran again (D-044)", () => {
  it("offers no restart, and says why", () => {
    render(<FailedRuns runs={[run({ superseded_by: "01a0b900-0000-7000-8000-0000000000cc" })]} onRestart={vi.fn()} />);
    expect(screen.queryByTestId("restart-01a0b900-0000-7000-8000-000000000001")).toBeNull();
    expect(screen.getByTestId("superseded-01a0b900-0000-7000-8000-000000000001").textContent).toContain(
      "已經有新的執行",
    );
  });

  it("names the story rather than the template", () => {
    render(<FailedRuns runs={[run()]} onRestart={vi.fn()} />);
    expect(screen.getByText("Lumen City's microgrid")).toBeTruthy();
  });
});
