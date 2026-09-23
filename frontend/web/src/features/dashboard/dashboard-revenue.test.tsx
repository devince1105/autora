// @vitest-environment jsdom
// T-707: the revenue section — the last 30 days of money and members, as the backend counted them.
//
// No numbers here are made up by the page (T-309 AC): every one comes from the KPIs endpoint's
// revenue block, and the page only turns decimal strings into numbers and says what they are.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Connection } from "@/stores/realtime";

import { DashboardView } from "./DashboardView";
import { dashboardModel, formatMoney, revenueModel, type KpisData } from "./model";

afterEach(cleanup);

const live: Connection = { status: "live", lastEventAt: null, serverOffsetMs: 0 } as unknown as Connection;

/** 30 UTC days ending 2026-09-23, with money on three of them. */
const DAILY = Array.from({ length: 30 }, (_, i) => {
  const day = new Date(Date.UTC(2026, 7, 25 + i)).toISOString().slice(0, 10);
  const amount = i === 5 ? "360.000000" : i === 20 ? "720.000000" : i === 29 ? "360.000000" : "0.000000";
  return { day, amount };
});

const KPIS: KpisData = {
  as_of: "2026-09-23T12:00:00Z",
  currency: "TWD",
  cash: "1000",
  revenue_today: "360",
  expenses_today: "0",
  model_cost_today: "0",
  revenue: {
    days: 30,
    total: "1440.000000",
    daily: DAILY,
    payments: 4,
    new_members: 3,
    renewals: 1,
    lapsed_members: 2,
    members: 11,
    expiring_members: 3,
    average_payment: "360.000000",
    offer: { amount: "360.000000", currency: "TWD", interval: "year" },
  },
};

function show(kpis: KpisData | undefined) {
  render(<DashboardView companyId="c1" companyName="Autora" model={dashboardModel(null, kpis, live, new Date())} />);
  return screen.getByTestId("revenue-section");
}

describe("the revenue model", () => {
  it("turns the endpoint's decimal strings into numbers, and nothing else", () => {
    const model = revenueModel(KPIS)!;
    expect(model.total).toBe(1440);
    expect(model.daily).toHaveLength(30);
    expect(model.daily[20]).toEqual({ day: "2026-09-14", amount: 720 });
    expect([model.members, model.expiring, model.newMembers, model.renewals, model.lapsed]).toEqual([11, 3, 3, 1, 2]);
    expect(model.averagePayment).toBe(360);
    expect(model.offer).toEqual({ amount: 360, currency: "TWD", interval: "year" });
  });

  it("is nothing at all until the endpoint has answered", () => {
    expect(revenueModel(undefined)).toBeNull();
    expect(revenueModel({ ...KPIS, revenue: null })).toBeNull();
  });

  it("keeps an average of no payments as none, not zero", () => {
    const quiet = revenueModel({ ...KPIS, revenue: { ...KPIS.revenue!, payments: 0, average_payment: null } })!;
    expect(quiet.averagePayment).toBeNull();
  });
});

describe("the revenue section", () => {
  it("shows the money, the members and who is about to go", () => {
    const section = show(KPIS);
    expect(within(section).getByTestId("revenue-total").textContent).toContain(formatMoney(1440, "TWD"));
    expect(within(section).getByTestId("revenue-total").textContent).toContain("4 筆付款");
    expect(within(section).getByTestId("members").textContent).toContain("11");
    expect(within(section).getByTestId("members").textContent).toContain("30 天內到期 3 位");
    expect(within(section).getByTestId("new-members").textContent).toContain("續約 1");
    expect(within(section).getByTestId("lapsed").textContent).toContain("2");
  });

  it("names the price and what it buys", () => {
    const offer = within(show(KPIS)).getByTestId("offer");
    expect(offer.textContent).toContain(formatMoney(360, "TWD"));
    expect(offer.textContent).toContain("／年");
  });

  it("says a company that sells nothing is not on sale, rather than showing a price of nothing", () => {
    const offer = within(show({ ...KPIS, revenue: { ...KPIS.revenue!, offer: null } })).getByTestId("offer");
    expect(offer.textContent).toContain("尚未開賣");
  });

  it("draws a bar for every day, the busiest the tallest and a quiet day as a stub", () => {
    const bars = within(show(KPIS)).getAllByTestId("revenue-bar");
    expect(bars).toHaveLength(30);
    const heights = bars.map((bar) => Number(bar.getAttribute("height")));
    expect(Math.max(...heights)).toBe(heights[20]);
    expect(heights[5]).toBe(heights[20] / 2);
    expect(heights[0]).toBe(1); // nothing that day: a stub, not a gap
    expect(bars[20].querySelector("title")?.textContent).toContain("2026-09-14");
  });

  it("describes the chart in words for a reader who cannot see it", () => {
    const chart = within(show(KPIS)).getByRole("img");
    expect(chart.getAttribute("aria-label")).toContain(formatMoney(1440, "TWD"));
    expect(chart.getAttribute("aria-label")).toContain("2026-09-14");
  });

  it("says a month with no money is a month with no money, instead of drawing thirty stubs", () => {
    const empty = { ...KPIS.revenue!, total: "0", daily: DAILY.map((d) => ({ ...d, amount: "0" })), payments: 0, average_payment: null };
    const section = show({ ...KPIS, revenue: empty });
    expect(within(section).queryByTestId("revenue-chart")).toBeNull();
    expect(within(section).getByTestId("revenue-chart-empty").textContent).toContain("沒有收入");
    expect(within(section).getByTestId("revenue-total").textContent).toContain("這段時間沒有付款");
  });

  it("waits with dashes, not zeros, before the endpoint has answered", () => {
    const section = show(undefined);
    for (const id of ["revenue-total", "members", "new-members", "lapsed", "offer"]) {
      expect(within(section).getByTestId(id).textContent, id).toContain("—");
    }
    expect(within(section).queryByTestId("revenue-chart")).toBeNull();
  });
});
