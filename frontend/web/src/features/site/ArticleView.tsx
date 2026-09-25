// A published article as readers see it (T-515): text, date, byline, the sources behind it, and
// the other languages it was published in. Server-rendered; the beacon and the reading tools
// (listen, print: D-047) are its only client parts.
import Link from "next/link";

import type { PublicArticle } from "./api";
import { listHref } from "./ArticleList";
import { Beacon } from "./Beacon";
import { formatDate, isLang, isSection, LANG_NAMES, words, type Lang } from "./i18n";
import { MembersOnly } from "./MembersOnly";
import { ListenButton, PrintButton } from "./ReadingTools";

export function ArticleView({ article, lang }: { article: PublicArticle; lang: Lang }) {
  const w = words(lang);
  const others = Object.entries(article.langs).filter(([other]) => other !== lang && isLang(other));
  const section = isSection(article.section) ? article.section : null;
  const spoken = [article.title, ...(article.summary ? [article.summary] : []), ...article.blocks.map((b) => b.text)];
  return (
    <article className="mx-auto max-w-2xl px-4 pt-6 pb-10">
      <nav aria-label="breadcrumb" className="text-sm text-muted print:hidden">
        <Link href={`/news/${lang}`} className="hover:text-ink">
          {w.site}
        </Link>
        {section ? (
          <>
            <span className="mx-2">/</span>
            <Link href={listHref(lang, section)} className="hover:text-ink">
              {w.sections[section]}
            </Link>
          </>
        ) : null}
      </nav>
      <header className="mt-6 mb-10">
        {section ? <p className="mb-3 text-sm font-semibold text-accent">{w.sections[section]}</p> : null}
        <h1 className="font-display text-3xl leading-snug font-bold sm:text-4xl sm:leading-tight">{article.title}</h1>
        {article.summary ? <p className="mt-4 text-lg leading-relaxed text-muted">{article.summary}</p> : null}
        <p className="mt-5 text-sm text-muted">
          {/* the site's own name in the page's language, not the company's one spelling (D-043) */}
          <span>{w.site}・</span>
          {w.published} <time dateTime={article.published_at}>{formatDate(lang, article.published_at)}</time>
          {article.revised_at ? (
            <>
              {" ・ "}
              {w.revised} <time dateTime={article.revised_at}>{formatDate(lang, article.revised_at)}</time>
            </>
          ) : null}
        </p>
        <div className="mt-4 flex flex-wrap gap-2 print:hidden">
          <ListenButton lang={lang} texts={spoken} />
          <PrintButton lang={lang} />
        </div>
        {others.length ? (
          <p className="mt-3 text-sm print:hidden">
            {w.readIn}{" "}
            {others.map(([other, path]) => (
              <Link key={other} href={path} hrefLang={other} className="mr-2 text-accent underline">
                {LANG_NAMES[other as Lang]}
              </Link>
            ))}
          </p>
        ) : null}
      </header>

      {/* Chinese reads best with room between the lines, and is never set in italics */}
      <div className="space-y-6 text-[1.0625rem] leading-[1.9] sm:text-lg">
        {article.blocks.map((block, index) => {
          if (block.type === "heading") {
            return (
              <h2 key={index} className="pt-4 font-display text-2xl leading-snug font-bold">
                {block.text}
              </h2>
            );
          }
          if (block.type === "quote") {
            return (
              <blockquote key={index} className="rounded-r-lg border-l-4 border-accent/60 bg-canvas py-3 pr-4 pl-5 text-muted">
                {block.text}
              </blockquote>
            );
          }
          return <p key={index}>{block.text}</p>;
        })}
      </div>

      {article.locked ? <MembersOnly lang={lang} path={article.path} company={article.company_slug} /> : null}

      {article.sources.length ? (
        <section className="mt-10 border-t border-line pt-6">
          <h2 className="text-base font-semibold">{w.sources}</h2>
          <ol className="mt-3 list-decimal space-y-1.5 pl-6 text-sm break-words">
            {article.sources.map((source) => (
              <li key={source.url}>
                <a href={source.url} rel="noopener nofollow" className="text-accent underline">
                  {source.title}
                </a>{" "}
                <span className="text-muted">
                  {lang === "zh-TW" ? `（${source.site}）` : `(${source.site})`}
                </span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      <p className="mt-10 rounded-lg bg-canvas p-4 text-xs leading-relaxed text-muted">{w.notice}</p>

      {article.newer || article.older ? (
        <nav className="mt-10 grid gap-3 border-t border-line pt-6 sm:grid-cols-2 print:hidden">
          {article.newer ? (
            <Link href={article.newer.path} rel="prev" className="group rounded-lg border border-line p-4 hover:border-accent">
              <span className="text-xs text-muted">← {w.newerStory}</span>
              <span className="mt-1 line-clamp-2 block font-display font-semibold group-hover:text-accent">
                {article.newer.title}
              </span>
            </Link>
          ) : (
            <span className="hidden sm:block" />
          )}
          {article.older ? (
            <Link
              href={article.older.path}
              rel="next"
              className="group rounded-lg border border-line p-4 text-right hover:border-accent"
            >
              <span className="text-xs text-muted">{w.olderStory} →</span>
              <span className="mt-1 line-clamp-2 block font-display font-semibold group-hover:text-accent">
                {article.older.title}
              </span>
            </Link>
          ) : null}
        </nav>
      ) : null}
      <p className="mt-6 text-center text-sm print:hidden">
        <Link href={`/news/${lang}`} className="text-accent hover:underline">
          {w.allStories}
        </Link>
      </p>
      <Beacon articleId={article.article_id} lang={lang} />
    </article>
  );
}
