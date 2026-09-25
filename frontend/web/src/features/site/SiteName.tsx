// The site's name as the masthead draws it: in Chinese, 艾 (ài, "AI") is a heart — Lucide's
// "heart", filled in rose-600 — before 矽鯨 in a serif. Screen readers and copy-paste still get
// 艾矽鯨.
import { words, type Lang } from "./i18n";

/** Lucide's heart (ISC licence), filled. */
function Heart({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden className={className}>
      <path d="M2 9.5a5.5 5.5 0 0 1 9.591-3.676.56.56 0 0 0 .818 0A5.49 5.49 0 0 1 22 9.5c0 2.29-1.5 4-3 5.5l-5.492 5.313a2 2 0 0 1-3 .019L5 15c-1.5-1.5-3-3.2-3-5.5" />
    </svg>
  );
}

export function SiteName({ lang }: { lang: Lang }) {
  const name = words(lang).site;
  if (lang !== "zh-TW" || !name.startsWith("艾")) return <>{name}</>;
  return (
    <span className="inline-flex items-center">
      <Heart className="mr-[0.08em] size-[0.9em] text-rose-600" />
      <span className="sr-only">艾</span>
      <span className="font-brand">{name.slice(1)}</span>
    </span>
  );
}
