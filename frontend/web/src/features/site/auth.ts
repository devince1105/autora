// Signing in from the browser (D-025). The session is a cookie the API sets; every call here
// sends it with `credentials: "include"`, and none of them ever carries a password, because
// there is none: a link in the inbox is the whole login.
import { API_URL } from "@/config";

export interface Me {
  reader_id: string;
  email: string;
  member_until: string | null;
}

async function call(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
}

/** Ask for a login link. Answers the same way for an address that has an account and one that
 * does not, so this cannot be used to ask who reads here. */
export async function requestLink(email: string, nextPath?: string): Promise<void> {
  const response = await call("/api/auth/link", {
    method: "POST",
    body: JSON.stringify({ email, next_path: nextPath ?? null }),
  });
  if (!response.ok) throw new Error(`link request failed (${response.status})`);
}

export async function verify(token: string, company?: string): Promise<Me> {
  const query = company ? `?company=${encodeURIComponent(company)}` : "";
  const response = await call(`/api/auth/verify${query}`, {
    method: "POST",
    body: JSON.stringify({ token }),
  });
  if (!response.ok) throw new Error(`verify failed (${response.status})`);
  return (await response.json()) as Me;
}

export async function fetchMe(company?: string): Promise<Me | null> {
  const query = company ? `?company=${encodeURIComponent(company)}` : "";
  const response = await call(`/api/auth/me${query}`);
  if (!response.ok) return null;
  return (await response.json()) as Me | null;
}

export async function signOut(): Promise<void> {
  await call("/api/auth/logout", { method: "POST" });
}

export function isMember(me: Me | null, now: Date = new Date()): boolean {
  return me?.member_until != null && new Date(me.member_until) > now;
}
