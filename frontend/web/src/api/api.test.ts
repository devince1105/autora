import { parseEvent, type EventEnvelope } from "@autora/event-schema";
import { QueryClient } from "@tanstack/react-query";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

import { createRealtimeStore } from "@/stores/realtime";

import { ApiError, createApiClient } from "./client";
import { connectQueryInvalidation, eventToQueryKeys } from "./invalidation";
import { approvalsQuery, decideApproval, queryKeys, runQuery, startWorkflow } from "./queries";

const fixture = JSON.parse(
  readFileSync(new URL("../realtime/__fixtures__/contract.json", import.meta.url), "utf8"),
) as { snapshot_before: unknown; events: unknown[] };
const events: EventEnvelope[] = fixture.events.map((raw) => {
  const parsed = parseEvent(raw);
  if (!parsed.ok) throw new Error(parsed.error);
  return parsed.event;
});
const RUN = "01a0b3a0-0000-7000-8000-000000000001";

function mockApi(respond: (request: Request) => Response) {
  const requests: Request[] = [];
  const fetchImpl = vi.fn(async (input: Request) => {
    requests.push(input.clone());
    return respond(input);
  });
  const api = createApiClient({
    baseUrl: "http://api.test",
    getToken: () => "tok",
    fetch: fetchImpl as unknown as typeof fetch,
  });
  return { api, requests };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("typed client", () => {
  it("builds the URL from the OpenAPI path, sends the token, returns the typed body", async () => {
    const { api, requests } = mockApi(() => json({ id: RUN, state: "COMPLETED", cost_usd: "0.0035" }));
    const run = await runQuery(RUN, api).queryFn!({} as never);
    expect(requests[0].url).toBe(`http://api.test/api/runs/${RUN}`);
    expect(requests[0].headers.get("authorization")).toBe("Bearer tok");
    expect(run.state).toBe("COMPLETED");
    expect(run.cost_usd).toBe("0.0035");
  });

  it("turns a problem+json error into an ApiError", async () => {
    const { api } = mockApi(() =>
      json({ type: "about:blank", title: "Not Found", status: 404, detail: `agent run ${RUN} not found` }, 404),
    );
    const error = await Promise.resolve()
      .then(() => runQuery(RUN, api).queryFn!({} as never))
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, title: "Not Found", detail: `agent run ${RUN} not found` });
  });

  it("sends commands as REST with query and body parameters", async () => {
    const { api, requests } = mockApi(() => json({ id: "x" }));
    await approvalsQuery("c1", "PENDING", api).queryFn!({} as never);
    expect(requests[0].url).toBe("http://api.test/api/approvals?company_id=c1&state=PENDING");
    await decideApproval("ap1", "approve", null, api);
    expect(requests[1].method).toBe("POST");
    expect(await requests[1].json()).toEqual({ decision: "approve", reason: null });
    await startWorkflow("c1", { template: "echo.chain_v1", project_id: "p1" }, api);
    expect(await requests[2].json()).toEqual({ template: "echo.chain_v1", project_id: "p1", params: {} });
  });

  it("sends no Authorization header without a token", async () => {
    const requests: Request[] = [];
    const api = createApiClient({
      baseUrl: "http://api.test",
      getToken: () => null,
      fetch: (async (r: Request) => { requests.push(r); return json([]); }) as unknown as typeof fetch,
    });
    await api.GET("/api/companies");
    expect(requests[0].headers.has("authorization")).toBe(false);
  });
});

describe("event -> stale queries", () => {
  const first = (type: string) => {
    const event = events.find((e) => e.event_type === type);
    if (!event) throw new Error(`no ${type} in the fixture`);
    return event;
  };

  it.each([
    ["TASK_SUCCEEDED", (e: EventEnvelope) => [queryKeys.trace(e.run_id!), queryKeys.run(e.run_id!), queryKeys.task(e.task_id!)]],
    ["TASK_CREATED", (e: EventEnvelope) => [queryKeys.task(e.task_id!)]],
    ["AGENT_THINKING", (e: EventEnvelope) => [queryKeys.trace(e.run_id!)]],
    ["AGENT_RUN_COMPLETED", (e: EventEnvelope) => [queryKeys.trace(e.run_id!), queryKeys.run(e.run_id!), queryKeys.kpis(e.company_id)]],
    ["APPROVAL_REQUESTED", (e: EventEnvelope) => [queryKeys.trace(e.run_id!), ["approvals", e.company_id]]],
    ["AGENT_PAUSED", (e: EventEnvelope) => [queryKeys.agents(e.company_id)]],
  ])("%s", (type, expected) => {
    const event = first(type);
    expect(eventToQueryKeys(event)).toEqual(expected(event));
  });

  it("approvals of every state share the prefix the events invalidate", () => {
    expect(queryKeys.approvals("c1", "APPROVED").slice(0, 2)).toEqual(["approvals", "c1"]);
  });
});

describe("invalidation wiring", () => {
  it("invalidates each stale key once per store update, only for new events", () => {
    const store = createRealtimeStore();
    const queryClient = new QueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
    const stop = connectQueryInvalidation(queryClient, store);

    store.getState().hydrate(fixture.snapshot_before);
    expect(invalidate).not.toHaveBeenCalled(); // hydrating is not news

    store.getState().applyEvents(fixture.events);
    const keys = invalidate.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey));
    expect(new Set(keys).size).toBe(keys.length); // each key once
    const expected = new Set(events.flatMap((e) => eventToQueryKeys(e).map((k) => JSON.stringify(k))));
    expect(new Set(keys)).toEqual(expected);

    invalidate.mockClear();
    store.getState().applyEvents(fixture.events); // replays: nothing new
    expect(invalidate).not.toHaveBeenCalled();
    stop();
  });
});
