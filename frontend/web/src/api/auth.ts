// Operator token (MVP: one operator, one bearer token; 3d-office/05 §6, platform/12 §Admin).
//
// Kept in the browser's localStorage after the operator enters it, never baked into the
// bundle: anything in NEXT_PUBLIC_* is readable by everyone who loads the page. Phase 6 replaces
// this with a session cookie and per-company access.
const KEY = "autora.operatorToken";

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null; // private mode or blocked storage
  }
}

export function getToken(): string | null {
  return storage()?.getItem(KEY) ?? null;
}

export function setToken(token: string | null): void {
  const store = storage();
  if (!store) return;
  if (token) store.setItem(KEY, token);
  else store.removeItem(KEY);
}
