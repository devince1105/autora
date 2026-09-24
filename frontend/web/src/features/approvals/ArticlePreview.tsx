// The article an approval is about, readable where the decision is made (D-046).
//
// An approver used to see a title and a JSON blob, and had to go and find the article. Now the
// card opens the version that was submitted — title, summary, every paragraph, in each language
// — with its fact-check and what the writer said they changed; and for a revision of a
// published article, the paragraphs that differ from the one on the site.
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { articleQuery } from "@/api/queries";
import { withCompany } from "@/features/company/CompanyScope";
import type { ArticleDetail } from "@/features/newsroom/model";

import { changed, diffParagraphs, type DiffLine } from "./diff";

const LANG_NAME: Record<string, string> = { "zh-TW": "中文", en: "English" };
const BLOCK_PREFIX: Record<string, string> = { heading: "## ", quote: "〉" };

/** The version's text as lines a person reads and a diff compares: title, summary, paragraphs. */
export function lines(article: ArticleDetail, lang: string): string[] {
  const text = article.languages[lang];
  if (!text) return [];
  return [
    `標題：${text.title}`,
    ...(text.summary ? [`摘要：${text.summary}`] : []),
    ...text.blocks.map((b) => `${BLOCK_PREFIX[b.type] ?? ""}${b.text}`),
  ];
}

const DIFF_STYLE: Record<DiffLine["op"], string> = {
  same: "",
  removed: "bg-danger/10 text-danger line-through decoration-danger/60",
  added: "bg-ok/10 text-ok",
};
const DIFF_MARK: Record<DiffLine["op"], string> = { same: "", removed: "－ ", added: "＋ " };

export function ArticlePreviewView({
  draft,
  published,
  lang,
  onLang,
}: {
  /** The article as submitted: ``shown`` is the version being decided. */
  draft: ArticleDetail;
  /** The version on the site, when it is another one: a revision is read against it. */
  published: ArticleDetail | null;
  lang: string;
  onLang: (lang: string) => void;
}) {
  const [compare, setCompare] = useState(published !== null);
  const langs = Object.keys(draft.languages).sort(
    (a, b) => Number(b === draft.primary_lang) - Number(a === draft.primary_lang) || a.localeCompare(b),
  );
  const shownLang = draft.languages[lang] ? lang : langs[0];
  const version = draft.versions.find((v) => v.version === draft.shown);
  const check = draft.fact_checks.filter((f) => f.version === draft.shown).at(-1);
  const now = shownLang ? lines(draft, shownLang) : [];
  const diff = published && shownLang ? diffParagraphs(lines(published, shownLang), now) : null;

  return (
    <div data-testid="article-preview" className="mt-3 grid gap-3 rounded-lg border border-line bg-canvas p-4 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        {langs.map((l) => (
          <button
            key={l}
            type="button"
            aria-pressed={l === shownLang}
            onClick={() => onLang(l)}
            className={l === shownLang ? "font-semibold underline" : "text-accent"}
          >
            {LANG_NAME[l] ?? l}
          </button>
        ))}
        {diff ? (
          <label className="ml-auto flex items-center gap-1 text-muted">
            <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} />
            對照目前發布的版本
          </label>
        ) : null}
      </div>

      <p className="text-muted">
        第 {draft.shown} 版
        {check ? (
          <span className={check.passed ? "text-ok" : "text-danger"}>
            ・事實查核{check.passed ? "通過" : "未通過"}（檢查 {check.checked} 項{check.failed ? `，${check.failed} 項未過` : ""}）
          </span>
        ) : (
          "・沒有事實查核紀錄"
        )}
        {version?.change_summary ? `・寫手說明：${version.change_summary}` : null}
      </p>

      {compare && diff ? (
        <div data-testid="article-diff" className="grid gap-2">
          {changed(diff) ? null : <p className="text-muted">這個語言的內容和目前發布的版本相同。</p>}
          {diff.map((line, i) => (
            <p key={i} data-op={line.op} className={`rounded px-1 leading-relaxed whitespace-pre-wrap ${DIFF_STYLE[line.op]}`}>
              {DIFF_MARK[line.op]}
              {line.text}
            </p>
          ))}
        </div>
      ) : (
        <div data-testid="article-text" className="grid gap-2">
          {now.map((line, i) => (
            <p key={i} className={`leading-relaxed whitespace-pre-wrap ${i === 0 ? "font-semibold" : ""}`}>
              {line}
            </p>
          ))}
        </div>
      )}

      <Link
        href={withCompany(`/newsroom/articles/${draft.id}?version=${draft.shown}`, draft.company_id)}
        className="text-accent underline"
      >
        開啟完整文章頁（論點、引用出處、查核細節）
      </Link>
    </div>
  );
}

/** The submitted version and, for a revision, the published one — fetched only when opened. */
export function ArticlePreview({ articleId, draftGroupId }: { articleId: string; draftGroupId: string | null }) {
  const [open, setOpen] = useState(false);
  const [lang, setLang] = useState("zh-TW");
  const latest = useQuery({ ...articleQuery(articleId), enabled: open });
  const versions = latest.data?.versions ?? [];
  const submitted = versions.find((v) => v.draft_group_id === draftGroupId)?.version ?? latest.data?.shown ?? null;
  const onSite = versions.find((v) => v.published && v.version !== submitted)?.version ?? null;
  const draft = useQuery({ ...articleQuery(articleId, submitted), enabled: open && submitted !== null });
  const published = useQuery({ ...articleQuery(articleId, onSite), enabled: open && onSite !== null });
  const error = latest.error ?? draft.error ?? published.error;
  const ready = draft.data && (onSite === null || published.data);

  return (
    <div className="mt-2">
      <button type="button" onClick={() => setOpen(!open)} className="text-sm text-accent underline">
        {open ? "收合全文" : "展開全文"}
      </button>
      {open && error ? (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error.message}
        </p>
      ) : null}
      {open && !error && !ready ? <p className="mt-2 text-sm text-muted">載入文章中…</p> : null}
      {open && ready && draft.data ? (
        <ArticlePreviewView
          draft={draft.data}
          published={onSite !== null ? (published.data ?? null) : null}
          lang={lang}
          onLang={setLang}
        />
      ) : null}
    </div>
  );
}
