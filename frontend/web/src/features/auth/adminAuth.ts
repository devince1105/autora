// Signing in to the back office (D-055): an emailed link for the addresses on ADMIN_EMAILS, kept
// as the API's httpOnly cookie. Every call sends it with `credentials: "include"`; the operator
// token (localStorage) still works beside it, for the day email does not.
import { API_URL } from "@/config";

import { getToken } from "@/api/auth";

export interface AdminMe {
  via: "email" | "token";
  email: string | null;
}

async function call(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  return fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });
}

/** Ask for a link. The API answers 202 for any address, so this never says who the admins are. */
export async function requestAdminLink(email: string, nextPath: string | null): Promise<void> {
  const response = await call("/api/admin/auth/link", {
    method: "POST",
    body: JSON.stringify({ email, next_path: nextPath }),
  });
  if (!response.ok) throw new Error(`link request failed (${response.status})`);
}

export async function verifyAdmin(token: string): Promise<AdminMe> {
  const response = await call("/api/admin/auth/verify", { method: "POST", body: JSON.stringify({ token }) });
  if (!response.ok) throw new Error(`verify failed (${response.status})`);
  return (await response.json()) as AdminMe;
}

/** Who is calling the back office; null when nobody may. */
export async function fetchAdminMe(): Promise<AdminMe | null> {
  const response = await call("/api/admin/auth/me");
  if (response.status === 401) return null;
  if (!response.ok) throw new Error(`me failed (${response.status})`);
  return (await response.json()) as AdminMe;
}

export async function signOutAdmin(): Promise<void> {
  await call("/api/admin/auth/logout", { method: "POST" });
}

/** Where a signed-out visit to ``path`` goes: the login page, and back afterwards. */
export function loginHref(path: string | null): string {
  return path && path.startsWith("/admin") && !path.startsWith("/admin/login")
    ? `/admin/login?next=${encodeURIComponent(path)}`
    : "/admin/login";
}

/** Where a sign-in goes on to: a back-office page, never another address. */
export function afterLogin(next: string | null | undefined): string {
  return next && /^\/admin(\/|$)/.test(next) && !next.startsWith("/admin/login") ? next : "/admin/dashboard";
}
