// Asking for a login link (D-025). One field, no password, and the same answer whatever the
// address is — the page must not become a way to ask who reads here.
"use client";

import { use, useState } from "react";

import { requestLink } from "@/features/site/auth";
import { isLang, words, type Lang } from "@/features/site/i18n";

type State = "idle" | "sending" | "sent" | "failed";

export default function LoginPage({
  params,
  searchParams,
}: {
  params: Promise<{ lang: string }>;
  searchParams: Promise<{ next?: string }>;
}) {
  const { lang } = use(params);
  const { next } = use(searchParams);
  const language: Lang = isLang(lang) ? lang : "zh-TW";
  const w = words(language);
  const [email, setEmail] = useState("");
  const [state, setState] = useState<State>("idle");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setState("sending");
    try {
      await requestLink(email, next?.startsWith("/") ? next : undefined);
      setState("sent");
    } catch {
      setState("failed");
    }
  }

  return (
    <section className="mx-auto max-w-md px-4 py-12">
      <h1 className="text-2xl font-bold">{w.loginTitle}</h1>
      <p className="mt-3 text-muted">{w.loginHint}</p>
      {state === "sent" ? (
        <p className="mt-6 rounded-lg border border-line bg-surface p-4" data-testid="login-sent">
          {w.loginSent}
        </p>
      ) : (
        <form onSubmit={submit} className="mt-6 flex flex-col gap-3">
          <input
            type="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-label="email"
            placeholder="you@example.com"
            className="rounded-lg border border-line bg-surface px-3 py-2"
          />
          <button
            type="submit"
            disabled={state === "sending"}
            className="rounded-lg bg-accent px-4 py-2 font-semibold text-surface disabled:opacity-60"
          >
            {w.loginSend}
          </button>
          {state === "failed" ? <p className="text-sm text-warn">{w.loginFailed}</p> : null}
        </form>
      )}
    </section>
  );
}
