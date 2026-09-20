// @vitest-environment jsdom
// T-515: the public site — article and list rendering, the reader beacon, the public API client.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchArticle, fetchArticles, type PublicArticle } from "./api";
import { ArticleList } from "./ArticleList";
import { ArticleView } from "./ArticleView";
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
    expect(screen.getByText(/流明日報（示範）/)).toBeTruthy();
    expect(screen.getByText(formatDate("zh-TW", ARTICLE.published_at)).tagName).toBe("TIME");
    const sources = within(screen.getByRole("heading", { name: "資料來源" }).parentElement!);
    const links = sources.getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(ARTICLE.sources.map((s) => s.url));
    expect(links[0]!.getAttribute("rel")).toContain("nofollow");
    const english = screen.getByRole("link", { name: "English" });
    expect(english.getAttribute("href")).toBe(ARTICLE.langs.en);
    expect(english.getAttribute("hreflang")).toBe("en");
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
