"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";

import { getToken, setToken } from "@/api/auth";

// localStorage is not observable; this tiny subscription lets the gate re-render when the token
// changes in this tab (sign in / out).
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
export function storeToken(token: string | null): void {
  setToken(token);
  listeners.forEach((listener) => listener());
}

/**
 * MVP access: one operator token (API_BEARER_TOKEN), entered once per browser and kept in
 * localStorage (3d-office/05 §6). Children render only with a token; a 401 anywhere should
 * call storeToken(null) to come back here.
 */
export function TokenGate({ children }: { children: ReactNode }) {
  const token = useSyncExternalStore(subscribe, getToken, () => null);
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");

  if (token) return <>{children}</>;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = draft.trim();
    if (!value) return;
    queryClient.clear(); // nothing fetched with another token survives
    storeToken(value);
  };

  return (
    <main className="grid min-h-screen place-items-center p-4">
      <form onSubmit={submit} className="grid w-full max-w-md gap-4 rounded-2xl border border-line bg-surface p-8">
        <h1 className="text-2xl font-semibold">Autora</h1>
        <p className="text-sm leading-relaxed text-muted">
          這是 Autora 自己的 API 密碼：填入後端 <code>.env</code> 的 <code>API_BEARER_TOKEN</code>
          （開發環境預設為 <code>change-me</code>）。它不是 NVIDIA 或 Anthropic 的金鑰——那些只留在後端，
          瀏覽器永遠看不到。權杖只存在這個瀏覽器。
        </p>
        <label className="grid gap-1.5 text-sm">
          操作者權杖
          <input
            className="rounded-lg border border-line bg-canvas px-3 py-2 text-ink outline-none focus:border-accent"
            type="password"
            autoComplete="off"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        </label>
        <button
          type="submit"
          disabled={!draft.trim()}
          className="rounded-lg bg-accent px-4 py-2.5 font-semibold text-accent-ink disabled:cursor-default disabled:opacity-50"
        >
          進入
        </button>
      </form>
    </main>
  );
}
