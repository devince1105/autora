// Where the API lives. NEXT_PUBLIC_API_URL is baked in at build time (docker-compose sets it).
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

/** ws:// or wss:// for the same host as an http(s) API URL. */
export function toWebSocketUrl(apiUrl: string): string {
  return apiUrl.replace(/^http/, "ws");
}
