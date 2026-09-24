// @vitest-environment jsdom
// T-702: the paywall's button, from "become a member" to PAYUNi's payment page.
//
// The point of most of these is what the browser does *not* do: it never sees a secret, it never
// grants anything, and a site with no store yet says so instead of failing silently.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CheckoutError, fetchOffer, formatOffer, goToPaymentPage, startCheckout } from "./checkout";
import { MembersOnly } from "./MembersOnly";

const PAGE = {
  order_id: "0192cccc-cccc-7ccc-8ccc-cccccccccccc",
  mer_trade_no: "AU0192CCCCCCCC7CCC",
  amount: "360.000000",
  currency: "TWD",
  url: "https://sandbox-api.payuni.com.tw/api/upp",
  fields: { MerID: "SHOP123", Version: "1.0", EncryptInfo: "abcd1234", HashInfo: "F00D" },
};

const OFFER = { amount: "360.000000", currency: "TWD", interval: "year", available: true };

function respond(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
}

afterEach(() => {
  cleanup();
  document.body.innerHTML = ""; // the forms are appended to the body, not rendered by React
  vi.restoreAllMocks();
});

describe("what a year costs", () => {
  it("is what the API says, not what the page was written with", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValue(respond({ ...OFFER, amount: "480.000000" }));
    const offer = await fetchOffer("lumen");
    expect(String(fetchMock.mock.calls[0][0])).toContain("interval=year");
    expect(offer).not.toBeNull();
    expect(formatOffer(offer!, "zh-TW")).toBe("NT$480");
  });

  it("is nothing at all when the site has nothing for sale", async () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(respond({ ...OFFER, available: false }));
    expect(await fetchOffer()).toBeNull();
  });

  it("is nothing at all when the API cannot be reached, rather than an error", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    expect(await fetchOffer()).toBeNull();
  });

  it("says which dollar it is", () => {
    expect(formatOffer(OFFER, "zh-TW")).toBe("NT$360");
    expect(formatOffer(OFFER, "en")).toBe("NT$360");
    expect(formatOffer({ ...OFFER, currency: "USD", amount: "12" }, "en")).toBe("USD 12");
  });
});

describe("starting a checkout", () => {
  it("asks for an order and hands back the form", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValue(respond(PAGE, 201));
    const page = await startCheckout("zh-TW", "lumen");
    expect(page.url).toBe(PAGE.url);
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.credentials).toBe("include");
    expect(JSON.parse(String(init?.body))).toEqual({ lang: "zh-TW", company: "lumen", interval: "year" });
  });

  it("asks for a month when a month is chosen (D-034)", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValue(respond(PAGE, 201));
    await startCheckout("zh-TW", undefined, "month");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body)).interval).toBe("month");
  });

  it.each([
    [401, "signed-out"],
    [404, "unavailable"],
    [503, "unavailable"],
    [500, "failed"],
  ])("turns %i into %s", async (status, problem) => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(respond({}, status));
    await expect(startCheckout("zh-TW")).rejects.toMatchObject({ problem });
  });

  it("is a failure, not a crash, when the network is gone", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    await expect(startCheckout("zh-TW")).rejects.toBeInstanceOf(CheckoutError);
  });
});

describe("going to the payment page", () => {
  it("posts the sealed fields as a form, because the reader has to land there", () => {
    const submit = vi.fn();
    vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(submit);
    goToPaymentPage(PAGE);

    const form = document.querySelector("form");
    expect(form?.method).toBe("post");
    expect(form?.action).toBe(PAGE.url);
    const sent = Object.fromEntries(
      Array.from(form!.querySelectorAll("input")).map((i) => [i.name, i.value]),
    );
    expect(sent).toEqual(PAGE.fields);
    expect(submit).toHaveBeenCalledOnce();
  });
});

describe("the paywall", () => {
  beforeEach(() => {
    vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => undefined);
  });

  const MONTH = { ...OFFER, amount: "30.000000", interval: "month" };

  /** The API: ``year`` for the yearly offer, ``month`` for the monthly one (unavailable if null). */
  function mockCalls(
    year: unknown,
    checkout: () => Promise<Response>,
    month: unknown = { ...MONTH, available: false },
  ) {
    return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (!url.includes("/api/checkout/offer")) return checkout();
      return respond(url.includes("interval=month") ? month : year);
    });
  }

  it("shows the price the API charges", async () => {
    mockCalls({ ...OFFER, amount: "480.000000" }, () => respond(PAGE, 201));
    render(<MembersOnly lang="zh-TW" path="/news/zh-TW/articles/x" company="lumen" />);
    await waitFor(() => expect(screen.getByTestId("members-only").textContent).toContain("NT$480"));
  });

  it("offers a month and a year side by side, and says what a year saves (D-034)", async () => {
    mockCalls({ ...OFFER, amount: "330.000000" }, () => respond(PAGE, 201), MONTH);
    render(<MembersOnly lang="zh-TW" path="/news/zh-TW/articles/x" company="lumen" />);
    await waitFor(() => expect(screen.getByTestId("plan-month").textContent).toContain("NT$30"));
    expect(screen.getByTestId("plan-year").textContent).toContain("NT$330");
    expect(screen.getByTestId("plan-year").textContent).toContain("比月繳省 8%");
  });

  it("leaves out a plan the API does not sell, rather than show a price nobody can pay", async () => {
    mockCalls(OFFER, () => respond(PAGE, 201));
    render(<MembersOnly lang="zh-TW" path="/news/zh-TW/articles/x" company="lumen" />);
    await waitFor(() => expect(screen.queryByTestId("plan-month")).toBeNull());
    expect(screen.getByTestId("plan-year")).toBeTruthy();
  });

  it("orders the plan that was chosen", async () => {
    const fetchMock = mockCalls(OFFER, () => respond(PAGE, 201), MONTH);
    render(<MembersOnly lang="zh-TW" path="/news/zh-TW/articles/x" company="lumen" />);
    fireEvent.click(await screen.findByRole("button", { name: "選擇月繳" }));
    await waitFor(() => expect(document.querySelector("form")).not.toBeNull());
    const order = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/checkout"));
    expect(JSON.parse(String(order![1]!.body)).interval).toBe("month");
  });

  it("says what paying agrees to, with the policies a click away", () => {
    mockCalls(OFFER, () => respond(PAGE, 201));
    render(<MembersOnly lang="zh-TW" path="/news/zh-TW/articles/x" />);
    const notice = screen.getByTestId("members-only");
    expect(notice.textContent).toContain("不會自動扣款");
    expect(screen.getByRole("link", { name: "服務條款" }).getAttribute("href")).toBe("/news/zh-TW/terms");
    expect(screen.getByRole("link", { name: "退款政策" }).getAttribute("href")).toBe("/news/zh-TW/refund");
  });

  it("takes the reader to PAYUNi", async () => {
    mockCalls(OFFER, () => respond(PAGE, 201));
    render(<MembersOnly lang="zh-TW" path="/news/zh-TW/articles/x" company="lumen" />);
    fireEvent.click(screen.getByRole("button", { name: "選擇年繳" }));
    await waitFor(() => expect(document.querySelector("form")?.action).toBe(PAGE.url));
  });

  it("says so when the site has no store yet, instead of a dead end", async () => {
    mockCalls({ ...OFFER, available: false }, () => respond({}, 503));
    render(<MembersOnly lang="zh-TW" path="/news/zh-TW/articles/x" />);
    fireEvent.click(screen.getByRole("button", { name: "選擇年繳" }));
    expect((await screen.findByRole("status")).textContent).toContain("即將開放");
  });

  it("says try again when the payment page could not be opened", async () => {
    mockCalls(OFFER, () => respond({}, 500));
    render(<MembersOnly lang="zh-TW" path="/news/zh-TW/articles/x" />);
    fireEvent.click(screen.getByRole("button", { name: "選擇年繳" }));
    expect((await screen.findByRole("status")).textContent).toContain("請稍後再試");
  });

  it("sends somebody who is not signed in to sign in first", async () => {
    mockCalls(OFFER, () => respond({}, 401));
    const assign = vi.fn();
    Object.defineProperty(window, "location", { value: { assign }, writable: true });
    render(<MembersOnly lang="zh-TW" path="/news/zh-TW/articles/x" />);
    fireEvent.click(screen.getByRole("button", { name: "選擇年繳" }));
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(
        "/news/zh-TW/login?next=%2Fnews%2Fzh-TW%2Farticles%2Fx",
      ),
    );
  });

  it("never puts a secret in the page", async () => {
    mockCalls(OFFER, () => respond(PAGE, 201));
    render(<MembersOnly lang="en" path="/news/en/articles/x" company="lumen" />);
    fireEvent.click(screen.getByRole("button", { name: "Choose yearly" }));
    await waitFor(() => expect(document.querySelector("form")).not.toBeNull());
    const sent = Array.from(document.querySelectorAll("form input")).map((i) => i.getAttribute("name"));
    expect(sent).toEqual(["MerID", "Version", "EncryptInfo", "HashInfo"]);
  });
});
