// Typed REST client (T-308). Paths, parameters and response bodies come from the API's own
// OpenAPI document (src/api/openapi.json -> src/api/schema.gen.ts via `make gen-api`), so a
// renamed field breaks the build instead of a page.
import createClient, { type Client } from "openapi-fetch";

import { API_URL } from "@/config";

import { getToken } from "./auth";
import type { components, paths } from "./schema.gen";

export type Schemas = components["schemas"];
export type ApiClient = Client<paths>;

/** A failed request, with the RFC 7807 problem the API returned. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly title: string,
    readonly detail: string | undefined,
    readonly body: unknown,
  ) {
    super(detail ? `${status} ${title}: ${detail}` : `${status} ${title}`);
  }

  static from(response: Response, body: unknown): ApiError {
    const problem = (body && typeof body === "object" ? body : {}) as {
      title?: string;
      detail?: string;
    };
    return new ApiError(response.status, problem.title ?? response.statusText, problem.detail, body);
  }
}

export interface ApiClientOptions {
  baseUrl?: string;
  getToken?: () => string | null;
  fetch?: typeof fetch;
}

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const client = createClient<paths>({
    baseUrl: options.baseUrl ?? API_URL,
    fetch: options.fetch,
    // the back office's sign-in is the API's cookie (D-055); the token header still works too
    credentials: "include",
  });
  const token = options.getToken ?? getToken;
  client.use({
    onRequest({ request }) {
      const value = token();
      if (value) request.headers.set("Authorization", `Bearer ${value}`);
      return request;
    },
  });
  return client;
}

/** Unwrap an openapi-fetch result: the data, or an ApiError. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    throw ApiError.from(result.response, result.error);
  }
  return result.data as T;
}

/** The app's client. */
export const api = createApiClient();
