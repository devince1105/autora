// The public site's two languages (D-002: zh-TW first, then en) and its few words of UI.
export const LANGS = ["zh-TW", "en"] as const;
export type Lang = (typeof LANGS)[number];

export function isLang(value: string): value is Lang {
  return (LANGS as readonly string[]).includes(value);
}

export const LANG_NAMES: Record<Lang, string> = { "zh-TW": "中文", en: "English" };

const WORDS = {
  "zh-TW": {
    site: "Autora 新聞",
    latest: "最新報導",
    empty: "還沒有報導。",
    sources: "資料來源",
    published: "發布於",
    allStories: "所有報導",
    readIn: "閱讀其他語言：",
    notice: "本站為示範：報導由 AI 新聞室撰寫、事實查核並經人核准。",
  },
  en: {
    site: "Autora News",
    latest: "Latest stories",
    empty: "No stories yet.",
    sources: "Sources",
    published: "Published",
    allStories: "All stories",
    readIn: "Read in:",
    notice: "A demo: stories are written and fact-checked by an AI newsroom and approved by a person.",
  },
} as const;

export function words(lang: Lang) {
  return WORDS[lang];
}

export function formatDate(lang: Lang, iso: string): string {
  return new Intl.DateTimeFormat(lang, { dateStyle: "long", timeZone: "Asia/Taipei" }).format(
    new Date(iso),
  );
}
