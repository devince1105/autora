// @vitest-environment jsdom
// T-515: the public site — article and list rendering, the reader beacon, the public API client.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchArticle, fetchArticles, type PublicArticle } from "./api";
import { ArticleList } from "./ArticleList";
import { ArticleView } from "./ArticleView";
import { MemberBadge } from "./MemberBadge";
import { formatDate, isLang } from "./i18n";
import { sendBeacon, sessionHash, type SessionStore } from "./session";

afterEach(cleanup);

const ARTICLE: PublicArticle = {
  article_id: "0192aaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa",
  lang: "zh-TW",
  slug: "lumen-city-microgrid-a1b2c3",
  path: "/news/zh-TW/articles/lumen-city-microgrid-a1b2c3",
  title: "流明市首座社區微電網啟用",
  summary: "港區 1,200 組屋頂太陽能板串成微電網。",
  published_at: "2026-09-19T04:00:00Z",
  company: "流明日報（示範）",
  company_id: "0192bbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb",
  company_slug: "lumen-daily",
  access: "free",
  locked: false,
  blocks: [
    { type: "heading", text: "重點" },
    { type: "paragraph", text: "微電網串連 1,200 組屋頂太陽能板。" },
    { type: "quote", text: "停電時可以撐六小時。" },
  ],
  sources: [
    { title: "Lumen City switches on its first microgrid", site: "news.fixtures.autora.test", url: "https://news.fixtures.autora.test/a" },
    { title: "流明市港區社區微電網啟用", site: "city.fixtures.autora.test", url: "https://city.fixtures.autora.test/b" },
  ],
  langs: {
    "zh-TW": "/news/zh-TW/articles/lumen-city-microgrid-a1b2c3",
    en: "/news/en/articles/lumen-city-microgrid-a1b2c3",
  },
};

const MEMBERS_ONLY: PublicArticle = {
  ...ARTICLE,
  access: "members",
  locked: true,
  blocks: ARTICLE.blocks.slice(0, 1),
  sources: [],
};

class Store implements SessionStore {
  data = new Map<string, string>();
  getItem(key: string) {
    return this.data.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.data.set(key, value);
  }
}

describe("the article page", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the text, the date, the sources and the other language", () => {
    render(<ArticleView article={ARTICLE} lang="zh-TW" />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("流明市首座社區微電網啟用");
    expect(screen.getByRole("heading", { level: 2, name: "重點" })).toBeTruthy();
    expect(screen.getByText("微電網串連 1,200 組屋頂太陽能板。").tagName).toBe("P");
    expect(screen.getByText("停電時可以撐六小時。").tagName).toBe("BLOCKQUOTE");
    expect(screen.getByText(/艾矽鯨・/)).toBeTruthy(); // the site's name, not the company's
    expect(screen.queryByText(/流明日報（示範）/)).toBeNull();
    expect(screen.getByText(formatDate("zh-TW", ARTICLE.published_at)).tagName).toBe("TIME");
    const sources = within(screen.getByRole("heading", { name: "資料來源" }).parentElement!);
    const links = sources.getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(ARTICLE.sources.map((s) => s.url));
    expect(links[0]!.getAttribute("rel")).toContain("nofollow");
    const english = screen.getByRole("link", { name: "English" });
    expect(english.getAttribute("href")).toBe(ARTICLE.langs.en);
    expect(english.getAttribute("hreflang")).toBe("en");
  });

  it("a corrected article says when it was updated (D-045)", () => {
    render(<ArticleView article={{ ...ARTICLE, revised_at: "2026-09-25T02:00:00Z" }} lang="zh-TW" />);
    expect(document.body.textContent).toContain("更新於");
    cleanup();
    render(<ArticleView article={ARTICLE} lang="zh-TW" />);
    expect(document.body.textContent).not.toContain("更新於");
  });

  it("counts a view when it opens", () => {
    render(<ArticleView article={ARTICLE} lang="zh-TW" />);
    const fetch = vi.mocked(globalThis.fetch);
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0]!;
    expect(String(url)).toMatch(/\/api\/analytics\/beacon$/);
    const body = JSON.parse(String(init!.body));
    expect(body).toMatchObject({ article_id: ARTICLE.article_id, lang: "zh-TW", event_type: "view" });
    expect(body.session_hash).toMatch(/^[0-9a-f]{32}$/);
    expect(init!.keepalive).toBe(true);
  });

  it("counts a completed read when the end of the article is reached, once", () => {
    let fire: (visible: boolean) => void = () => undefined;
    const disconnect = vi.fn();
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
          fire = (visible) => callback([{ isIntersecting: visible }]);
        }
        observe() {}
        disconnect = disconnect;
      },
    );
    render(<ArticleView article={ARTICLE} lang="zh-TW" />);
    fire(false);
    fire(true);
    const kinds = vi.mocked(globalThis.fetch).mock.calls.map(([, init]) => JSON.parse(String(init!.body)).event_type);
    expect(kinds).toEqual(["view", "read_complete"]);
    expect(disconnect).toHaveBeenCalled();
  });

  it("a members-only article shows its opening and both ways in (D-025)", () => {
    render(<ArticleView article={MEMBERS_ONLY} lang="zh-TW" />);

    expect(screen.getByText(MEMBERS_ONLY.blocks[0].text)).toBeTruthy();
    expect(screen.queryByText(ARTICLE.blocks[2].text)).toBeNull();
    const notice = screen.getByTestId("members-only");
    expect(notice.textContent).toContain("NT$330"); // which dollar, said once and from one place
    expect(notice.textContent).toContain("NT$30");
    expect(notice.textContent).toContain("已經是會員？");
    const link = within(notice).getByRole("link", { name: "登入" });
    expect(link.getAttribute("href")).toBe(`/news/zh-TW/login?next=${encodeURIComponent(ARTICLE.path)}`);
  });

  it("the become-a-member button asks this article's company for a checkout (T-702)", async () => {
    render(<ArticleView article={MEMBERS_ONLY} lang="zh-TW" />);
    const notice = screen.getByTestId("members-only");
    expect(within(notice).queryByRole("status")).toBeNull();

    fireEvent.click(within(notice).getByRole("button", { name: "選擇年繳" }));

    const started = await vi.waitFor(() =>
      vi.mocked(globalThis.fetch).mock.calls.find(([url]) => String(url).endsWith("/api/checkout")),
    );
    expect(JSON.parse(String(started![1]!.body))).toEqual({ lang: "zh-TW", company: "lumen-daily", interval: "year" });
  });

  it("a free article says nothing about membership", () => {
    render(<ArticleView article={ARTICLE} lang="zh-TW" />);
    expect(screen.queryByTestId("members-only")).toBeNull();
  });
});

describe("the header's member badge", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  function answer(body: unknown) {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
  }

  it("offers a way in when nobody is signed in", async () => {
    answer(null);
    render(<MemberBadge lang="zh-TW" />);
    const link = await screen.findByTestId("sign-in");
    expect(link.getAttribute("href")).toBe("/news/zh-TW/login");
  });

  it("says when the membership runs out", async () => {
    answer({ reader_id: "r", email: "reader@example.com", member_until: "2027-09-23T00:00:00Z" });
    render(<MemberBadge lang="zh-TW" />);
    const badge = await screen.findByTestId("member-badge");
    expect(badge.textContent).toContain("會員");
    expect(badge.textContent).toContain(formatDate("zh-TW", "2027-09-23T00:00:00Z"));
  });

  it("a signed-in reader who has not paid is not called a member", async () => {
    answer({ reader_id: "r", email: "reader@example.com", member_until: null });
    render(<MemberBadge lang="zh-TW" />);
    const badge = await screen.findByTestId("member-badge");
    expect(badge.textContent).toContain("reader@example.com");
    expect(badge.textContent).not.toContain("會員");
  });

  it("a membership that has already run out is not a membership", async () => {
    answer({ reader_id: "r", email: "reader@example.com", member_until: "2020-01-01T00:00:00Z" });
    render(<MemberBadge lang="zh-TW" />);
    const badge = await screen.findByTestId("member-badge");
    expect(badge.textContent).not.toContain("會員");
  });
});

describe("the front page", () => {
  it("lists the stories, or says there are none", () => {
    const { rerender } = render(<ArticleList articles={[ARTICLE]} lang="en" />);
    const link = screen.getByRole("link", { name: ARTICLE.title });
    expect(link.getAttribute("href")).toBe(ARTICLE.path);
    rerender(<ArticleList articles={[]} lang="en" />);
    expect(screen.getByText("No stories yet.")).toBeTruthy();
  });
});

describe("the reader's session id", () => {
  const random = (byte: number) => () => new Uint8Array(16).fill(byte);

  it("is random, kept for the day and replaced the next day", () => {
    const store = new Store();
    const monday = new Date("2026-09-21T09:00:00Z");
    const first = sessionHash(monday, store, random(0xab));
    expect(first).toBe("ab".repeat(16));
    expect(sessionHash(new Date("2026-09-21T23:00:00Z"), store, random(0x01))).toBe(first);
    expect(sessionHash(new Date("2026-09-22T00:01:00Z"), store, random(0x02))).toBe("02".repeat(16));
  });

  it("works without storage, and ignores a corrupted one", () => {
    expect(sessionHash(new Date(), null, random(0x03))).toBe("03".repeat(16));
    const broken = new Store();
    broken.setItem("autora.site.session", "{not json");
    expect(sessionHash(new Date(), broken, random(0x04))).toBe("04".repeat(16));
    const blocked: SessionStore = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => undefined,
    };
    expect(sessionHash(new Date(), blocked, random(0x05))).toBe("05".repeat(16));
  });

  it("a lost beacon is not an error", async () => {
    const failing = vi.fn(() => Promise.reject(new Error("offline")));
    expect(() =>
      sendBeacon({ article_id: "a", lang: "en", event_type: "view", session_hash: "0".repeat(32) }, failing),
    ).not.toThrow();
    await Promise.resolve();
    expect(failing).toHaveBeenCalledOnce();
  });
});

describe("the public API client", () => {
  const json = (body: unknown, status = 200) =>
    vi.fn<typeof fetch>(() =>
      Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })),
    );

  it("reads an article, and returns null when it is not published", async () => {
    const found = json(ARTICLE);
    expect(await fetchArticle("zh-TW", ARTICLE.slug, { baseUrl: "http://api", fetch: found })).toEqual(ARTICLE);
    const request = found.mock.calls[0]![0] as Request;
    expect(request.url).toBe(`http://api/api/public/articles/zh-TW/${ARTICLE.slug}`);
    expect(request.headers.get("Authorization")).toBeNull(); // readers have no token
    expect(await fetchArticle("zh-TW", "gone", { baseUrl: "http://api", fetch: json({ title: "Not Found" }, 404) })).toBeNull();
    await expect(fetchArticle("zh-TW", "x", { baseUrl: "http://api", fetch: json({ title: "Boom" }, 500) })).rejects.toThrow("500");
  });

  it("lists articles for a language (and a company)", async () => {
    const listed = json([ARTICLE]);
    expect(await fetchArticles("en", { baseUrl: "http://api", fetch: listed, company: "demo" })).toHaveLength(1);
    const request = listed.mock.calls[0]![0] as Request;
    expect(request.url).toBe("http://api/api/public/articles?lang=en&company=demo");
  });

  it("knows its languages", () => {
    expect(isLang("zh-TW") && isLang("en")).toBe(true);
    expect(isLang("ja") || isLang("zh-CN")).toBe(false);
  });
});
