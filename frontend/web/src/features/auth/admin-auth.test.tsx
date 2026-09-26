// @vitest-environment jsdom
// The back office's door (D-055): a signed-out visit goes to /admin/login and comes back, a
// signed-in admin sees the page and who they are, and the login form asks for a link.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
let search = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/admin/dashboard",
  useSearchParams: () => search,
}));

import { afterLogin, loginHref } from "./adminAuth";
import { AdminLogin } from "./AdminLogin";
import { storeToken, TokenGate } from "./TokenGate";

const calls: { url: string; init: RequestInit }[] = [];
function answer(status: number, body: unknown = null) {
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(body === null ? null : JSON.stringify(body), { status });
  });
}

function wrap(children: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>);
}

beforeEach(() => {
  calls.length = 0;
  replace.mockReset();
  search = new URLSearchParams();
  storeToken(null);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the back office's door", () => {
  it("sends a signed-out visit to the login page, and back afterwards", async () => {
    answer(401);
    wrap(<TokenGate><p>inside</p></TokenGate>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/admin/login?next=%2Fadmin%2Fdashboard"));
    expect(screen.queryByText("inside")).toBeNull();
    expect(calls[0].init.credentials).toBe("include");
  });

  it("shows the page and who is signed in", async () => {
    answer(200, { via: "email", email: "admin@aisiwhale.test" });
    wrap(<TokenGate><p>inside</p></TokenGate>);
    expect(await screen.findByText("inside")).toBeTruthy();
    expect(screen.getByTestId("admin-who").textContent).toBe("admin@aisiwhale.test");
    expect(screen.getByRole("button", { name: "登出" })).toBeTruthy();
  });

  it("goes back only to a page of the back office", () => {
    expect(afterLogin("/admin/approvals?company=c1")).toBe("/admin/approvals?company=c1");
    expect(afterLogin("https://evil.example/admin")).toBe("/admin/dashboard");
    expect(afterLogin("/news/zh-TW")).toBe("/admin/dashboard");
    expect(afterLogin("/admin/login")).toBe("/admin/dashboard");
    expect(loginHref("/admin/login")).toBe("/admin/login");
  });
});

describe("the login page", () => {
  it("asks for a link for the address, and says what happens next", async () => {
    answer(202);
    search = new URLSearchParams("next=/admin/approvals");
    wrap(<AdminLogin />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: " admin@aisiwhale.test " } });
    fireEvent.click(screen.getByRole("button", { name: "寄送登入連結" }));
    expect(await screen.findByTestId("admin-login-sent")).toBeTruthy();
    expect(calls[0].url).toMatch(/\/api\/admin\/auth\/link$/);
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ email: "admin@aisiwhale.test", next_path: "/admin/approvals" });
  });
});
