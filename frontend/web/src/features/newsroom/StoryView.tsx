// One story (T-517): its leads, the evidence captured for it, its claims with their quotes, and
// the timeline of its workflow. The researcher's and the analyst's activity links land here.
import type { EventEnvelope } from "@autora/event-schema";
import Link from "next/link";

import { withCompany } from "@/features/company/CompanyScope";

import { ARTICLE_STATE, formatTime, label, STORY_STATE, type StoryDetail } from "./model";
import { Badge, ClaimList, Empty, EventList, NewsroomHeader, Section } from "./parts";

const STARTABLE = new Set(["DISCOVERED", "SELECTED"]);

export function StoryView({
  story,
  events,
  onStart,
  starting,
  startError,
}: {
  story: StoryDetail;
  events: readonly EventEnvelope[];
  onStart: () => void;
  starting: boolean;
  startError: string | null;
}) {
  const [state, tone] = label(STORY_STATE, story.state);
  return (
    <main className="mx-auto max-w-4xl px-4 pt-8 pb-12">
      <NewsroomHeader companyId={story.company_id} current="stories" eyebrow="Story" title={story.title}>
        <p className="mt-2 flex flex-wrap items-center gap-3 text-sm">
          <Badge text={state} tone={tone} />
          <span className="text-muted">
            分數 {Math.round(Number(story.score) * 100)}・{story.sources} 個來源・首次出現 {formatTime(story.first_seen_at)}
          </span>
          {story.article ? (
            <Link href={`/admin/newsroom/articles/${story.article.id}`} className="text-accent underline">
              文章（{label(ARTICLE_STATE, story.article.state)[0]}）
            </Link>
          ) : null}
          {STARTABLE.has(story.state) ? (
            <button
              onClick={onStart}
              disabled={starting}
              className="rounded bg-accent px-3 py-1 text-accent-ink disabled:opacity-50"
            >
              {starting ? "啟動中…" : "開始製作"}
            </button>
          ) : null}
        </p>
        {startError ? <p className="mt-2 text-sm text-danger">{startError}</p> : null}
        {story.summary ? <p className="mt-3 text-muted">{story.summary}</p> : null}
      </NewsroomHeader>

      <Section id="sources" title={`線索（${story.leads.length}）`}>
        {story.leads.length === 0 ? (
          <Empty>沒有來源項目（手動建立的題材）。</Empty>
        ) : (
          <ul className="space-y-1 text-sm">
            {story.leads.map((lead) => (
              <li key={lead.url}>
                <a href={lead.url} rel="noopener noreferrer" target="_blank" className="text-accent underline">
                  {lead.title}
                </a>
                <span className="text-muted">
                  ・{lead.source}
                  {lead.published_at ? `・${formatTime(lead.published_at)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section id="evidence" title={`證據（${story.evidence_list.length}）`}>
        {story.evidence_list.length === 0 ? (
          <Empty>還沒有擷取證據。</Empty>
        ) : (
          <ul className="space-y-1 text-sm">
            {story.evidence_list.map((evidence) => (
              <li key={evidence.id}>
                <a href={evidence.url} rel="noopener noreferrer" target="_blank" className="text-accent underline">
                  {evidence.title ?? evidence.url}
                </a>
                <span className="text-muted">
                  ・{evidence.site}・{evidence.chars.toLocaleString()} 字
                  {evidence.truncated ? "（已截斷）" : ""}
                  {evidence.trust_level ? `・信任度 ${Number(evidence.trust_level).toFixed(1)}` : ""}・
                  {formatTime(evidence.retrieved_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section id="claims" title={`主張（${story.claim_list.length}）`}>
        <ClaimList claims={story.claim_list} />
      </Section>

      <Section id="timeline" title="時間軸">
        <EventList events={events} />
        {story.workflow_run_ids.length ? null : <Empty>還沒有開始製作。</Empty>}
      </Section>
      <p className="mt-8 text-sm">
        <Link href={withCompany("/admin/newsroom/stories", story.company_id)} className="text-accent underline">
          ← 所有題材
        </Link>
      </p>
    </main>
  );
}
