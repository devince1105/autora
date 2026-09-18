// Server state (T-308, 3d-office/05 §5): TanStack Query options for what the realtime store does
// not hold (run details, traces, task history, approvals...). Each factory returns plain query
// options, so pages use them with useQuery / useSuspenseQuery and tests call queryFn directly.
// Freshness comes from events: src/api/invalidation.ts invalidates the matching keys.
import { QueryClient, queryOptions } from "@tanstack/react-query";

import { api as defaultApi, unwrap, type ApiClient } from "./client";

export const queryKeys = {
  companies: () => ["companies"] as const,
  agents: (companyId: string) => ["agents", companyId] as const,
  run: (runId: string) => ["run", runId] as const,
  trace: (runId: string) => ["trace", runId] as const,
  task: (taskId: string) => ["task", taskId] as const,
  approvals: (companyId: string, state = "PENDING") => ["approvals", companyId, state] as const,
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
  state: "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED" = "PENDING",
  api: ApiClient = defaultApi,
) {
  return queryOptions({
    queryKey: queryKeys.approvals(companyId, state),
    queryFn: async () =>
      unwrap(await api.GET("/api/approvals", { params: { query: { company_id: companyId, state } } })),
  });
}

// --- commands (REST only; the WebSocket carries no commands, 05 §2) --------------------------

export async function decideApproval(
  approvalId: string,
  decision: "approve" | "reject",
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

/** Defaults: data is kept until an event invalidates it; errors are not retried in a loop. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 60_000, retry: 1, refetchOnWindowFocus: false },
      mutations: { retry: 0 },
    },
  });
}
