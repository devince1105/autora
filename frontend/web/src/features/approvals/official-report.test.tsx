// @vitest-environment jsdom
// D-051: a transcribed official's report is checked against its scan in the inbox — the card
// says where the PDF is, how much was read, and which rows a stock page will show.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApprovalInbox } from "./ApprovalInbox";
import { approvalCard, type Approval } from "./model";

afterEach(cleanup);

const AT = "2026-09-26T00:00:00Z";
const approval = {
  id: "ap9",
  company_id: "c1",
  kind: "official_report",
  state: "PENDING",
  action: "approve_official_report",
  summary: "核對川普 2026-09-22 交易申報的轉錄：120 筆，其中 2 筆有股票代號、1 筆讀不清",
  payload: {
    report: "https://extapps2.oge.gov/r.pdf",
    person: "川普",
    received_on: "2026-09-22",
    pages: 5,
    rows: 120,
    unreadable: 1,
    stock_rows: 2,
    stocks: ["p.2 #65 BAC purchase 2026-02-05 $15,001 - $50,000", "p.2 #66 NVDA sale 2026-02-05 $250,001 - $500,000"],
  },
  task_id: null,
  run_id: null,
  ref_type: "official_report",
  ref_id: "r1",
  requested_by: { kind: "system", id: "newsroom" },
  created_at: AT,
  expires_at: null,
  decided_at: null,
  decided_by: null,
  reason: null,
} as unknown as Approval;

describe("checking a transcribed report", () => {
  it("shows the scan's link, what was read and the rows for the stock pages — not JSON", () => {
    const card = approvalCard(approval, {}, new Date(AT));
    expect(card.kind).toBe("名人交易申報");
    expect(card.canSendBack).toBe(false); // approve or reject; nothing to send back to
    render(
      <ApprovalInbox
        state="PENDING"
        onState={vi.fn()}
        cards={[card]}
        loadError={null}
        decide={vi.fn(async () => ({}))}
        live
        refresh={vi.fn()}
      />,
    );
    const preview = within(screen.getByTestId("official-report"));
    expect(preview.getByRole("link", { name: /開啟原始申報（PDF，5 頁）/ }).getAttribute("href")).toBe(
      "https://extapps2.oge.gov/r.pdf",
    );
    expect(screen.getByTestId("official-report").textContent).toContain("有 1 筆的日期或金額讀不清");
    expect(preview.getAllByRole("listitem").map((li) => li.textContent)).toEqual(approval.payload.stocks as string[]);
    expect(screen.getByTestId("approval-ap9").querySelector("pre")).toBeNull();
  });
});
