// @vitest-environment jsdom
// D-048: the market strip — what each figure says, the Taiwanese colours, and staying out of the
// way when there is nothing to show.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchMarkets, type PublicQuote } from "./api";
import { advance } from "./drift";
import { formatChange, formatValue, MarketStrip } from "./MarketStrip";
import { SiteFooter } from "./SiteFooter";

afterEach(cleanup);

const q = (over: Partial<PublicQuote>): PublicQuote => ({
  key: "taiex",
  value: 48024.6,
  change: -132.69,
  change_pct: -0.28,
  as_of: "2026-09-24",
  basis: "close",
  source: "TWSE",
  ...over,
});

const QUOTES = [
  q({}),
  q({ key: "tw:2454", value: 1650, change: 15, change_pct: 0.92 }),
  q({ key: "nasdaq", value: 27054.06, change: 115.83, change_pct: 0.43, basis: "prev_close", source: "FRED" }),
  q({ key: "us10y", value: 4.12, change: -0.03, change_pct: null, basis: "prev_close", source: "FRED" }),
  q({ key: "btc", value: 83268, change: -994.1, change_pct: -1.18, as_of: "2026-09-25", basis: "24h", source: "CoinGecko" }),
];

describe("the market strip", () => {
  it("names each figure in the reader's language, with its change and its day", () => {
    render(<MarketStrip quotes={QUOTES} lang="zh-TW" />);
    const items = within(screen.getByTestId("market-strip"))
      .getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "加權指數48,024.60−0.28%↓",
      "聯發科2454.TW1,650+0.92%↑",
      "那斯達克27,054.06+0.43%↑",
      "美國10年期公債4.12%−0.03↓",
      "比特幣83,268−1.18%↓",
    ]);
    expect(items[0]!.getAttribute("title")).toBe("收盤 9/24・TWSE");
    expect(items[2]!.getAttribute("title")).toBe("前一交易日收盤 9/24・FRED");
    expect(items[4]!.getAttribute("title")).toContain("24 小時漲跌");
  });

  it("is red when it rises and green when it falls, as in Taiwan", () => {
    render(<MarketStrip quotes={QUOTES.slice(0, 2)} lang="en" />);
    const [down, up] = within(screen.getByTestId("market-strip"))
      .getAllByRole("listitem");
    expect(up!.textContent).toContain("MediaTek2454.TW");
    expect(down!.querySelector(".text-fall")).toBeTruthy();
    expect(up!.querySelector(".text-rise")).toBeTruthy();

  });

  it("is not there at all when there are no figures", () => {
    const { container } = render(<MarketStrip quotes={[]} lang="zh-TW" />);
    expect(container.innerHTML).toBe("");
  });

  it("shows a US stock like a Taiwan one: its name and its code", () => {
    const nvda = q({ key: "us:NVDA", value: 224.53, change: 2.25, change_pct: 1.01, basis: "last", source: "Finnhub" });
    render(<MarketStrip quotes={[nvda]} lang="zh-TW" />);
    const item = within(screen.getByTestId("market-strip")).getByRole("listitem");
    expect(item.textContent).toBe("輝達NVDA224.53+1.01%↑");
    expect(item.getAttribute("title")).toBe("最新價 9/24・Finnhub");
    cleanup();
    render(<MarketStrip quotes={[nvda]} lang="en" />);
    expect(within(screen.getByTestId("market-strip")).getByRole("listitem").textContent).toBe("NVDA224.53+1.01%↑");
  });

  it("formats a figure for what it is", () => {
    expect(formatValue(q({ key: "tw:2330", value: 2475 }), "zh-TW")).toBe("2,475");
    expect(formatValue(q({ key: "tw:2317", value: 245.5 }), "zh-TW")).toBe("245.50");
    expect(formatValue(q({ key: "us10y", value: 4.1 }), "en")).toBe("4.10%");
    expect(formatChange(q({ change: null, change_pct: null }))).toBeNull();
  });

  it("the footer credits whose figures are shown, and nobody when none are", () => {
    const operator = { brand: "Nanguado", owner: null, email: "service@example.test", phone: null };
    const { rerender } = render(<SiteFooter lang="zh-TW" operator={operator} marketSources={["TWSE", "CoinGecko"]} />);
    const footer = screen.getByTestId("site-footer").textContent;
    expect(footer).toContain("市場資料：臺灣證券交易所、CoinGecko。");
    expect(footer).not.toContain("FRED");
    rerender(<SiteFooter lang="zh-TW" operator={operator} />);
    expect(screen.getByTestId("site-footer").textContent).not.toContain("CoinGecko");
  });

  it("a market service being down never takes the page with it", async () => {
    const down = vi.fn<typeof fetch>(() => Promise.reject(new Error("offline")));
    expect(await fetchMarkets({ baseUrl: "http://api", fetch: down })).toEqual([]);
    const listed = vi.fn<typeof fetch>(() =>
      Promise.resolve(new Response(JSON.stringify(QUOTES), { headers: { "Content-Type": "application/json" } })),
    );
    expect(await fetchMarkets({ baseUrl: "http://api", fetch: listed })).toHaveLength(5);
    expect((listed.mock.calls[0]![0] as Request).url).toBe("http://api/api/public/markets");
  });
});

describe("the strip's drift", () => {
  it("moves at its speed and wraps where the second copy begins", () => {
    expect(advance(0, 1, 1000)).toBe(30);
    expect(advance(990, 1, 1000)).toBe(20); // past one copy: back to the same place in the first
    expect(advance(10, 0.5, 0)).toBe(10); // nothing to loop over: stays
  });

  it("does not draw a second copy when the figures already fit", () => {
    render(<MarketStrip quotes={QUOTES} lang="zh-TW" />);
    // jsdom lays nothing out: every width is 0, so nothing overflows
    expect(within(screen.getByTestId("market-strip")).getAllByRole("listitem", { hidden: true })).toHaveLength(QUOTES.length);
  });
});
