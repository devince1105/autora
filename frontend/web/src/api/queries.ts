// Server state (T-308, 3d-office/05 §5): TanStack Query options for what the realtime store does
// not hold (run details, traces, task history, approvals...). Each factory returns plain query
// options, so pages use them with useQuery / useSuspenseQuery and tests call queryFn directly.
// Freshness comes from events: src/api/invalidation.ts invalidates the matching keys.
import { QueryClient, queryOptions } from "@tanstack/react-query";

import { api as defaultApi, unwrap, type ApiClient, type Schemas } from "./client";

export const queryKeys = {
  companies: () => ["companies"] as const,
  agents: (companyId: string) => ["agents", companyId] as const,
  run: (runId: string) => ["run", runId] as const,
  trace: (runId: string) => ["trace", runId] as const,
  task: (taskId: string) => ["task", taskId] as const,
  approvals: (companyId: string, state = "PENDING") => ["approvals", companyId, state] as const,
  kpis: (companyId: string) => ["kpis", companyId] as const,
  /** Newsroom pages (T-517): every key starts with "newsroom", so one invalidation covers them. */
  stories: (companyId: string, state: string | null) => ["newsroom", "stories", companyId, state] as const,
  story: (storyId: string) => ["newsroom", "story", storyId] as const,
  articles: (companyId: string) => ["newsroom", "articles", companyId] as const,
  article: (articleId: string, version: number | null) => ["newsroom", "article", articleId, version] as const,
  sources: (companyId: string) => ["newsroom", "sources", companyId] as const,
  workflowEvents: (companyId: string, runId: string) => ["newsroom", "events", companyId, runId] as const,
  roles: () => ["roles"] as const,
  /** The company's days (T-608). */
  org: (companyId: string) => ["org", companyId] as const,
  failedWorkflows: (companyId: string) => ["workflows", "failed", companyId] as const,
  cycles: (companyId: string) => ["cycles", companyId] as const,
  cycle: (cycleId: string) => ["cycle", cycleId] as const,
};

export function companiesQuery(api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.companies(),
    queryFn: async () => unwrap(await api.GET("/api/companies")),
  });
}

export function agentsQuery(companyId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.agents(companyId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/companies/{company_id}/agents", {
          params: { path: { company_id: companyId } },
        }),
      ),
  });
}

export function runQuery(runId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.run(runId),
    queryFn: async () =>
      unwrap(await api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } })),
  });
}

export function traceQuery(runId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.trace(runId),
    queryFn: async () =>
      unwrap(await api.GET("/api/runs/{run_id}/trace", { params: { path: { run_id: runId } } })),
  });
}

export function taskQuery(taskId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.task(taskId),
    queryFn: async () =>
      unwrap(await api.GET("/api/tasks/{task_id}", { params: { path: { task_id: taskId } } })),
  });
}

export function approvalsQuery(
  companyId: string,
  state: "PENDING" | "APPROVED" | "REJECTED" | "RETURNED" | "EXPIRED" = "PENDING",
  api: ApiClient = defaultApi,
) {
  return queryOptions({
    queryKey: queryKeys.approvals(companyId, state),
    queryFn: async () =>
      unwrap(await api.GET("/api/approvals", { params: { query: { company_id: companyId, state } } })),
  });
}

/**
 * KPIs are aggregates the event stream cannot rebuild (model costs are not events), so they are
 * server state: refetched when an event says they changed, and every minute while shown.
 */
/** The org chart (T-600): who the company's departments are, and what they are called. */
export function orgQuery(companyId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.org(companyId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/companies/{company_id}/org", {
          params: { path: { company_id: companyId } },
        }),
      ),
    staleTime: 5 * 60_000, // an org chart changes when somebody is hired, not every minute
  });
}

export function cyclesQuery(companyId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.cycles(companyId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/companies/{company_id}/cycles", {
          params: { path: { company_id: companyId } },
        }),
      ),
    refetchInterval: 60_000,
  });
}

export function cycleQuery(cycleId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.cycle(cycleId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/cycles/{cycle_id}", {
          params: { path: { cycle_id: cycleId } },
        }),
      ),
  });
}

export function kpisQuery(companyId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.kpis(companyId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/companies/{company_id}/kpis", {
          params: { path: { company_id: companyId } },
        }),
      ),
    refetchInterval: 60_000,
  });
}

// --- newsroom (T-517) -------------------------------------------------------------------------

export type StoryState = "DISCOVERED" | "SELECTED" | "IN_PRODUCTION" | "PUBLISHED" | "DROPPED" | "IGNORED";

export function storiesQuery(companyId: string, state: StoryState | null = null, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.stories(companyId, state),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/companies/{company_id}/stories", {
          params: { path: { company_id: companyId }, query: state ? { state } : {} },
        }),
      ),
  });
}

export function storyQuery(storyId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.story(storyId),
    queryFn: async () =>
      unwrap(await api.GET("/api/stories/{story_id}", { params: { path: { story_id: storyId } } })),
  });
}

export function articlesQuery(companyId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.articles(companyId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/companies/{company_id}/articles", {
          params: { path: { company_id: companyId } },
        }),
      ),
  });
}

export function articleQuery(articleId: string, version: number | null = null, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.article(articleId, version),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/articles/{article_id}", {
          params: { path: { article_id: articleId }, query: version ? { version } : {} },
        }),
      ),
  });
}

export function sourcesQuery(companyId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.sources(companyId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/companies/{company_id}/sources", {
          params: { path: { company_id: companyId } },
        }),
      ),
  });
}

/** One workflow run's events (its timeline): they carry the run's id as correlation. */
export function workflowEventsQuery(companyId: string, runId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.workflowEvents(companyId, runId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/events", {
          params: { query: { company_id: companyId, correlation_id: runId, limit: 500 } },
        }),
      ),
  });
}

// --- commands (REST only; the WebSocket carries no commands, 05 §2) --------------------------

export async function decideApproval(
  approvalId: string,
  decision: "approve" | "reject" | "revise",
  reason: string | null = null,
  api: ApiClient = defaultApi,
) {
  return unwrap(
    await api.POST("/api/approvals/{approval_id}/decide", {
      params: { path: { approval_id: approvalId } },
      body: { decision, reason },
    }),
  );
}

/** Take a published article off the site (D-044). The reason is kept with its history. */
export async function unpublishArticle(articleId: string, reason: string, api: ApiClient = defaultApi) {
  return unwrap(
    await api.POST("/api/articles/{article_id}/unpublish", {
      params: { path: { article_id: articleId } },
      body: { reason },
    }),
  );
}

/** Change a published article (D-045): the site keeps the published version until the new one. */
export async function reviseArticle(articleId: string, reason: string, api: ApiClient = defaultApi) {
  return unwrap(
    await api.POST("/api/articles/{article_id}/revise", {
      params: { path: { article_id: articleId } },
      body: { reason },
    }),
  );
}

/** Put an article that was taken down back on the site (D-044). */
export async function republishArticle(articleId: string, api: ApiClient = defaultApi) {
  return unwrap(
    await api.POST("/api/articles/{article_id}/republish", { params: { path: { article_id: articleId } } }),
  );
}

/** Runs that ended badly and could be started again (AC-9). */
export function failedWorkflowsQuery(companyId: string, api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.failedWorkflows(companyId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/companies/{company_id}/workflows/failed", {
          params: { path: { company_id: companyId } },
        }),
      ),
    refetchInterval: 30_000,
  });
}

/** Start a failed run again. The company may still refuse: read the decision in the result. */
export async function restartWorkflow(
  companyId: string,
  workflowRunId: string,
  api: ApiClient = defaultApi,
) {
  return unwrap(
    await api.POST("/api/companies/{company_id}/workflows/{workflow_run_id}/restart", {
      params: { path: { company_id: companyId, workflow_run_id: workflowRunId } },
    }),
  );
}

export async function startWorkflow(
  companyId: string,
  body: { template: string; project_id: string; params?: Record<string, unknown> },
  api: ApiClient = defaultApi,
) {
  return unwrap(
    await api.POST("/api/companies/{company_id}/workflows", {
      params: { path: { company_id: companyId } },
      body: { ...body, params: body.params ?? {} },
    }),
  );
}

export function rolesQuery(api: ApiClient = defaultApi) {
  return queryOptions({
    queryKey: queryKeys.roles(),
    queryFn: async () => unwrap(await api.GET("/api/roles")),
    staleTime: Infinity, // the runtime's roles change with a deploy, not with the data
  });
}

export type NewAgent = Schemas["NewAgent"];

/** Hire an agent (T-517 follow-up): it appears in the office and takes its role's tasks. */
export async function hireAgent(companyId: string, body: NewAgent, api: ApiClient = defaultApi) {
  return unwrap(
    await api.POST("/api/companies/{company_id}/agents", {
      params: { path: { company_id: companyId } },
      body,
    }),
  );
}

export type AgentAction = "pause" | "resume" | "retire";

/** Stop an agent taking work, put it back, or let it go (T-517 follow-up). */
export async function decideAgent(
  companyId: string,
  agentId: string,
  action: AgentAction,
  reason: string | null = null,
  api: ApiClient = defaultApi,
) {
  const paths = {
    pause: "/api/companies/{company_id}/agents/{agent_id}/pause",
    resume: "/api/companies/{company_id}/agents/{agent_id}/resume",
    retire: "/api/companies/{company_id}/agents/{agent_id}/retire",
  } as const;
  return unwrap(
    await api.POST(paths[action], {
      params: { path: { company_id: companyId, agent_id: agentId } },
      body: { reason },
    }),
  );
}

export async function startStory(storyId: string, projectId: string | null = null, api: ApiClient = defaultApi) {
  return unwrap(
    await api.POST("/api/stories/{story_id}/start", {
      params: { path: { story_id: storyId } },
      body: { project_id: projectId },
    }),
  );
}

export type NewSource = Schemas["NewSource"];

export async function addSource(companyId: string, body: NewSource, api: ApiClient = defaultApi) {
  return unwrap(
    await api.POST("/api/companies/{company_id}/sources", {
      params: { path: { company_id: companyId } },
      body,
    }),
  );
}

/** Defaults: data is kept until an event invalidates it; errors are not retried in a loop. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 60_000, retry: 1, refetchOnWindowFocus: false },
      mutations: { retry: 0 },
    },
  });
}
