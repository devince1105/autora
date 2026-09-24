// @vitest-environment jsdom
// Which company an admin page shows when the URL does not say (T-517 follow-up): one that has
// agents, so a leftover empty company does not greet the operator with an empty office.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CompanyScope, withCompany } from "./CompanyScope";

let search = "";
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(search) }));

const EMPTY = { id: "c-empty", slug: "smoke-old", name: "舊的 smoke", type: "newsroom", mission: null, status: "active", created_at: "2026-09-17T00:00:00Z", agents: 0 };
const STAFFED = { id: "c-newsroom", slug: "newsroom-demo", name: "流明日報", type: "newsroom", mission: null, status: "active", created_at: "2026-09-19T00:00:00Z", agents: 5 };

function show(companies: unknown[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["companies"], companies);
  return render(
    <QueryClientProvider client={client}>
      <CompanyScope>{(company) => <p>{company.name}</p>}</CompanyScope>
    </QueryClientProvider>,
  );
}

const FINANCE = { ...STAFFED, id: "c-finance", slug: "autora-finance", name: "Autora 財經", created_at: "2026-09-24T00:00:00Z" };

afterEach(() => {
  cleanup();
  search = "";
  window.localStorage.clear();
});

describe("the company a page shows", () => {
  it("prefers one with agents when the URL does not say", () => {
    show([EMPTY, STAFFED]);
    expect(screen.getByText("流明日報")).toBeTruthy();
  });

  it("still shows the only company there is, empty or not", () => {
    show([EMPTY]);
    expect(screen.getByText("舊的 smoke")).toBeTruthy();
  });

  it("obeys ?company=, even for an empty one", () => {
    search = `company=${EMPTY.id}`;
    show([EMPTY, STAFFED]);
    expect(screen.getByText("舊的 smoke")).toBeTruthy();
  });

  it("says so when the asked-for company is not there", () => {
    search = "company=c-gone";
    show([EMPTY, STAFFED]);
    expect(screen.getByText(/找不到公司 c-gone/)).toBeTruthy();
  });

  it("keeps the company in links between pages", () => {
    expect(withCompany("/office", "c-1")).toBe("/office?company=c-1");
  });

  it("keeps a link's own query and anchor, as the backend's activity links have (D-041)", () => {
    expect(withCompany("/newsroom/articles/a1?version=2#fact-check", "c-1")).toBe(
      "/newsroom/articles/a1?version=2&company=c-1#fact-check",
    );
    expect(withCompany("/newsroom/stories/s1#claims", "c-1")).toBe("/newsroom/stories/s1?company=c-1#claims");
    expect(withCompany("/office?company=old", "c-1")).toBe("/office?company=c-1");
  });

  it("opens on the company last looked at, not the oldest one with agents (D-041)", () => {
    search = `company=${FINANCE.id}`;
    show([STAFFED, FINANCE]);
    cleanup();
    search = ""; // a bookmark, a typed /office, a link that forgot the company
    show([STAFFED, FINANCE]);
    expect(screen.getByText("Autora 財經")).toBeTruthy();
  });

  it("forgets a remembered company that is gone", () => {
    window.localStorage.setItem("autora.admin.company", "c-deleted");
    show([EMPTY, STAFFED]);
    expect(screen.getByText("流明日報")).toBeTruthy();
  });
});
