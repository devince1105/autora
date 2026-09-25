// The public site's reads (T-515): no operator token, published articles only. Typed by the API's
// OpenAPI document like the admin client; used by server components, so it takes the server-side
// API URL.
import createClient from "openapi-fetch";

import { ApiError, type Schemas } from "@/api/client";
import type { paths } from "@/api/schema.gen";
import { SERVER_API_URL } from "@/config";

import type { Section } from "./i18n";

export type PublicArticle = Schemas["PublicArticle"];
export type PublicArticleSummary = Schemas["PublicArticleSummary"];

export interface SiteClientOptions {
  baseUrl?: string;
  fetch?: typeof fetch;
  /** The reader's cookie, forwarded when the server renders a page for them (D-025). Without
   * it the API cannot tell a member from anybody else, and a locked article stays locked. */
  cookie?: string;
}

function client(options: SiteClientOptions) {
  return createClient<paths>({
    baseUrl: options.baseUrl ?? SERVER_API_URL,
    fetch: options.fetch,
    headers: options.cookie ? { cookie: options.cookie } : undefined,
  });
}

/** A published article in ``lang``, or null when there is none (not published, or not in it). */
export async function fetchArticle(
  lang: string,
  slug: string,
  options: SiteClientOptions = {},
): Promise<PublicArticle | null> {
  const { data, error, response } = await client(options).GET("/api/public/articles/{lang}/{slug}", {
    params: { path: { lang, slug } },
  });
  if (response.status === 404) return null;
  if (error !== undefined || !data) throw ApiError.from(response, error);
  return data;
}

export interface ListOptions extends SiteClientOptions {
  company?: string;
  /** Only this section (D-047). */
  section?: Section;
  limit?: number;
  offset?: number;
}

/** Newest first. A page that comes back shorter than ``limit`` is the last. */
export async function fetchArticles(lang: string, options: ListOptions = {}): Promise<PublicArticleSummary[]> {
  const { company, section, limit, offset } = options;
  const { data, error, response } = await client(options).GET("/api/public/articles", {
    params: { query: { lang, company, section, limit, offset } },
  });
  if (error !== undefined || !data) throw ApiError.from(response, error);
  return data;
}
