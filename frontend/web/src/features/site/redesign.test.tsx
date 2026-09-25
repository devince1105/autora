// @vitest-environment jsdom
// D-047: the public site's sections, pages, next article along, reading tools and light/dark.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchArticles, type PublicArticle, type PublicArticleSummary } from "./api";
import { ArticleList, listHref } from "./ArticleList";
import { ArticleView } from "./ArticleView";
import { isSection } from "./i18n";
import { applyTheme, currentTheme, THEME_KEY, THEME_SCRIPT } from "./theme";
import { ThemeToggle } from "./ThemeToggle";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function summary(n: number, over: Partial<PublicArticleSummary> = {}): PublicArticleSummary {
  return {
    article_id: `a${n}`,
    lang: "zh-TW",
    slug: `s${n}`,
    path: `/news/zh-TW/articles/s${n}`,
    title: `第 ${n} 篇`,
    summary: `摘要 ${n}`,
    published_at: "2026-09-19T04:00:00Z",
    access: "free",
    section: "holdings",
    ...over,
  };
}

const ARTICLE: PublicArticle = {
  ...summary(1),
  company: "艾矽鯨",
  company_id: "c",
  company_slug: "autora-finance",
  locked: false,
  blocks: [{ type: "paragraph", text: "段永平第二季出清台積電。" }],
  sources: [],
  langs: { "zh-TW": "/news/zh-TW/articles/s1" },
  newer: { title: "較新的那篇", path: "/news/zh-TW/articles/s0" },
  older: null,
};

describe("the front page", () => {
  it("leads with the newest story, then lists the rest", () => {
    render(<ArticleList articles={[summary(1), summary(2), summary(3)]} lang="zh-TW" />);
    const headlines = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headlines).toEqual(["第 1 篇", "第 2 篇", "第 3 篇"]);
    expect(screen.getByRole("heading", { level: 2, name: "第 1 篇" }).closest("article")).toBeTruthy();
    expect(screen.getAllByText("大戶持股")).toHaveLength(3 + 1); // each story's label and the tab
  });

  it("has a tab per section, the current one marked", () => {
    render(<ArticleList articles={[summary(1)]} lang="zh-TW" section="ai" />);
    const tabs = within(screen.getByRole("navigation", { name: "報導分類" })).getAllByRole("link");
    expect(tabs.map((t) => [t.textContent, t.getAttribute("href")])).toEqual([
      ["全部", "/news/zh-TW"],
      ["大戶持股", "/news/zh-TW?section=holdings"],
      ["AI 科技", "/news/zh-TW?section=ai"],
      ["台股", "/news/zh-TW?section=tw"],
      ["美股", "/news/zh-TW?section=us"],
      ["加密貨幣", "/news/zh-TW?section=crypto"],
    ]);
    expect(tabs.filter((t) => t.getAttribute("aria-current") === "page").map((t) => t.textContent)).toEqual(["AI 科技"]);
  });

  it("pages to older stories and back, in the same section", () => {
    const { rerender } = render(<ArticleList articles={[summary(1)]} lang="zh-TW" section="tw" hasMore />);
    expect(screen.getByRole("link", { name: "較舊的報導 →" }).getAttribute("href")).toBe("/news/zh-TW?section=tw&page=2");
    expect(screen.queryByRole("link", { name: "← 較新的報導" })).toBeNull();
    rerender(<ArticleList articles={[summary(11)]} lang="zh-TW" section="tw" page={2} />);
    expect(screen.getByRole("link", { name: "← 較新的報導" }).getAttribute("href")).toBe("/news/zh-TW?section=tw");
    expect(screen.queryByRole("link", { name: "較舊的報導 →" })).toBeNull();
    expect(screen.getByText("第 2 頁")).toBeTruthy();
    expect(document.querySelector("article")).toBeNull(); // only the first page leads with one
    cleanup();
    render(<ArticleList articles={[summary(1)]} lang="en" />);
    expect(screen.queryByText("Page 1")).toBeNull(); // one page: nothing to page through
  });

  it("marks members-only stories, and knows its sections", () => {
    render(<ArticleList articles={[summary(1, { access: "members", section: null })]} lang="en" />);
    expect(screen.getByText("Member")).toBeTruthy();
    expect(listHref("en", null, 3)).toBe("/news/en?page=3");
    expect(isSection("ai") && !isSection("nft") && !isSection(undefined)).toBe(true);
  });

  it("asks the API for a section and a page", async () => {
    const listed = vi.fn<typeof fetch>(() =>
      Promise.resolve(new Response("[]", { headers: { "Content-Type": "application/json" } })),
    );
    await fetchArticles("zh-TW", { baseUrl: "http://api", fetch: listed, section: "us", limit: 11, offset: 10 });
    expect((listed.mock.calls[0]![0] as Request).url).toBe(
      "http://api/api/public/articles?lang=zh-TW&section=us&limit=11&offset=10",
    );
  });
});

describe("the article page", () => {
  it("says where it is, and leads on to the next article along", () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))));
    render(<ArticleView article={ARTICLE} lang="zh-TW" />);
    const crumbs = within(screen.getByRole("navigation", { name: "breadcrumb" })).getAllByRole("link");
    expect(crumbs.map((a) => a.getAttribute("href"))).toEqual(["/news/zh-TW", "/news/zh-TW?section=holdings"]);
    const newer = screen.getByRole("link", { name: /較新一篇/ });
    expect(newer.getAttribute("href")).toBe("/news/zh-TW/articles/s0");
    expect(newer.textContent).toContain("較新的那篇");
    expect(screen.queryByText(/較舊一篇/)).toBeNull();
    expect(screen.getByRole("button", { name: /列印/ })).toBeTruthy();
  });

  it("reads itself aloud, a paragraph at a time, where the browser can", () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))));
    const spoken: { text: string; lang: string }[] = [];
    const synth = { speak: vi.fn((u) => spoken.push({ text: u.text, lang: u.lang })), cancel: vi.fn(), getVoices: () => [] };
    vi.stubGlobal("speechSynthesis", synth);
    vi.stubGlobal(
      "SpeechSynthesisUtterance",
      class {
        lang = "";
        constructor(public text: string) {}
      },
    );
    render(<ArticleView article={ARTICLE} lang="zh-TW" />);
    fireEvent.click(screen.getByRole("button", { name: /朗讀/ }));
    expect(spoken.map((s) => s.text)).toEqual(["第 1 篇", "摘要 1", "段永平第二季出清台積電。"]);
    expect(spoken.every((s) => s.lang === "zh-TW")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /停止朗讀/ }));
    expect(synth.cancel).toHaveBeenCalled();
  });
});

describe("light and dark", () => {
  it("follows the system until the reader picks, and keeps the pick", () => {
    const root = document.createElement("div");
    expect(currentTheme(root, true)).toBe("dark");
    const saved = new Map<string, string>();
    applyTheme(root, "light", { setItem: (k, v) => void saved.set(k, v) });
    expect(root.getAttribute("data-theme")).toBe("light");
    expect(currentTheme(root, true)).toBe("light");
    expect(saved.get(THEME_KEY)).toBe("light");
    applyTheme(root, "dark", { setItem: () => { throw new Error("blocked"); } }); // still changes
    expect(root.getAttribute("data-theme")).toBe("dark");
  });

  it("the page-load script applies the saved pick to the site, and survives no storage", () => {
    const site = document.createElement("div");
    const run = (storage: unknown) =>
      new Function("localStorage", "document", THEME_SCRIPT)(storage, { currentScript: { parentElement: site } });
    run({ getItem: () => "sepia" });
    expect(site.hasAttribute("data-theme")).toBe(false);
    run({ getItem: () => "dark" });
    expect(site.getAttribute("data-theme")).toBe("dark");
    expect(() => run(undefined)).not.toThrow();
  });

  it("the button switches the site and says what it will do", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    const site = document.createElement("div");
    site.setAttribute("data-site", "");
    document.body.appendChild(site);
    render(<ThemeToggle lang="zh-TW" />, { container: site });
    fireEvent.click(screen.getByRole("button", { name: "切換為深色模式" }));
    expect(site.getAttribute("data-theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "切換為淺色模式" })).toBeTruthy();
    site.remove();
  });
});

describe("a site reached without the page-load script", () => {
  it("the button puts the saved pick back", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    window.localStorage.setItem(THEME_KEY, "dark");
    const site = document.createElement("div");
    site.setAttribute("data-site", "");
    document.body.appendChild(site);
    render(<ThemeToggle lang="en" />, { container: site });
    expect(site.getAttribute("data-theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeTruthy();
    window.localStorage.removeItem(THEME_KEY);
    site.remove();
  });
});
