// The newsroom's stories (T-517): what the sources brought, clustered; what is being made of them.
import Link from "next/link";

import { Badge, Empty } from "./parts";
import { formatTime, label, STORY_STATE, type StorySummary } from "./model";

export const STORY_FILTERS = ["ALL", "DISCOVERED", "SELECTED", "IN_PRODUCTION", "PUBLISHED", "DROPPED"] as const;
export type StoryFilter = (typeof STORY_FILTERS)[number];

export function StoriesView({
  stories,
  filter,
  onFilter,
}: {
  stories: readonly StorySummary[] | undefined;
  filter: StoryFilter;
  onFilter: (filter: StoryFilter) => void;
}) {
  return (
    <>
      <div role="tablist" className="mb-4 flex flex-wrap gap-2 text-sm">
        {STORY_FILTERS.map((key) => (
          <button
            key={key}
            role="tab"
            aria-selected={filter === key}
            onClick={() => onFilter(key)}
            className={`rounded border px-3 py-1 ${filter === key ? "border-accent text-accent" : "border-line text-muted"}`}
          >
            {key === "ALL" ? "全部" : label(STORY_STATE, key)[0]}
          </button>
        ))}
      </div>
      {!stories ? (
        <Empty>載入中…</Empty>
      ) : stories.length === 0 ? (
        <Empty>沒有題材。新增來源後，每 5 分鐘讀取一次並分群成題材。</Empty>
      ) : (
        <ul className="divide-y divide-line rounded border border-line bg-surface">
          {stories.map((story) => {
            const [state, tone] = label(STORY_STATE, story.state);
            return (
              <li key={story.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <Badge text={state} tone={tone} />
                <Link href={`/admin/newsroom/stories/${story.id}`} className="font-medium hover:text-accent">
                  {story.title}
                </Link>
                <span className="grow" />
                <span className="text-xs text-muted">
                  分數 {Math.round(Number(story.score) * 100)}・{story.items} 則來源項目・{story.claims} 則主張
                  ・{formatTime(story.first_seen_at)}
                </span>
                {story.article ? (
                  <Link href={`/admin/newsroom/articles/${story.article.id}`} className="text-xs text-accent underline">
                    文章
                  </Link>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
