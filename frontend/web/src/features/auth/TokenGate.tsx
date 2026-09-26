"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore, type ReactNode } from "react";

import { getToken, setToken } from "@/api/auth";

import { fetchAdminMe, loginHref, signOutAdmin } from "./adminAuth";

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

export const ADMIN_ME_KEY = ["admin-me"] as const;

/**
 * The back office's door (D-055). Children render for an admin signed in with an emailed link
 * (the API's cookie) or for the operator token; anybody else is sent to /admin/login and back.
 * A 401 anywhere calls storeToken(null), which asks again.
 */
export function TokenGate({ children }: { children: ReactNode }) {
  const token = useSyncExternalStore(subscribe, getToken, () => null);
  const router = useRouter();
  const pathname = usePathname();
  const me = useQuery({ queryKey: [...ADMIN_ME_KEY, token], queryFn: fetchAdminMe, retry: false, staleTime: 60_000 });
  const signedOut = me.isSuccess && me.data === null;

  useEffect(() => {
    if (!signedOut) return;
    // the query from the address bar, not useSearchParams: that would need a Suspense boundary
    // around every page this gate wraps
    router.replace(loginHref(`${pathname}${window.location.search}`));
  }, [signedOut, pathname, router]);

  if (me.data) {
    return (
      <>
        <AdminBar email={me.data.email} />
        {children}
      </>
    );
  }
  return (
    <main className="grid min-h-screen place-items-center p-4 text-sm text-muted">
      {me.isError ? "連不到後端 API。" : "確認登入中…"}
    </main>
  );
}

function AdminBar({ email }: { email: string | null }) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const signOut = async () => {
    await signOutAdmin().catch(() => undefined);
    storeToken(null);
    queryClient.clear(); // nothing fetched as this admin survives
    router.replace("/admin/login");
  };
  return (
    <div className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-6xl items-center justify-end gap-3 px-4 py-1.5 text-xs text-muted">
        <span data-testid="admin-who">{email ?? "操作者權杖"}</span>
        <button type="button" onClick={signOut} className="text-accent underline">
          登出
        </button>
      </div>
    </div>
  );
}
