// The site's sections under the masthead (D-047, after the WSJ's), on every page. On the front page
// the one being shown is marked; elsewhere none is. Pinned to the top once the masthead has
// scrolled away, so a reader can always move between sections.
"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { listHref } from "./ArticleList";
import { filterName, isFilter, isSection, topicOf, TOPICS, type Lang, type Topic, words } from "./i18n";

/** Which tab is current: on the front page, its tab (a section's is the tab it is under) or
 * "all"; on any other page, none. */
export function currentSection(lang: Lang, pathname: string, section: string | null): Topic | "all" | null {
  if (pathname !== `/news/${lang}`) return null;
  if (!isFilter(section)) return "all";
  return isSection(section) ? topicOf(section) : section;
}

function Tabs({ lang, current }: { lang: Lang; current: Topic | "all" | null }) {
  const w = words(lang);
  const tabs: [Topic | null, string][] = [[null, w.all], ...TOPICS.map((t): [Topic, string] => [t, filterName(lang, t)])];
  return (
    // px-1: with each tab's own px-3, the first label sits on the column's edge, under the masthead
    <ul className="mx-auto flex max-w-3xl gap-1 px-1">
      {tabs.map(([id, label]) => {
        const here = (id ?? "all") === current;
        return (
          <li key={id ?? "all"} className="shrink-0">
            <Link
              href={listHref(lang, id)}
              aria-current={here ? "page" : undefined}
              className={`block border-b-2 px-3 py-2.5 text-sm whitespace-nowrap ${
                here ? "border-ink font-semibold text-ink" : "border-transparent text-muted hover:text-ink"
              }`}
            >
              {label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function Current({ lang }: { lang: Lang }) {
  const pathname = usePathname();
  const search = useSearchParams();
  return <Tabs lang={lang} current={currentSection(lang, pathname, search.get("section"))} />;
}

export function SectionNav({ lang }: { lang: Lang }) {
  const w = words(lang);
  return (
    <nav
      aria-label={w.sectionsLabel}
      className="sticky top-0 z-30 overflow-x-auto border-b border-line bg-surface/90 backdrop-blur-md [scrollbar-width:none] print:hidden"
    >
      {/* the query string is read in the browser: until then, the tabs without a current one */}
      <Suspense fallback={<Tabs lang={lang} current={null} />}>
        <Current lang={lang} />
      </Suspense>
    </nav>
  );
}
