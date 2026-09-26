"use client";

// /admin/login and /admin/login/verify (D-055). An address on ADMIN_EMAILS gets a one-time
// link; the verify page swaps it for the API's cookie and goes on to the page that was asked
// for. The operator token stays as a way in when email does not work.
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { afterLogin, fetchAdminMe, requestAdminLink, verifyAdmin } from "./adminAuth";
import { storeToken } from "./TokenGate";

type State = "idle" | "sending" | "sent" | "failed";

export function AdminLogin() {
  const next = useSearchParams()?.get("next") ?? null;
  const [email, setEmail] = useState("");
  const [state, setState] = useState<State>("idle");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setState("sending");
    try {
      await requestAdminLink(email.trim(), next && next.startsWith("/admin") ? next : null);
      setState("sent");
    } catch {
      setState("failed");
    }
  }

  return (
    <Card>
      <h1 className="text-2xl font-semibold">登入後台</h1>
      <p className="text-sm leading-relaxed text-muted">
        輸入管理員的 email，我們會寄一個 15 分鐘內有效、只能用一次的登入連結。只有列在後台管理員名單上的信箱會收到信。
      </p>
      {state === "sent" ? (
        <p className="rounded-lg border border-line bg-canvas p-4 text-sm" role="status" data-testid="admin-login-sent">
          如果這個信箱是管理員，登入連結已經寄出，請到信箱點連結。沒收到的話，看一下垃圾郵件，或確認信箱有沒有打錯。
        </p>
      ) : (
        <form onSubmit={submit} className="grid gap-3">
          <label className="grid gap-1.5 text-sm">
            Email
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="rounded-lg border border-line bg-canvas px-3 py-2 text-ink outline-none focus:border-accent"
            />
          </label>
          <button
            type="submit"
            disabled={state === "sending"}
            className="rounded-lg bg-accent px-4 py-2.5 font-semibold text-accent-ink disabled:opacity-50"
          >
            寄送登入連結
          </button>
          {state === "failed" ? <p className="text-sm text-danger">連結寄不出去，請稍後再試，或改用操作者權杖。</p> : null}
        </form>
      )}
      <TokenFallback next={next} />
    </Card>
  );
}

/** The way in when email does not work: the API_BEARER_TOKEN, kept in this browser only. */
function TokenFallback({ next }: { next: string | null }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [wrong, setWrong] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = draft.trim();
    if (!value) return;
    queryClient.clear(); // nothing fetched with another credential survives
    storeToken(value);
    if (await fetchAdminMe().catch(() => null)) {
      router.replace(afterLogin(next));
    } else {
      storeToken(null);
      setWrong(true);
    }
  }

  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-muted">改用操作者權杖</summary>
      <form onSubmit={submit} className="mt-3 grid gap-2">
        <p className="text-xs leading-relaxed text-muted">
          後端 <code>.env</code> 的 <code>API_BEARER_TOKEN</code>，給腳本或寄信出問題時使用；只存在這個瀏覽器。
        </p>
        <input
          type="password"
          autoComplete="off"
          aria-label="操作者權杖"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setWrong(false);
          }}
          className="rounded-lg border border-line bg-canvas px-3 py-2 text-ink outline-none focus:border-accent"
        />
        <button type="submit" disabled={!draft.trim()} className="rounded-lg border border-line px-4 py-2 disabled:opacity-50">
          用權杖進入
        </button>
        {wrong ? <p className="text-danger">權杖不對。</p> : null}
      </form>
    </details>
  );
}

/**
 * The other end of the link. A one-time token and an effect that may run twice (React's
 * development double-invoke, a refresh) do not get along, so the exchange happens once per token
 * and a failure asks who is signed in before believing it — as the site's verify page does.
 */
export function AdminVerify() {
  const params = useSearchParams();
  const token = params?.get("token") ?? null;
  const next = params?.get("next") ?? null;
  const router = useRouter();
  const queryClient = useQueryClient();
  const [failed, setFailed] = useState(false);
  const tried = useRef<string | null>(null);

  useEffect(() => {
    if (!token) {
      setFailed(true);
      return;
    }
    if (tried.current === token) return;
    tried.current = token;
    const onwards = () => {
      queryClient.clear();
      router.replace(afterLogin(next));
    };
    verifyAdmin(token)
      .then(onwards)
      .catch(async () => {
        const me = await fetchAdminMe().catch(() => null);
        if (me?.via === "email") onwards();
        else setFailed(true);
      });
  }, [token, next, router, queryClient]);

  return (
    <Card>
      <h1 className="text-2xl font-semibold">登入後台</h1>
      {failed ? (
        <>
          <p className="text-sm text-danger" role="alert">
            這個登入連結已經失效、用過了，或這個信箱不在管理員名單上。
          </p>
          <a href="/admin/login" className="text-sm text-accent underline">
            重新寄一個連結
          </a>
        </>
      ) : (
        <p className="text-sm text-muted">登入中…</p>
      )}
    </Card>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-screen place-items-center p-4">
      <div className="grid w-full max-w-md gap-4 rounded-2xl border border-line bg-surface p-8">{children}</div>
    </main>
  );
}
