// @vitest-environment jsdom
// D-046: an approver reads the article, and a revision against the version on the site.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ArticleDetail } from "@/features/newsroom/model";

import { ApprovalInbox } from "./ApprovalInbox";
import { ArticlePreviewView, lines } from "./ArticlePreview";
import { changed, diffParagraphs } from "./diff";
import { approvalCard, type Approval } from "./model";

afterEach(cleanup);

const AT = "2026-09-25T00:00:00Z";

function article(over: Partial<ArticleDetail> & { zh?: string[]; title?: string } = {}): ArticleDetail {
  const { zh = ["輝達減持 7,563,100 股。", "出清台積電。"], title = "H&H 最新 13F", ...rest } = over;
  return {
    id: "a1",
    story_id: "s1",
    title,
    state: "IN_REVIEW",
    slug: "h-h",
    version: 2,
    langs: ["zh-TW", "en"],
    revision_count: 0,
    published_at: AT,
    listed: true,
    updated_at: AT,
    views: 0,
    story_title: "H&H",
    company_id: "c1",
    primary_lang: "zh-TW",
    published_langs: ["zh-TW", "en"],
    public_urls: {},
    versions: [
      { version: 1, draft_group_id: "g1", langs: ["zh-TW", "en"], created_at: AT, change_summary: null, current: false, published: true },
      { version: 2, draft_group_id: "g2", langs: ["zh-TW", "en"], created_at: AT, change_summary: "拿掉「逆勢」", current: true, published: false },
    ],
    shown: 2,
    languages: {
      "zh-TW": { version_id: "v", title, summary: "摘要", blocks: zh.map((text) => ({ type: "paragraph", text, claim_ids: [] })) },
      en: { version_id: "ve", title: "H&H's latest 13F", summary: null, blocks: [{ type: "paragraph", text: "Nvidia cut.", claim_ids: [] }] },
    },
    claims: [],
    fact_checks: [{ id: "f", version: 2, passed: true, created_at: AT, checked: 12, failed: 0, results: [] }],
    distributions: [],
    analytics: [],
    events_for: [],
    workflow_run_ids: [],
    ...rest,
  } as ArticleDetail;
}

describe("what changed, paragraph by paragraph", () => {
  it("keeps what both share and marks the rest", () => {
    expect(diffParagraphs(["a", "b", "c"], ["a", "B", "c", "d"])).toEqual([
      { op: "same", text: "a" },
      { op: "removed", text: "b" },
      { op: "added", text: "B" },
      { op: "same", text: "c" },
      { op: "added", text: "d" },
    ]);
    expect(changed(diffParagraphs(["x"], ["x"]))).toBe(false);
    expect(diffParagraphs([], ["new"])).toEqual([{ op: "added", text: "new" }]);
  });
});

describe("reading the article before deciding", () => {
  it("shows the submitted version whole, with its fact-check and what the writer changed", () => {
    render(<ArticlePreviewView draft={article()} published={null} lang="zh-TW" onLang={vi.fn()} />);
    const text = screen.getByTestId("article-text").textContent!;
    expect(text).toContain("標題：H&H 最新 13F");
    expect(text).toContain("出清台積電。");
    const meta = screen.getByTestId("article-preview").textContent!;
    expect(meta).toContain("第 2 版");
    expect(meta).toContain("事實查核通過（檢查 12 項）");
    expect(meta).toContain("寫手說明：拿掉「逆勢」");
    expect(screen.queryByText("對照目前發布的版本")).toBeNull(); // nothing on the site to compare
    expect(screen.getByRole("link").getAttribute("href")).toBe("/admin/newsroom/articles/a1?version=2&company=c1");
  });

  it("reads each language", () => {
    const onLang = vi.fn();
    render(<ArticlePreviewView draft={article()} published={null} lang="en" onLang={onLang} />);
    expect(screen.getByTestId("article-text").textContent).toContain("Nvidia cut.");
    fireEvent.click(screen.getByRole("button", { name: "中文" }));
    expect(onLang).toHaveBeenCalledWith("zh-TW");
  });

  it("a revision opens on what differs from the version on the site", () => {
    const before = article({ title: "H&H 逆勢加碼", zh: ["輝達減持 7,563,100 股，獲利了結。", "出清台積電。"], shown: 1 });
    const after = article();
    render(<ArticlePreviewView draft={after} published={before} lang="zh-TW" onLang={vi.fn()} />);
    const diff = screen.getByTestId("article-diff");
    const ops = Array.from(diff.querySelectorAll("[data-op]")).map((p) => [p.getAttribute("data-op"), p.textContent]);
    expect(ops).toContainEqual(["removed", "－ 輝達減持 7,563,100 股，獲利了結。"]);
    expect(ops).toContainEqual(["added", "＋ 輝達減持 7,563,100 股。"]);
    expect(ops).toContainEqual(["same", "出清台積電。"]);
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByTestId("article-text")).toBeTruthy(); // and back to reading it whole
  });

  it("the lines are the title, the summary and the paragraphs", () => {
    expect(lines(article(), "zh-TW")).toEqual(["標題：H&H 最新 13F", "摘要：摘要", "輝達減持 7,563,100 股。", "出清台積電。"]);
    expect(lines(article(), "ja")).toEqual([]);
  });
});

describe("the inbox card about an article", () => {
  const approval = {
    id: "ap1",
    company_id: "c1",
    kind: "article",
    state: "PENDING",
    action: "approve_article",
    summary: "核准發布：H&H 最新 13F",
    payload: { story_id: "s1", article_id: "a1", draft_group_id: "g2" },
    task_id: "t1",
    run_id: null,
    ref_type: "task",
    ref_id: "t1",
    requested_by: { kind: "system", id: "newsroom" },
    created_at: AT,
    expires_at: null,
    decided_at: null,
    decided_by: null,
    reason: null,
  } as unknown as Approval;

  it("knows which article and which draft it is about", () => {
    expect(approvalCard(approval, {}, new Date(AT)).article).toEqual({ id: "a1", draftGroupId: "g2" });
  });

  it("shows the article, not its payload", () => {
    render(
      <ApprovalInbox
        state="PENDING"
        onState={vi.fn()}
        cards={[approvalCard(approval, {}, new Date(AT))]}
        loadError={null}
        decide={vi.fn(async () => ({}))}
        live
        refresh={vi.fn()}
        preview={(card) => <p data-testid="preview">{card.article?.id}</p>}
      />,
    );
    const card = screen.getByTestId("approval-ap1");
    expect(within(card).getByTestId("preview").textContent).toBe("a1");
    expect(card.querySelector("pre")).toBeNull();
  });
});
