// @vitest-environment jsdom
// Budgets and capital on the admin dashboard (D-054): the balance and every envelope, a warning
// when an envelope of NT$0 stops work (cycle 5), and the two requests the forms send.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { createApiClient } from "@/api/client";
import { addCapital, setBudget, type Finance } from "@/api/queries";

import { FinanceView } from "./FinancePanel";

afterEach(cleanup);

const FINANCE: Finance = {
  currency: "TWD",
  balance: "0",
  spent_today: "0",
  cycle_seq: 5,
  cycle_stage: "EXECUTING",
  budgets: [
    { id: "b1", scope: "project", scope_id: "p1", name: "持股動態與科技產業", period: "cycle", amount: "0", currency: "TWD", hard_cap: true },
    { id: "b2", scope: "business_unit", scope_id: "u1", name: "AI Media", period: "cycle", amount: "100", currency: "TWD", hard_cap: true },
  ],
  projects: [{ id: "p1", name: "持股動態與科技產業", state: "ACTIVE" }],
};

function show(finance: Finance) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <FinanceView companyId="c1" finance={finance} />
    </QueryClientProvider>,
  );
}

describe("the finance panel", () => {
  it("shows the balance, the envelopes, and which of them stop work", () => {
    show(FINANCE);
    expect(screen.getByTestId("finance-balance").textContent).toMatch(/0/);
    expect(screen.getByTestId("finance-budgets").textContent).toContain("AI Media");
    expect(screen.getByRole("alert").textContent).toContain("持股動態與科技產業 的預算是 0");
    expect(screen.getByRole("option", { name: "專案：持股動態與科技產業" })).toBeTruthy();
  });

  it("says nothing is limited when there is no envelope", () => {
    show({ ...FINANCE, budgets: [] });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(/不設就不限制/)).toBeTruthy();
  });

  it("sends a budget and a capital injection where the API expects them", async () => {
    const requests: { method: string; url: string; body: unknown }[] = [];
    const api = createApiClient({
      baseUrl: "http://api",
      fetch: (async (r: Request) => {
        requests.push({ method: r.method, url: r.url, body: await r.json() });
        return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
      }) as typeof fetch,
    });
    await setBudget("c1", { amount: "100", period: "cycle", project_id: "p1" }, api);
    await addCapital("c1", "3000", "九月", "r1", api);
    expect(requests).toEqual([
      { method: "POST", url: "http://api/api/companies/c1/finance/budgets", body: { amount: "100", period: "cycle", project_id: "p1" } },
      { method: "POST", url: "http://api/api/companies/c1/finance/capital", body: { amount: "3000", memo: "九月", request_id: "r1" } },
    ]);
  });
});
