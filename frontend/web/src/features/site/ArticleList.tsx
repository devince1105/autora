// The site's front page: the newest published stories in one language.
import Link from "next/link";

import type { PublicArticleSummary } from "./api";
import { formatDate, words, type Lang } from "./i18n";

export function ArticleList({ articles, lang }: { articles: PublicArticleSummary[]; lang: Lang }) {
  const w = words(lang);
  return (
    <section className="mx-auto max-w-2xl px-4 py-10">
      <h1 className="text-2xl font-bold">{w.latest}</h1>
      {articles.length === 0 ? (
        <p className="mt-6 text-muted">{w.empty}</p>
      ) : (
        <ul className="mt-6 divide-y divide-line">
          {articles.map((article) => (
            <li key={article.article_id} className="py-5">
              <Link href={article.path} className="text-xl font-semibold hover:text-accent">
                {article.title}
              </Link>
              {article.summary ? <p className="mt-1 text-muted">{article.summary}</p> : null}
              <p className="mt-1 text-sm text-muted">
                <time dateTime={article.published_at}>{formatDate(lang, article.published_at)}</time>
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
