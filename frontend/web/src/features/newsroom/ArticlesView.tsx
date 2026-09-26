// The newsroom's articles (T-517): drafts, in review, published.
import Link from "next/link";

import { ARTICLE_STATE, formatTime, label, type ArticleSummary } from "./model";
import { Badge, Empty } from "./parts";

export function ArticlesView({ articles }: { articles: readonly ArticleSummary[] | undefined }) {
  if (!articles) return <Empty>載入中…</Empty>;
  if (articles.length === 0) return <Empty>還沒有文章。寫手寫出第一份草稿後就會出現在這裡。</Empty>;
  return (
    <table className="w-full rounded border border-line bg-surface text-sm">
      <thead className="text-left text-xs text-muted">
        <tr>
          <th className="px-3 py-2">狀態</th>
          <th className="px-3 py-2">標題</th>
          <th className="px-3 py-2">版本</th>
          <th className="px-3 py-2">語言</th>
          <th className="px-3 py-2 text-right">瀏覽</th>
          <th className="px-3 py-2">更新</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-line">
        {articles.map((article) => {
          const [state, tone] = label(ARTICLE_STATE, article.state);
          return (
            <tr key={article.id}>
              <td className="px-3 py-2">
                <Badge text={state} tone={tone} />
              </td>
              <td className="px-3 py-2">
                <Link href={`/admin/newsroom/articles/${article.id}`} className="font-medium hover:text-accent">
                  {article.title}
                </Link>
              </td>
              <td className="px-3 py-2">
                v{article.version ?? "—"}
                {article.revision_count ? <span className="text-muted">（修訂 {article.revision_count} 次）</span> : null}
              </td>
              <td className="px-3 py-2">{article.langs.join(" / ")}</td>
              <td className="px-3 py-2 text-right">{article.views.toLocaleString()}</td>
              <td className="px-3 py-2 text-muted">{formatTime(article.updated_at)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
