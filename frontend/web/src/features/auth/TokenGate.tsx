"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";

import { getToken, setToken } from "@/api/auth";

import styles from "./auth.module.css";

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
    <main className={styles.page}>
      <form className={styles.card} onSubmit={submit}>
        <h1 className={styles.title}>Autora</h1>
        <p className={styles.hint}>
          請輸入操作者權杖（API 的 <code>API_BEARER_TOKEN</code>）。權杖只存在這個瀏覽器。
        </p>
        <label className={styles.label}>
          操作者權杖
          <input
            className={styles.input}
            type="password"
            autoComplete="off"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        </label>
        <button className={styles.button} type="submit" disabled={!draft.trim()}>
          進入
        </button>
      </form>
    </main>
  );
}
