// @vitest-environment jsdom
// D-049: a stock's page — its figure, the investors' 13F positions, our stories — and the strip's
// links to it.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchStock, type PublicHolder, type PublicStock } from "./api";
import { MarketStrip } from "./MarketStrip";
import { stockPage } from "./quote";
import { StockView } from "./StockView";

afterEach(cleanup);

const holder = (over: Partial<PublicHolder>): PublicHolder => ({
  investor: "段永平",
  filer: "H&H International Investment, LLC",
  period: "2026-06-30",
  previous_period: "2026-03-31",
  change: "decreased",
  title_of_class: "COM",
  put_call: "",
  shares: 6280675,
  previous_shares: 13843775,
  value_usd: 1256700261,
  portfolio_pct: 6.58,
  filing_url: "https://www.sec.gov/Archives/edgar/data/1759760/000175976026000007/0001759760-26-000007-index.htm",
  ...over,
});

const NVDA: PublicStock = {
  symbol: "NVDA",
  market: "us",
  name: "輝達",
  quote: { key: "us:NVDA", value: 225.17, change: 0.59, change_pct: 0.26, as_of: "2026-09-25", basis: "last", source: "Finnhub" },
  holders: [
    holder({}),
    holder({ investor: "麥可・貝瑞", filer: "Scion", change: "new", put_call: "PUT", shares: 1000000, previous_shares: 0 }),
  ],
  articles: [
    {
      article_id: "a1",
      lang: "zh-TW",
      slug: "hh",
      path: "/news/zh-TW/articles/hh",
      title: "段永平 H&H 最新 13F：減持輝達逾五成",
      summary: null,
      published_at: "2026-09-25T00:00:00Z",
      access: "free",
      section: "holdings",
    },
  ],
};

describe("a stock's page", () => {
  it("shows its figure, who holds it and what they did, and our stories", () => {
    render(<StockView stock={NVDA} lang="zh-TW" />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("輝達NVDA");
    expect(document.body.textContent).toContain("225.17+0.26% ↑");
    const [duan, burry] = screen.getAllByTestId("holder");
    expect(duan!.textContent).toContain("段永平減碼");
    expect(duan!.textContent).toContain("前季 13,843,775");
    expect(duan!.textContent).toContain("6.58%");
    expect(within(duan!).getByRole("link", { name: /申報/ }).getAttribute("href")).toBe(NVDA.holders[0]!.filing_url);
    expect(screen.getByRole("link", { name: /減持輝達逾五成/ }).getAttribute("href")).toBe("/news/zh-TW/articles/hh");
    expect(document.body.textContent).toContain("不構成投資建議");
    // an option is a bet on the stock, not a holding of it
    expect(burry!.textContent).toContain("新建倉・賣權（看跌）");
    expect(burry!.textContent).toContain("標的股數");
    expect(burry!.querySelector(".text-rise")).toBeNull();
  });

  it("a Taiwan stock says what 13F does and does not cover; an empty one says so", () => {
    render(<StockView stock={{ ...NVDA, symbol: "2330", market: "tw", name: "台積電", quote: null }} lang="zh-TW" />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("台積電2330.TW");
    expect(document.body.textContent).toContain("美國存託憑證（TSM）的持有人");
    expect(document.body.textContent).toContain("目前沒有報價。");
    cleanup();
    render(<StockView stock={{ ...NVDA, symbol: "2454", market: "tw", name: "聯發科", holders: [], articles: [] }} lang="zh-TW" />);
    expect(document.body.textContent).toContain("13F 只涵蓋美國上市證券");
    expect(document.body.textContent).toContain("還沒有提到這檔股票的報導。");
  });

  it("is asked of the API by symbol, and a symbol without a page is null", async () => {
    const found = vi.fn<typeof fetch>(() =>
      Promise.resolve(new Response(JSON.stringify(NVDA), { headers: { "Content-Type": "application/json" } })),
    );
    expect((await fetchStock("NVDA", "zh-TW", { baseUrl: "http://api", fetch: found, company: "c" }))?.name).toBe("輝達");
    expect((found.mock.calls[0]![0] as Request).url).toBe("http://api/api/public/stocks/NVDA?lang=zh-TW&company=c");
    const missing = vi.fn<typeof fetch>(() => Promise.resolve(new Response("{}", { status: 404 })));
    expect(await fetchStock("XYZ", "en", { baseUrl: "http://api", fetch: missing })).toBeNull();
  });
});

describe("the strip's way to a stock", () => {
  it("links a stock to its page, and nothing else", () => {
    expect(stockPage("us:NVDA", "en")).toBe("/news/en/stocks/NVDA");
    expect(stockPage("tw:0050", "zh-TW")).toBe("/news/zh-TW/stocks/0050");
    expect(stockPage("taiex", "zh-TW")).toBeNull();
    render(<MarketStrip quotes={[NVDA.quote!, { ...NVDA.quote!, key: "btc", source: "CoinGecko", basis: "24h" }]} lang="zh-TW" />);
    const items = within(screen.getByTestId("market-strip")).getAllByRole("listitem");
    expect(within(items[0]!).getByRole("link").getAttribute("href")).toBe("/news/zh-TW/stocks/NVDA");
    expect(within(items[1]!).queryByRole("link")).toBeNull();
  });
});
