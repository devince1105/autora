// @vitest-environment jsdom
// D-048: the market strip — what each figure says, the Taiwanese colours, and staying out of the
// way when there is nothing to show.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchMarkets, type PublicQuote } from "./api";
import { formatChange, formatValue, MarketStrip } from "./MarketStrip";
import { legalDoc } from "./legal";
import { SiteFooter } from "./SiteFooter";
import { QUOTES_SCRIPT, quotesConfig, US_TICKERS, UsStocks } from "./UsStocks";

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
  q({ key: "spx", value: 7725.55, change: 21.56, change_pct: 0.28, basis: "prev_close", source: "FRED" }),
  q({ key: "us10y", value: 4.12, change: -0.03, change_pct: null, basis: "prev_close", source: "FRED" }),
  q({ key: "btc", value: 83268, change: -994.1, change_pct: -1.18, as_of: "2026-09-25", basis: "24h", source: "CoinGecko" }),
];

describe("the market strip", () => {
  it("names each figure in the reader's language, with its change and its day", () => {
    render(<MarketStrip quotes={QUOTES} lang="zh-TW" />);
    const items = within(screen.getByTestId("market-strip"))
      .getAllByRole("listitem")
      .filter((li) => li.title); // ours; the US stocks come after
    expect(items.map((li) => li.textContent)).toEqual([
      "加權指數48,024.60▼−0.28%",
      "聯發科2454.TW1,650▲+0.92%",
      "標普5007,725.55▲+0.28%",
      "美國10年期公債4.12%▼−0.03",
      "比特幣83,268▼−1.18%",
    ]);
    expect(items[0]!.getAttribute("title")).toBe("收盤 9/24・TWSE");
    expect(items[2]!.getAttribute("title")).toBe("前一交易日收盤 9/24・FRED");
    expect(items[4]!.getAttribute("title")).toContain("24 小時漲跌");
  });

  it("is red when it rises and green when it falls, as in Taiwan", () => {
    render(<MarketStrip quotes={QUOTES.slice(0, 2)} lang="en" />);
    const [down, up] = within(screen.getByTestId("market-strip"))
      .getAllByRole("listitem")
      .filter((li) => li.title);
    expect(up!.textContent).toContain("MediaTek2454.TW");
    expect(down!.querySelector(".text-fall")).toBeTruthy();
    expect(up!.querySelector(".text-rise")).toBeTruthy();

  });

  it("has the US stocks in the same block, and keeps them when ours are missing", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    render(<MarketStrip quotes={[]} lang="zh-TW" />);
    const block = within(screen.getByTestId("market-strip"));
    expect(block.getByTestId("us-stocks")).toBeTruthy();
    expect(block.getByRole("link", { name: /TradingView/ }).getAttribute("rel")).toContain("nofollow");
    vi.unstubAllGlobals();
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

describe("the US stocks (TradingView)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("has the stocks asked for, named in the reader's language", () => {
    const names = US_TICKERS.map(([symbol]) => symbol.split(":")[1]);
    for (const wanted of ["NVDA", "AAPL", "GOOGL", "AMZN", "META", "MSFT", "TSLA", "AMD", "MU", "QQQ"]) {
      expect(names).toContain(wanted);
    }
    expect(quotesConfig("zh-TW", "dark").symbols[0]).toEqual({ proName: "NASDAQ:NVDA", title: "輝達" });
    // "regular" is TradingView's one-line tape (44px); its "compact" is two lines (72px)
    expect(quotesConfig("en", "light")).toMatchObject({
      locale: "en",
      colorTheme: "light",
      isTransparent: true,
      displayMode: "regular",
    });
    expect(quotesConfig("en", "light").symbols[0]!.title).toBe("NVDA");
  });

  it("loads TradingView's script with the site's language and light or dark, and credits it", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const site = document.createElement("div");
    site.setAttribute("data-site", "");
    site.setAttribute("data-theme", "dark");
    document.body.appendChild(site);
    const list = document.createElement("ul");
    site.appendChild(list);
    render(<UsStocks lang="zh-TW" />, { container: list });
    const script = screen.getByTestId("us-stocks").querySelector("script")!;
    expect(script.src).toBe(QUOTES_SCRIPT);
    expect(JSON.parse(script.text)).toMatchObject({ colorTheme: "dark", locale: "zh_TW" });
    expect(screen.getByRole("link", { name: /美股報價/ }).getAttribute("href")).toBe("https://www.tradingview.com/");
    site.remove();
  });

  it("the privacy policy tells readers about it", () => {
    const operator = { brand: "Nanguado", owner: null, email: "service@example.test", phone: null };
    for (const lang of ["zh-TW", "en"] as const) {
      expect(JSON.stringify(legalDoc("privacy", lang, operator))).toContain("TradingView");
    }
  });
});
