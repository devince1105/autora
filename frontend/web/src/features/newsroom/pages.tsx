"use client";

// The newsroom pages' data (T-517): each page loads through the typed client and stays fresh
// through the company's event stream (src/api/invalidation.ts); the views render.
import { parseEvent, type EventEnvelope } from "@autora/event-schema";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import {
  addSource,
  articleQuery,
  articlesQuery,
  sourcesQuery,
  startStory,
  storiesQuery,
  storyQuery,
  workflowEventsQuery,
  type StoryState,
} from "@/api/queries";
import { CompanyScope, type Company } from "@/features/company/CompanyScope";
import { useCompanyStream } from "@/features/company/useCompanyStream";

import { ArticlesView } from "./ArticlesView";
import { ArticleView } from "./ArticleView";
import type { ArticleDetail, StoryDetail } from "./model";
import { Empty, NewsroomHeader } from "./parts";
import { AddSourceForm, SourcesView } from "./SourcesView";
import { StoriesView, type StoryFilter } from "./StoriesView";
import { StoryView } from "./StoryView";

function Loading({ error }: { error: Error | null }) {
  return (
    <main className="mx-auto max-w-4xl px-4 pt-8">
      {error ? <p className="text-danger">{error.message}</p> : <Empty>載入中…</Empty>}
    </main>
  );
}

/** The timeline of a workflow run (the latest one, when there are several). Events this page
 * cannot read (a newer type) are left out, as the realtime store does. */
function useTimeline(companyId: string, runIds: readonly string[]): EventEnvelope[] {
  const runId = runIds.at(-1);
  const events = useQuery({
    ...workflowEventsQuery(companyId, runId ?? "none"),
    enabled: Boolean(runId),
  });
  if (!runId) return [];
  return (events.data?.items ?? []).flatMap((raw) => {
    const parsed = parseEvent(raw);
    return parsed.ok ? [parsed.event] : [];
  });
}

// --- lists ------------------------------------------------------------------------------------

export function StoriesPage() {
  return <CompanyScope>{(company) => <CompanyStories company={company} />}</CompanyScope>;
}

function CompanyStories({ company }: { company: Company }) {
  useCompanyStream(company.id);
  const [filter, setFilter] = useState<StoryFilter>("ALL");
  const stories = useQuery(storiesQuery(company.id, filter === "ALL" ? null : (filter as StoryState)));
  return (
    <main className="mx-auto max-w-4xl px-4 pt-8 pb-12">
      <NewsroomHeader companyId={company.id} current="stories" title={`${company.name} 的題材`} />
      {stories.error ? <p className="text-danger">{stories.error.message}</p> : null}
      <StoriesView stories={stories.data} filter={filter} onFilter={setFilter} />
    </main>
  );
}

export function ArticlesPage() {
  return <CompanyScope>{(company) => <CompanyArticles company={company} />}</CompanyScope>;
}

function CompanyArticles({ company }: { company: Company }) {
  useCompanyStream(company.id);
  const articles = useQuery(articlesQuery(company.id));
  return (
    <main className="mx-auto max-w-4xl px-4 pt-8 pb-12">
      <NewsroomHeader companyId={company.id} current="articles" title={`${company.name} 的文章`} />
      {articles.error ? <p className="text-danger">{articles.error.message}</p> : null}
      <ArticlesView articles={articles.data} />
    </main>
  );
}

export function SourcesPage() {
  return <CompanyScope>{(company) => <CompanySources company={company} />}</CompanyScope>;
}

function CompanySources({ company }: { company: Company }) {
  useCompanyStream(company.id);
  const sources = useQuery(sourcesQuery(company.id));
  const queryClient = useQueryClient();
  return (
    <main className="mx-auto max-w-4xl px-4 pt-8 pb-12">
      <NewsroomHeader companyId={company.id} current="sources" title={`${company.name} 的來源`} />
      {sources.error ? <p className="text-danger">{sources.error.message}</p> : null}
      <SourcesView sources={sources.data} />
      <h2 className="mt-8 mb-3 text-lg font-semibold">新增來源</h2>
      <AddSourceForm
        onAdd={async (body) => {
          await addSource(company.id, body);
          await queryClient.invalidateQueries({ queryKey: ["newsroom", "sources", company.id] });
        }}
      />
    </main>
  );
}

// --- details ----------------------------------------------------------------------------------

export function StoryPage({ storyId }: { storyId: string }) {
  const story = useQuery(storyQuery(storyId));
  if (!story.data) return <Loading error={story.error} />;
  return <LoadedStory story={story.data} />;
}

function LoadedStory({ story }: { story: StoryDetail }) {
  useCompanyStream(story.company_id);
  const events = useTimeline(story.company_id, story.workflow_run_ids);
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: () => startStory(story.id),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["newsroom"] }),
  });
  return (
    <StoryView
      story={story}
      events={events}
      onStart={() => start.mutate()}
      starting={start.isPending}
      startError={start.error?.message ?? null}
    />
  );
}

export function ArticlePage({ articleId }: { articleId: string }) {
  const requested = Number(useSearchParams().get("version")) || null;
  const article = useQuery(articleQuery(articleId, requested));
  if (!article.data) return <Loading error={article.error} />;
  return <LoadedArticle article={article.data} />;
}

function LoadedArticle({ article }: { article: ArticleDetail }) {
  useCompanyStream(article.company_id);
  const [lang, setLang] = useState(article.primary_lang);
  const events = useTimeline(article.company_id, article.workflow_run_ids);
  return <ArticleView article={article} lang={lang} onLang={setLang} events={events} />;
}
