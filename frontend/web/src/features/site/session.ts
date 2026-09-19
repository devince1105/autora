// Counting readers without knowing who they are (T-515; platform/05 §8). The browser makes a random
// id each day and keeps it only for that day: it cannot be tied to a person or followed across
// days, and nothing else about the reader is sent. The API counts a session once per article,
// language, kind and day.
import { API_URL } from "@/config";

export type BeaconKind = "view" | "read_complete";

const KEY = "autora.site.session";

export interface SessionStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function randomId(random: () => Uint8Array): string {
  return Array.from(random(), (b) => b.toString(16).padStart(2, "0")).join("");
}

const cryptoRandom = () => crypto.getRandomValues(new Uint8Array(16));

/** Today's session id (32 hex characters), made on the first page of the day. */
export function sessionHash(
  now: Date,
  store: SessionStore | null,
  random: () => Uint8Array = cryptoRandom,
): string {
  const day = now.toISOString().slice(0, 10);
  try {
    const saved = store ? (JSON.parse(store.getItem(KEY) ?? "null") as { day?: string; id?: string } | null) : null;
    if (saved?.day === day && typeof saved.id === "string" && /^[0-9a-f]{32}$/.test(saved.id)) {
      return saved.id;
    }
    const id = randomId(random);
    store?.setItem(KEY, JSON.stringify({ day, id }));
    return id;
  } catch {
    return randomId(random); // storage blocked: this page's own id
  }
}

export interface BeaconBody {
  article_id: string;
  lang: string;
  event_type: BeaconKind;
  session_hash: string;
}

/** Best-effort: a lost beacon is a missed count, never an error for the reader. */
export function sendBeacon(body: BeaconBody, send: typeof fetch = fetch): void {
  void send(`${API_URL}/api/analytics/beacon`, {
    method: "POST",
    keepalive: true,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => undefined);
}
