// @vitest-environment jsdom
// Hiring an agent from the office (T-517 follow-up): the form offers the roles the runtime can
// run, sends what the API expects, and says why a hire was refused.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createApiClient } from "@/api/client";
import { hireAgent, rolesQuery, type NewAgent } from "@/api/queries";

import { AgentsView, HireForm, type Agent } from "./AgentsView";

afterEach(cleanup);

const RAE: Agent = {
  id: "a1",
  role: "researcher",
  display_name: "Rae",
  avatar_key: "default",
  status: "active",
  activity: { state: "WORKING", stored_state: "WORKING", detail: {}, run_id: null, task_id: null, since: "2026-09-20T00:00:00Z", last_event_seq: 1 },
};

describe("the agent list", () => {
  it("shows who works here and what they are doing", () => {
    render(<AgentsView agents={[RAE]} />);
    const row = screen.getByText("Rae").closest("li")!;
    expect(within(row).getByText("研究員")).toBeTruthy();
    expect(within(row).getByText(/在職・工作中/)).toBeTruthy();
  });

  it("says when there is nobody", () => {
    render(<AgentsView agents={[]} />);
    expect(screen.getByText("這間公司還沒有代理。")).toBeTruthy();
  });
});

describe("the hire form", () => {
  const roles = ["analyst", "editor", "researcher"];

  it("offers the runtime's roles, marks the taken ones, and hires", async () => {
    const onHire = vi.fn<(agent: NewAgent) => Promise<void>>(() => Promise.resolve());
    render(<HireForm roles={roles} taken={["researcher"]} onHire={onHire} />);
    expect(screen.getByRole("option", { name: "研究員（已有人）" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("角色"), { target: { value: "editor" } });
    fireEvent.change(screen.getByLabelText("名字"), { target: { value: " Eli " } });
    fireEvent.change(screen.getByLabelText(/單次執行上限/), { target: { value: "0.5" } });
    fireEvent.submit(screen.getByRole("button", { name: "雇用" }).closest("form")!);
    await vi.waitFor(() => expect(onHire).toHaveBeenCalledOnce());
    expect(onHire.mock.calls[0]![0]).toMatchObject({ role: "editor", display_name: "Eli", per_run_usd: "0.5" });
  });

  it("shows why a hire was refused", async () => {
    render(<HireForm roles={roles} taken={[]} onHire={() => Promise.reject(new Error("422 no behavior for role 'chef'"))} />);
    fireEvent.change(screen.getByLabelText("名字"), { target: { value: "X" } });
    fireEvent.submit(screen.getByRole("button", { name: "雇用" }).closest("form")!);
    expect(await screen.findByText(/no behavior for role 'chef'/)).toBeTruthy();
  });
});

describe("the requests", () => {
  function client() {
    const requests: Request[] = [];
    const api = createApiClient({
      baseUrl: "http://api",
      getToken: () => "t",
      fetch: (async (r: Request) => {
        requests.push(r);
        return new Response(JSON.stringify({ roles: ["editor"] }), { headers: { "Content-Type": "application/json" } });
      }) as unknown as typeof fetch,
    });
    return { api, requests };
  }

  it("asks for the roles and posts the hire", async () => {
    const { api, requests } = client();
    await rolesQuery(api).queryFn!({} as never);
    await hireAgent("c1", { role: "editor", display_name: "Eli", description: null, tools: [], per_run_usd: null, avatar_key: "default" }, api);
    expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
      "GET http://api/api/roles",
      "POST http://api/api/companies/c1/agents",
    ]);
  });
});
