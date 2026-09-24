// @vitest-environment jsdom
// T-517: the newsroom's admin pages — stories, a story, articles, an article (versions, languages,
// claims with quotes in place, fact-check, distribution, readers), sources, and their data.
import type { EventEnvelope } from "@autora/event-schema";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createApiClient } from "@/api/client";
import { eventToQueryKeys } from "@/api/invalidation";
import { articleQuery, queryKeys, storiesQuery, workflowEventsQuery } from "@/api/queries";

import { ArticlesView } from "./ArticlesView";
import { ArticleView } from "./ArticleView";
import { claimNumbers, orderedClaims, problems, type ArticleDetail, type ClaimView, type StoryDetail } from "./model";
import { AddSourceForm, SourcesView } from "./SourcesView";
import { StoriesView } from "./StoriesView";
import { StoryView } from "./StoryView";

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
afterEach(cleanup);

const C = "0192c000-0000-7000-8000-000000000001";
const STORY = "0192c000-0000-7000-8000-000000000002";
const ARTICLE = "0192c000-0000-7000-8000-000000000003";
const PANELS = "0192c000-0000-7000-8000-00000000000a";
const COST = "0192c000-0000-7000-8000-00000000000b";
const AT = "2026-09-21T02:00:00Z";

const claim = (id: string, text: string, status = "VERIFIED"): ClaimView => ({
  id,
  text,
  claim_type: "number",
  status,
  quotes: [
    {
      evidence_id: "0192c000-0000-7000-8000-0000000000e1",
      evidence_title: "Lumen City switches on its first microgrid",
      url: "https://news.fixtures.autora.test/pilot",
      support_type: "supports",
      quote: "links 1,200 rooftop solar panels",
      before: "The pilot in Harbor District ",
      after: " and a 4 MWh battery.",
    },
  ],
});

const ARTICLE_DETAIL: ArticleDetail = {
  id: ARTICLE,
  story_id: STORY,
  title: "流明市首座社區微電網啟用",
  state: "PUBLISHED",
  slug: "microgrid-a1b2c3",
  version: 2,
  langs: ["en", "zh-TW"],
  revision_count: 1,
  published_at: AT,
  updated_at: AT,
  views: 7,
  story_title: "Lumen City microgrid",
  company_id: C,
  primary_lang: "zh-TW",
  published_langs: ["zh-TW", "en"],
  public_urls: { "zh-TW": "/news/zh-TW/articles/microgrid-a1b2c3", en: "/news/en/articles/microgrid-a1b2c3" },
  versions: [
    { version: 1, draft_group_id: "g1", langs: ["zh-TW", "en"], created_at: AT, change_summary: null, current: false, published: false },
    { version: 2, draft_group_id: "g2", langs: ["zh-TW", "en"], created_at: AT, change_summary: "補上導言", current: true, published: true },
  ],
  shown: 2,
  languages: {
    en: {
      version_id: "v2en",
      title: "Lumen City switches on its first microgrid",
      summary: null,
      blocks: [{ type: "paragraph", text: "The city spent NT$420 million.", claim_ids: [COST] }],
    },
    "zh-TW": {
      version_id: "v2zh",
      title: "流明市首座社區微電網啟用",
      summary: "重點數字",
      blocks: [
        { type: "heading", text: "重點", claim_ids: [] },
        { type: "paragraph", text: "市府花了 4.2 億元。", claim_ids: [COST] },
        { type: "paragraph", text: "串連 1,200 組太陽能板。", claim_ids: [PANELS, COST] },
      ],
    },
  },
  claims: [claim(PANELS, "The microgrid links 1,200 rooftop panels."), claim(COST, "The city spent NT$420 million.")],
  fact_checks: [
    { id: "f2", version: 2, passed: true, created_at: AT, checked: 2, failed: 0, results: [] },
    {
      id: "f1",
      version: 1,
      passed: false,
      created_at: AT,
      checked: 2,
      failed: 1,
      results: [{ claim_id: PANELS, text: "1,300 panels", verdict: "fail", problems: ["number 1,300 not in any quote"] }],
    },
  ],
  distributions: [
    { id: "d1", channel: "site", status: "published", external_ref: "/news/zh-TW/articles/x", created_at: AT, content: { "zh-TW": { url: "/news/zh-TW/articles/x", title: "流明市" } } },
    { id: "d2", channel: "social_draft", status: "draft", external_ref: null, created_at: AT, content: { en: { text: "New: the microgrid.", url: "/news/en/articles/x" } } },
  ],
  analytics: [{ day: "2026-09-21", lang: "en", views: 5, uniques: 5, read_complete: 2 }],
  workflow_run_ids: ["run1"],
};

const event = (event_type: string, payload: object): EventEnvelope =>
  ({
    event_id: `e-${event_type}`,
    event_type,
    schema_version: 1,
    seq: 1,
    company_id: C,
    occurred_at: AT,
    payload,
    correlation_id: "run1",
  }) as unknown as EventEnvelope;

describe("the model", () => {
  it("numbers claims by first citation and orders them so", () => {
    const numbers = claimNumbers(ARTICLE_DETAIL.languages["zh-TW"]!.blocks);
    expect([...numbers]).toEqual([
      [COST, 1],
      [PANELS, 2],
    ]);
    expect(orderedClaims(ARTICLE_DETAIL.claims, numbers).map((c) => c.id)).toEqual([COST, PANELS]);
  });

  it("puts a fact-check result's problems on one line", () => {
    expect(problems({ problems: ["a", "b"] })).toBe("a；b");
    expect(problems({ draft: "the languages cite different claims" })).toBe("the languages cite different claims");
  });
});

describe("taking an article off the site (D-044)", () => {
  const onSite = () => ({ unpublish: vi.fn(), republish: vi.fn(), busy: false, error: null });

  it("a published article comes down only with a reason", () => {
    const controls = onSite();
    render(<ArticleView article={{ ...ARTICLE_DETAIL, state: "PUBLISHED" }} lang="zh-TW" onLang={vi.fn()} events={[]} onSite={controls} />);
    const down = within(screen.getByTestId("site-controls")).getByRole("button", { name: "下架" }) as HTMLButtonElement;
    expect(down.disabled).toBe(true);
    fireEvent.change(within(screen.getByTestId("site-controls")).getByRole("textbox"), { target: { value: "用字要改" } });
    fireEvent.click(down);
    expect(controls.unpublish).toHaveBeenCalledWith("用字要改");
  });

  it("one that was taken down says so and can go back up", () => {
    const controls = onSite();
    render(<ArticleView article={{ ...ARTICLE_DETAIL, state: "ARCHIVED" }} lang="zh-TW" onLang={vi.fn()} events={[]} onSite={controls} />);
    expect(screen.getByTestId("site-controls").textContent).toContain("網站上看不到這篇");
    fireEvent.click(screen.getByRole("button", { name: "重新上架" }));
    expect(controls.republish).toHaveBeenCalled();
  });

  it("a draft has nothing to take down", () => {
    render(<ArticleView article={{ ...ARTICLE_DETAIL, state: "DRAFT" }} lang="zh-TW" onLang={vi.fn()} events={[]} onSite={onSite()} />);
    expect(screen.queryByTestId("site-controls")).toBeNull();
  });
});

describe("an article", () => {
  function show(lang = "zh-TW", onLang = vi.fn()) {
    render(<ArticleView article={ARTICLE_DETAIL} lang={lang} onLang={onLang} events={[event("ARTICLE_PUBLISHED", { langs: ["zh-TW", "en"], url: "/news/zh-TW/articles/x" })]} />);
    return onLang;
  }

  it("shows the version's text with each paragraph's claims marked", () => {
    show();
    const text = screen.getByRole("article");
    expect(text.getAttribute("lang")).toBe("zh-TW");
    expect(within(text).getByRole("heading", { name: "流明市首座社區微電網啟用" })).toBeTruthy();
    const paragraph = within(text).getByText(/串連 1,200 組太陽能板/);
    const marks = within(paragraph).getAllByRole("link");
    expect(marks.map((a) => a.textContent)).toEqual(["[2]", "[1]"]);
    expect(marks[0]!.getAttribute("href")).toBe(`#claim-${PANELS}`);
    expect(screen.getByText("這一版的修改：補上導言")).toBeTruthy();
  });

  it("switches language and version", () => {
    const onLang = show();
    fireEvent.click(screen.getByRole("tab", { name: "en" }));
    expect(onLang).toHaveBeenCalledWith("en");
    cleanup();
    show("en");
    expect(within(screen.getByRole("article")).getByText(/NT\$420 million/)).toBeTruthy();
    const versions = within(screen.getByRole("navigation", { name: "版本" }));
    expect(versions.getByRole("link", { name: /v1/ }).getAttribute("href")).toBe(`/newsroom/articles/${ARTICLE}?version=1`);
    expect(versions.getByRole("link", { name: /v2・已發布/ }).getAttribute("aria-current")).toBe("page");
  });

  it("lists every cited claim with its quote in place", () => {
    show();
    const item = document.getElementById(`claim-${PANELS}`)!;
    expect(within(item).getByText("[2]")).toBeTruthy();
    expect(within(item).getByText("查核通過")).toBeTruthy();
    const mark = item.querySelector("mark")!;
    expect(mark.textContent).toBe("links 1,200 rooftop solar panels");
    expect(mark.parentElement!.textContent).toBe("…The pilot in Harbor District links 1,200 rooftop solar panels and a 4 MWh battery.…");
  });

  it("shows the fact-checks, the distribution, the readers, the public pages and the timeline", () => {
    show();
    const checks = within(document.getElementById("fact-check")!);
    expect(checks.getByText("通過")).toBeTruthy();
    expect(checks.getByText("「1,300 panels」：number 1,300 not in any quote")).toBeTruthy();
    const distribution = within(document.getElementById("distribution")!);
    expect(distribution.getByText("社群貼文（草稿，未發出）")).toBeTruthy();
    expect(distribution.getByText("New: the microgrid.")).toBeTruthy();
    expect(screen.getByText("讀者（共 7 次瀏覽）")).toBeTruthy();
    expect(screen.getByRole("link", { name: "公開頁（en）" }).getAttribute("href")).toBe("/news/en/articles/microgrid-a1b2c3");
    expect(within(document.getElementById("timeline")!).getByText("文章發布")).toBeTruthy();
    expect(screen.getByRole("link", { name: "題材：Lumen City microgrid" }).getAttribute("href")).toBe(`/newsroom/stories/${STORY}`);
  });
});

const STORY_DETAIL: StoryDetail = {
  id: STORY,
  company_id: C,
  title: "Lumen City microgrid",
  state: "DISCOVERED",
  score: "0.72",
  items: 3,
  sources: 2,
  evidence: 1,
  claims: 1,
  first_seen_at: AT,
  article: null,
  summary: "A pilot.",
  angle: null,
  seed: {},
  leads: [{ title: "A lead", url: "https://news.fixtures.autora.test/a", source: "Lumen City News", published_at: AT }],
  evidence_list: [
    {
      id: "e1",
      title: "Pilot page",
      url: "https://news.fixtures.autora.test/pilot",
      site: "news.fixtures.autora.test",
      retrieved_at: AT,
      chars: 1234,
      truncated: false,
      trust_level: "0.70",
      run_id: null,
    },
  ],
  claim_list: [claim(PANELS, "The microgrid links 1,200 rooftop panels.", "UNVERIFIED")],
  workflow_run_ids: [],
};

describe("a story", () => {
  it("shows its leads, evidence and claims, and can be started", () => {
    const onStart = vi.fn();
    render(<StoryView story={STORY_DETAIL} events={[]} onStart={onStart} starting={false} startError={null} />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Lumen City microgrid");
    expect(screen.getByText("新發現")).toBeTruthy();
    expect(screen.getByRole("link", { name: "A lead" })).toBeTruthy();
    expect(within(document.getElementById("evidence")!).getByText(/1,234 字・信任度 0.7/)).toBeTruthy();
    expect(within(document.getElementById("claims")!).getByText("未查核")).toBeTruthy();
    expect(screen.getByText("還沒有開始製作。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "開始製作" }));
    expect(onStart).toHaveBeenCalledOnce();
    expect(screen.getByRole("link", { name: "文章" }).getAttribute("href")).toBe(`/newsroom/articles?company=${C}`);
  });

  it("cannot be started twice, and says why a start failed", () => {
    render(
      <StoryView
        story={{ ...STORY_DETAIL, state: "IN_PRODUCTION", workflow_run_ids: ["run1"] }}
        events={[event("TASK_READY", { required_role: "researcher" })]}
        onStart={vi.fn()}
        starting={false}
        startError="the company has no active project"
      />,
    );
    expect(screen.queryByRole("button", { name: "開始製作" })).toBeNull();
    expect(screen.getByText("the company has no active project")).toBeTruthy();
    expect(screen.queryByText("還沒有開始製作。")).toBeNull();
  });
});

describe("the lists", () => {
  it("stories: filter by state, link to each story and its article", () => {
    const onFilter = vi.fn();
    render(
      <StoriesView
        stories={[{ ...STORY_DETAIL, state: "PUBLISHED", article: { id: ARTICLE, state: "PUBLISHED", slug: "s", title: "t" } }]}
        filter="ALL"
        onFilter={onFilter}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "製作中" }));
    expect(onFilter).toHaveBeenCalledWith("IN_PRODUCTION");
    expect(screen.getByRole("link", { name: "Lumen City microgrid" }).getAttribute("href")).toBe(`/newsroom/stories/${STORY}`);
    expect(screen.getByRole("link", { name: "文章" }).getAttribute("href")).toBe(`/newsroom/articles/${ARTICLE}`);
    expect(screen.getByText(/分數 72・3 則來源項目・1 則主張/)).toBeTruthy();
  });

  it("articles: state, version, languages, readers", () => {
    render(<ArticlesView articles={[ARTICLE_DETAIL]} />);
    const row = screen.getByRole("link", { name: "流明市首座社區微電網啟用" }).closest("tr")!;
    expect(within(row).getByText("已發布")).toBeTruthy();
    expect(within(row).getByText("（修訂 1 次）")).toBeTruthy();
    expect(within(row).getByText("en / zh-TW")).toBeTruthy();
    expect(within(row).getByText("7")).toBeTruthy();
    cleanup();
    render(<ArticlesView articles={[]} />);
    expect(screen.getByText(/還沒有文章/)).toBeTruthy();
  });
});

describe("sources", () => {
  it("lists them", () => {
    render(
      <SourcesView
        sources={[
          { id: "s1", name: "Lumen City News", kind: "rss", url: "https://x.test/feed", config: {}, trust_level: "0.70", language: "en", status: "active", poll_interval_seconds: 3600, last_polled_at: null, next_poll_at: AT, items: 4 },
        ]}
      />,
    );
    expect(screen.getByText("Lumen City News")).toBeTruthy();
    expect(screen.getByText(/信任度 0.7・4 則項目・尚未讀取/)).toBeTruthy();
  });

  it("adds one of each kind", async () => {
    const onAdd = vi.fn(() => Promise.resolve());
    render(<AddSourceForm onAdd={onAdd} />);
    fireEvent.change(screen.getByLabelText("名稱"), { target: { value: "Watchlist" } });
    fireEvent.change(screen.getByLabelText("種類"), { target: { value: "url_list" } });
    fireEvent.change(screen.getByLabelText(/網址（空白或換行分隔）/), { target: { value: "https://a.test/1\nhttps://a.test/2" } });
    fireEvent.submit(screen.getByRole("button", { name: "新增來源" }).closest("form")!);
    await vi.waitFor(() => expect(onAdd).toHaveBeenCalledOnce());
    expect(onAdd.mock.calls[0]).toEqual([
      expect.objectContaining({ name: "Watchlist", kind: "url_list", url: null, config: { urls: ["https://a.test/1", "https://a.test/2"] } }),
    ]);
  });

  it("shows why a source was refused", async () => {
    render(<AddSourceForm onAdd={() => Promise.reject(new Error("422 an rss source needs an http(s) feed url"))} />);
    fireEvent.change(screen.getByLabelText("名稱"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Feed 網址"), { target: { value: "not a url" } });
    fireEvent.submit(screen.getByRole("button", { name: "新增來源" }).closest("form")!);
    expect(await screen.findByText("422 an rss source needs an http(s) feed url")).toBeTruthy();
  });
});

describe("the data", () => {
  function client() {
    const requests: Request[] = [];
    const api = createApiClient({
      baseUrl: "http://api",
      getToken: () => "t",
      fetch: (async (r: Request) => {
        requests.push(r);
        return new Response(JSON.stringify({ items: [], next_after: 0, has_more: false }), { headers: { "Content-Type": "application/json" } });
      }) as unknown as typeof fetch,
    });
    return { api, requests };
  }

  it("asks for the version, the state and the workflow's events", async () => {
    const { api, requests } = client();
    await articleQuery(ARTICLE, 2, api).queryFn!({} as never);
    await storiesQuery(C, "PUBLISHED", api).queryFn!({} as never);
    await workflowEventsQuery(C, "run1", api).queryFn!({} as never);
    expect(requests.map((r) => r.url)).toEqual([
      `http://api/api/articles/${ARTICLE}?version=2`,
      `http://api/api/companies/${C}/stories?state=PUBLISHED`,
      `http://api/api/events?company_id=${C}&correlation_id=run1&limit=500`,
    ]);
  });

  it("newsroom events refresh the newsroom pages; a workflow's events its timeline", () => {
    expect(eventToQueryKeys({ ...event("ARTICLE_PUBLISHED", {}), run_id: null, task_id: null })).toEqual([["newsroom"]]);
    expect(eventToQueryKeys({ ...event("WORKFLOW_RUN_EXTENDED", {}), run_id: null, task_id: null })).toEqual([
      queryKeys.workflowEvents(C, "run1"),
    ]);
  });
});
