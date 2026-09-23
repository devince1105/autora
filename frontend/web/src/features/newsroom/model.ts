// Pure helpers for the newsroom's admin pages (T-517): labels and the claim numbering.
import type { Schemas } from "@/api/client";
import type { Tone } from "@/events/describe";

export type StorySummary = Schemas["StorySummary"];
export type StoryDetail = Schemas["StoryDetail"];
export type ArticleSummary = Schemas["ArticleSummary"];
export type ArticleDetail = Schemas["ArticleDetail"];
export type ClaimView = Schemas["ClaimView"];
export type SourceView = Schemas["SourceView"];

export const STORY_STATE: Record<string, [string, Tone]> = {
  DISCOVERED: ["新發現", "neutral"],
  SELECTED: ["已選定", "review"],
  IN_PRODUCTION: ["製作中", "work"],
  PUBLISHED: ["已發布", "ok"],
  DROPPED: ["已放棄", "warn"],
  IGNORED: ["略過", "neutral"],
};

export const ARTICLE_STATE: Record<string, [string, Tone]> = {
  DRAFT: ["草稿", "work"],
  IN_REVIEW: ["待核准", "review"],
  APPROVED: ["已核准", "ok"],
  PUBLISHED: ["已發布", "ok"],
  REJECTED: ["已駁回", "danger"],
  ARCHIVED: ["已封存", "neutral"],
};

export const CLAIM_STATUS: Record<string, [string, Tone]> = {
  UNVERIFIED: ["未查核", "neutral"],
  VERIFIED: ["查核通過", "ok"],
  REJECTED: ["查核未過", "danger"],
};

export const CLAIM_TYPE: Record<string, string> = {
  fact: "事實",
  number: "數字",
  quote: "引述",
  attribution: "歸屬",
  opinion: "意見",
};

export const SUPPORT: Record<string, string> = { supports: "支持", contradicts: "反駁", context: "背景" };

export const TONE_BADGE: Record<Tone, string> = {
  neutral: "bg-canvas text-muted",
  think: "bg-canvas text-ink",
  work: "bg-canvas text-accent",
  review: "bg-canvas text-warn",
  ok: "bg-canvas text-ok",
  warn: "bg-canvas text-warn",
  danger: "bg-danger-soft text-danger",
};

export function label(table: Record<string, [string, Tone]>, value: string): [string, Tone] {
  return table[value] ?? [value, "neutral"];
}

/** Claims numbered in the order a version's blocks first cite them: [1], [2]... */
export function claimNumbers(blocks: readonly { claim_ids: readonly string[] }[]): Map<string, number> {
  const numbers = new Map<string, number>();
  for (const block of blocks) {
    for (const id of block.claim_ids) {
      if (!numbers.has(id)) numbers.set(id, numbers.size + 1);
    }
  }
  return numbers;
}

/** The claims in citation order (those the text does not cite come last). */
export function orderedClaims(claims: readonly ClaimView[], numbers: Map<string, number>): ClaimView[] {
  return [...claims].sort(
    (a, b) => (numbers.get(a.id) ?? Number.MAX_SAFE_INTEGER) - (numbers.get(b.id) ?? Number.MAX_SAFE_INTEGER),
  );
}

export function formatTime(iso: string): string {
  return new Intl.DateTimeFormat("zh-TW", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Taipei",
  }).format(new Date(iso));
}

/** A fact-check result's problems as one line (the fact-check stores one per claim). */
export function problems(result: Record<string, unknown>): string {
  const list = Array.isArray(result.problems) ? (result.problems as unknown[]).map(String) : [];
  if (typeof result.draft === "string") list.push(result.draft);
  return list.join("；");
}
