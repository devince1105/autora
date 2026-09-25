// The site's front page (D-047): the newest story large, then the rest as a list of headlines —
// a news reader scans headlines, and these stories have no pictures to put in cards. Below them,
// the way to older ones. The sections are in the header (SectionNav).
import Link from "next/link";

import type { PublicArticleSummary } from "./api";
import { formatDate, words, type Lang, type Section } from "./i18n";

export const PAGE_SIZE = 10;

/** The front page's address for a section and a page (page 1 and "all" are left out). */
export function listHref(lang: Lang, section: Section | null, page = 1): string {
  const query = new URLSearchParams();
  if (section) query.set("section", section);
  if (page > 1) query.set("page", String(page));
  const qs = query.toString();
  return `/news/${lang}${qs ? `?${qs}` : ""}`;
}

function Meta({ article, lang }: { article: PublicArticleSummary; lang: Lang }) {
  const w = words(lang);
  const section = article.section as Section | null | undefined;
  return (
    <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
      {section ? <span className="font-semibold text-accent">{w.sections[section]}</span> : null}
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

export function ArticleList({
  articles,
  lang,
  section = null,
  page = 1,
  hasMore = false,
}: {
  articles: PublicArticleSummary[];
  lang: Lang;
  section?: Section | null;
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
        {section ? w.sections[section] : w.latest}
        {page > 1 ? `・${w.page(page)}` : ""}
      </h1>
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
