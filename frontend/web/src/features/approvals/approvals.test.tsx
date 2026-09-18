// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { eventToQueryKeys } from "@/api/invalidation";
import type { AgentState } from "@/realtime/reducer";

import { ApprovalInbox, type ApprovalInboxProps } from "./ApprovalInbox";
import { approvalCard, type Approval } from "./model";

// A real pending approval: the echo writer's echo_note, with the company policy override
// echo_note/writer = needs_approval (seed_echo.py --approval on), from GET /api/approvals.
const [pending] = JSON.parse(
  readFileSync(join(process.cwd(), "src/features/approvals/__fixtures__/pending.json"), "utf8"),
) as Approval[];
const writerId = String(pending.requested_by.id);
const agents = {
  [writerId]: { id: writerId, role: "writer", display_name: "Wren", avatar_key: "default", activity: null, liveProgress: null },
} as Record<string, AgentState>;
const NOW = new Date(Date.parse(pending.created_at) + 90_000);

afterEach(cleanup);

describe("model", () => {
  it("a pending tool call: who asks, what will run, how long it has waited", () => {
    const card = approvalCard(pending, agents, NOW);
    expect(card).toMatchObject({
      state: "PENDING",
      kind: "工具呼叫",
      action: "echo_note",
      requester: "Wren",
      details: { text: "writer on approval inbox" },
      waiting: "1 分 30 秒",
      taskId: pending.task_id,
      runId: pending.run_id,
      decision: null,
    });
    expect(card.expires).toMatchObject({ at: pending.expires_at, soon: false });
    expect(card.expires!.in).toMatch(/小時/);
    // unknown agent: still says who, by id
    expect(approvalCard(pending, {}, NOW).requester).toBe(`代理 ${writerId.slice(0, 8)}`);
  });

  it("a decided one says by whom and why", () => {
    const decided = {
      ...pending,
      state: "REJECTED",
      decided_by: { kind: "human", id: "operator" },
      decided_at: "2026-09-18T11:00:00Z",
      reason: "wrong topic",
    } as Approval;
    expect(approvalCard(decided, agents, NOW).decision).toEqual({
      by: "人員 operator",
      at: "2026-09-18T11:00:00Z",
      reason: "wrong topic",
    });
  });

  it("an APPROVAL_* event makes every approvals list of the company stale", () => {
    const keys = eventToQueryKeys({
      event_type: "APPROVAL_APPROVED",
      company_id: pending.company_id,
      run_id: pending.run_id,
      task_id: pending.task_id,
    } as Parameters<typeof eventToQueryKeys>[0]);
    expect(keys).toContainEqual(["approvals", pending.company_id]);
  });
});

describe("inbox", () => {
  function setup(overrides: Partial<ApprovalInboxProps> = {}) {
    const props: ApprovalInboxProps = {
      state: "PENDING",
      onState: vi.fn(),
      cards: [approvalCard(pending, agents, NOW)],
      loadError: null,
      decide: vi.fn(async () => ({})),
      live: true,
      refresh: vi.fn(),
      ...overrides,
    };
    const view = render(<ApprovalInbox {...props} />);
    const card = () => screen.getByTestId(`approval-${pending.id}`);
    return { props, view, card };
  }

  it("approve: sent over REST, then the list changes when the event refreshes it", async () => {
    const { props, view, card } = setup();
    expect(within(card()).getByText(pending.summary)).toBeTruthy();
    expect(within(card()).getByRole("link", { name: "執行軌跡" }).getAttribute("href")).toBe(`/trace/${pending.run_id}`);

    fireEvent.click(within(card()).getByRole("button", { name: "核准" }));
    await waitFor(() => expect(within(card()).getByRole("status").textContent).toBe("已送出核准，等待更新…"));
    expect(props.decide).toHaveBeenCalledWith(pending.id, "approve", null);
    expect(within(card()).queryByRole("button", { name: "核准" })).toBeNull();
    expect(props.refresh).not.toHaveBeenCalled(); // live: the APPROVAL_APPROVED event will do it

    view.rerender(<ApprovalInbox {...props} cards={[]} />);
    expect(screen.getByText("目前沒有等待審批的項目。")).toBeTruthy();
  });

  it("reject with a reason", async () => {
    const { props, card } = setup();
    fireEvent.change(within(card()).getByRole("textbox"), { target: { value: "  wrong topic " } });
    fireEvent.click(within(card()).getByRole("button", { name: "駁回" }));
    await waitFor(() => expect(props.decide).toHaveBeenCalledWith(pending.id, "reject", "wrong topic"));
    await waitFor(() => expect(within(card()).getByRole("status").textContent).toBe("已送出駁回，等待更新…"));
  });

  it("with the stream down, no event will come: refetch right away", async () => {
    const { props, card } = setup({ live: false });
    fireEvent.click(within(card()).getByRole("button", { name: "核准" }));
    await waitFor(() => expect(props.refresh).toHaveBeenCalledTimes(1));
  });

  it("already decided elsewhere (409): says so and reloads", async () => {
    const decide = vi.fn(async () => {
      throw new ApiError(409, "Conflict", `approval ${pending.id} is already APPROVED`, null);
    });
    const { props, card } = setup({ decide });
    fireEvent.click(within(card()).getByRole("button", { name: "核准" }));
    await waitFor(() => expect(within(card()).getByRole("alert").textContent).toContain("已經被處理"));
    expect(props.refresh).toHaveBeenCalled();
    expect(within(card()).getByRole("button", { name: "核准" })).toBeTruthy(); // not marked as sent
  });

  it("another failure keeps the card and shows the error", async () => {
    const decide = vi.fn(async () => {
      throw new Error("network down");
    });
    const { props, card } = setup({ decide });
    fireEvent.click(within(card()).getByRole("button", { name: "駁回" }));
    await waitFor(() => expect(within(card()).getByRole("alert").textContent).toBe("送出失敗：network down"));
    expect(props.refresh).not.toHaveBeenCalled();
  });

  it("state tabs; decided lists have no buttons", () => {
    const decided = { ...pending, state: "APPROVED", decided_by: { kind: "human", id: "operator" }, decided_at: pending.created_at } as Approval;
    const { props } = setup({ state: "APPROVED", cards: [approvalCard(decided, agents, NOW)] });
    expect(screen.queryByRole("button", { name: "核准" })).toBeNull();
    expect(screen.getByText(/人員 operator 於/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "已過期" }));
    expect(props.onState).toHaveBeenCalledWith("EXPIRED");
  });
});
