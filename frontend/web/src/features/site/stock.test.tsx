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
  quote: {
    key: "us:NVDA",
    value: 225.17,
    change: 0.59,
    change_pct: 0.26,
    as_of: "2026-09-25",
    basis: "last",
    source: "Finnhub",
    open: 225.26,
    high: 226.94,
    low: 223.13,
    previous_close: 224.58,
    market_cap: 5.412e12,
    currency: "USD",
  },
  holders: [
    holder({}),
    holder({ investor: "麥可・貝瑞", filer: "Scion", change: "new", put_call: "PUT", shares: 1000000, previous_shares: 0 }),
  ],
  trades: [
    {
      person: "川普",
      kind: "sale",
      traded_on: "2026-02-05",
      amount_min: 250001,
      amount_max: 500000,
      amount_text: "$250,001 - $500,000",
      late: true,
      received_on: "2026-05-12",
      report_url: "https://extapps2.oge.gov/r.pdf#page=2",
      option: false,
    },
    {
      person: "川普",
      kind: "purchase",
      traded_on: "2026-01-26",
      amount_min: 50000001,
      amount_max: null,
      amount_text: "Over $50,000,000",
      late: false,
      received_on: "2026-05-12",
      report_url: "https://extapps2.oge.gov/r.pdf#page=3",
      option: false,
    },
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

  it("gives the day's figures and the market value, as the watch cards do", () => {
    render(<StockView stock={NVDA} lang="zh-TW" />);
    const card = document.querySelector("article header dl")!;
    expect(card.textContent).toBe("開盤225.26最高226.94最低223.13前收224.58總市值US$5.4兆");
    cleanup();
    // an ETF: its day, and no market value; an index or nothing at all: no card
    const etf = { ...NVDA.quote!, key: "tw:0050", market_cap: null, currency: "TWD" };
    render(<StockView stock={{ ...NVDA, quote: etf }} lang="zh-TW" />);
    expect(document.querySelector("article header dl")!.textContent).not.toContain("總市值");
    cleanup();
    const bare = { ...NVDA.quote!, open: null, high: null, low: null, previous_close: null, market_cap: null };
    render(<StockView stock={{ ...NVDA, quote: bare }} lang="en" />);
    expect(document.querySelector("article header dl")).toBeNull();
  });

  it("lists public figures' trades, as ranges, each with the page of its report", () => {
    render(<StockView stock={NVDA} lang="zh-TW" />);
    const [sold, bought] = screen.getAllByTestId("trade");
    expect(sold!.textContent).toContain("川普賣出US$250,001 – US$500,000");
    expect(sold!.textContent).toContain("逾 30 天才申報");
    expect(within(sold!).getByRole("link").getAttribute("href")).toBe("https://extapps2.oge.gov/r.pdf#page=2");
    expect(bought!.textContent).toContain("買進US$50,000,000 以上");
    expect(document.body.textContent).toContain("經人工對照原件核准後才顯示");
    cleanup();
    render(<StockView stock={{ ...NVDA, trades: [] }} lang="zh-TW" />);
    // not compiled here: pointed to the trackers that publish them (D-052)
    expect(document.body.textContent).toContain("艾矽鯨不自行整理");
    const links = within(screen.getByTestId("trackers")).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "https://open-cabinet.org/officials/trump-donald-j",
      "https://www.capitoltrades.com/politicians/P000197",
    ]);
    expect(links[0]!.getAttribute("rel")).toContain("nofollow");
  });

  it("a member's spouse's option is shown as the spouse's, and as an option", () => {
    const option = {
      person: "佩洛西",
      kind: "purchase",
      traded_on: "2026-07-24",
      amount_min: 1000001,
      amount_max: 5000000,
      amount_text: "$1,000,001 - $5,000,000",
      late: null,
      received_on: "2026-08-21",
      report_url: "https://disclosures-clerk.house.gov/r.pdf#page=1",
      owner: "SP",
      option: true,
      note: "Purchased 50 call options with a strike price of $100.",
    };
    render(<StockView stock={{ ...NVDA, trades: [option] }} lang="zh-TW" />);
    const [trade] = screen.getAllByTestId("trade");
    expect(trade!.textContent).toContain("佩洛西（配偶）買進選擇權US$1,000,001 – US$5,000,000");
    expect(trade!.textContent).toContain("Purchased 50 call options with a strike price of $100.");
    expect(trade!.textContent).not.toContain("逾 30 天");
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
