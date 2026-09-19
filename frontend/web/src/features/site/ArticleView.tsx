// A published article as readers see it (T-515): text, date, byline, the sources behind it, and
// the other languages it was published in. Server-rendered; the beacon is its only client part.
import Link from "next/link";

import type { PublicArticle } from "./api";
import { Beacon } from "./Beacon";
import { formatDate, isLang, LANG_NAMES, words, type Lang } from "./i18n";

export function ArticleView({ article, lang }: { article: PublicArticle; lang: Lang }) {
  const w = words(lang);
  const others = Object.entries(article.langs).filter(([other]) => other !== lang && isLang(other));
  return (
    <article className="mx-auto max-w-2xl px-4 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-bold leading-tight">{article.title}</h1>
        {article.summary ? <p className="mt-3 text-lg text-muted">{article.summary}</p> : null}
        <p className="mt-4 text-sm text-muted">
          {article.company ? <span>{article.company}・</span> : null}
          {w.published} <time dateTime={article.published_at}>{formatDate(lang, article.published_at)}</time>
        </p>
        {others.length ? (
          <p className="mt-2 text-sm">
            {w.readIn}{" "}
            {others.map(([other, path]) => (
              <Link key={other} href={path} hrefLang={other} className="mr-2 text-accent underline">
                {LANG_NAMES[other as Lang]}
              </Link>
            ))}
          </p>
        ) : null}
      </header>

      <div className="space-y-5 text-lg leading-8">
        {article.blocks.map((block, index) => {
          if (block.type === "heading") {
            return (
              <h2 key={index} className="pt-2 text-xl font-semibold">
                {block.text}
              </h2>
            );
          }
          if (block.type === "quote") {
            return (
              <blockquote key={index} className="border-l-4 border-line pl-4 italic text-muted">
                {block.text}
              </blockquote>
            );
          }
          return <p key={index}>{block.text}</p>;
        })}
      </div>

      {article.sources.length ? (
        <section className="mt-10 border-t border-line pt-6">
          <h2 className="text-base font-semibold">{w.sources}</h2>
          <ol className="mt-3 list-decimal space-y-1 pl-6 text-sm">
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

      <p className="mt-10 text-xs text-muted">{w.notice}</p>
      <Beacon articleId={article.article_id} lang={lang} />
    </article>
  );
}
