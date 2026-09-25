// The site's front page (D-047): the newest story large, then the rest as a list of headlines —
// a news reader scans headlines, and these stories have no pictures to put in cards. Below them,
// the way to older ones. The tabs are in the header (SectionNav); a tab of several sections
// (持股觀察, D-050) has its tags here, and every story says its section as a tag.
import Link from "next/link";

import type { PublicArticleSummary } from "./api";
import {
  filterName,
  formatDate,
  isSection,
  tagsOf,
  topicOf,
  words,
  type Filter,
  type Lang,
  type Section,
} from "./i18n";

export const PAGE_SIZE = 10;

/** The front page's address for a tab or a section, and a page (page 1 and "all" are left out). */
export function listHref(lang: Lang, filter: Filter | null, page = 1): string {
  const query = new URLSearchParams();
  if (filter) query.set("section", filter);
  if (page > 1) query.set("page", String(page));
  const qs = query.toString();
  return `/news/${lang}${qs ? `?${qs}` : ""}`;
}

function Meta({ article, lang }: { article: PublicArticleSummary; lang: Lang }) {
  const w = words(lang);
  const section = article.section as Section | null | undefined;
  return (
    <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
      {section ? (
        <span className="rounded-full border border-accent/40 px-2 py-px font-semibold text-accent">
          {w.sections[section]}
        </span>
      ) : null}
      <time dateTime={article.published_at}>{formatDate(lang, article.published_at)}</time>
      {article.revised_at ? (
        <span>
          ・{w.revised} {formatDate(lang, article.revised_at)}
        </span>
      ) : null}
      {article.access === "members" ? (
        <span className="rounded-full border border-line px-1.5 py-px text-[0.7rem]">{w.member}</span>
      ) : null}
    </p>
  );
}

function Lead({ article, lang }: { article: PublicArticleSummary; lang: Lang }) {
  return (
    <article className="border-b border-line py-8">
      <Meta article={article} lang={lang} />
      <h2 className="mt-3 font-display text-3xl leading-snug font-bold sm:text-4xl sm:leading-tight">
        <Link href={article.path} className="hover:text-accent">
          {article.title}
        </Link>
      </h2>
      {article.summary ? <p className="mt-4 text-lg leading-relaxed text-muted">{article.summary}</p> : null}
    </article>
  );
}

function Row({ article, lang }: { article: PublicArticleSummary; lang: Lang }) {
  return (
    <li className="py-6">
      <Meta article={article} lang={lang} />
      <h2 className="mt-2 font-display text-xl leading-snug font-semibold">
        <Link href={article.path} className="hover:text-accent">
          {article.title}
        </Link>
      </h2>
      {article.summary ? <p className="mt-2 line-clamp-2 leading-relaxed text-muted">{article.summary}</p> : null}
    </li>
  );
}

/** A tab of several sections' tags: all of it, or one of them. */
function Tags({ lang, filter }: { lang: Lang; filter: Filter }) {
  const w = words(lang);
  const topic = isSection(filter) ? topicOf(filter) : filter;
  const tags = tagsOf(topic);
  if (!tags.length) return null;
  const chips: [Filter, string][] = [[topic, w.all], ...tags.map((t): [Filter, string] => [t, w.sections[t]])];
  return (
    <nav aria-label={w.tagsLabel} className="flex flex-wrap gap-2 pt-4 print:hidden">
      {chips.map(([id, label]) => (
        <Link
          key={id}
          href={listHref(lang, id)}
          aria-current={id === filter ? "page" : undefined}
          className={`rounded-full border px-3 py-1 text-xs ${
            id === filter ? "border-ink bg-ink font-semibold text-surface" : "border-line text-muted hover:text-ink"
          }`}
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}

export function ArticleList({
  articles,
  lang,
  section = null,
  page = 1,
  hasMore = false,
}: {
  articles: PublicArticleSummary[];
  lang: Lang;
  /** The tab or the section shown (``?section=``). */
  section?: Filter | null;
  page?: number;
  /** Is there a next page? (The page asked for one more than it shows.) */
  hasMore?: boolean;
}) {
  const w = words(lang);
  // only the first page leads with a story: an older page is a plain continuation of the list
  const lead = page === 1 ? articles[0] : undefined;
  const rest = lead ? articles.slice(1) : articles;
  return (
    <section className="mx-auto max-w-3xl px-4 pt-2 pb-10">
      <h1 className="sr-only">
        {section ? filterName(lang, section) : w.latest}
        {page > 1 ? `・${w.page(page)}` : ""}
      </h1>
      {section ? <Tags lang={lang} filter={section} /> : null}
      {articles.length === 0 ? (
        <p className="py-16 text-center text-muted">{w.empty}</p>
      ) : (
        <>
          {lead ? <Lead article={lead} lang={lang} /> : null}
          <ul className="divide-y divide-line">
            {rest.map((article) => (
              <Row key={article.article_id} article={article} lang={lang} />
            ))}
          </ul>
        </>
      )}
      {page > 1 || hasMore ? (
        <nav className="mt-4 flex items-center justify-between border-t border-line pt-6 text-sm print:hidden">
          {page > 1 ? (
            <Link href={listHref(lang, section, page - 1)} rel="prev" className="text-accent hover:underline">
              {w.newerPage}
            </Link>
          ) : (
            <span />
          )}
          <span className="text-muted">{w.page(page)}</span>
          {hasMore ? (
            <Link href={listHref(lang, section, page + 1)} rel="next" className="text-accent hover:underline">
              {w.olderPage}
            </Link>
          ) : (
            <span />
          )}
        </nav>
      ) : null}
    </section>
  );
}
