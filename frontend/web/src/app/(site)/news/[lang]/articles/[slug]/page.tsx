import type { Metadata } from "next";
import { cookies } from "next/headers";
import { notFound } from "next/navigation";
import { cache } from "react";

import { fetchArticle } from "@/features/site/api";
import { ArticleView } from "@/features/site/ArticleView";
import { isLang, words } from "@/features/site/i18n";

// Rendered per request, not cached: whether the rest of a members-only article is in the page
// depends on who is asking (D-025), and a cached page would answer for the wrong reader.
export const dynamic = "force-dynamic";

type Params = Promise<{ lang: string; slug: string }>;

// generateMetadata and the page ask for the same article: one request.
const load = cache((lang: string, slug: string, cookie: string) => fetchArticle(lang, slug, { cookie }));

async function readerCookie(): Promise<string> {
  const jar = await cookies();
  const session = jar.get("autora_reader");
  return session ? `autora_reader=${session.value}` : "";
}

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { lang, slug } = await params;
  if (!isLang(lang)) return {};
  const article = await load(lang, slug, await readerCookie());
  if (!article) return {};
  return {
    title: `${article.title} · ${words(lang).site}`,
    description: article.summary ?? undefined,
    alternates: { canonical: article.path, languages: article.langs },
  };
}

export default async function Page({ params }: { params: Params }) {
  const { lang, slug } = await params;
  if (!isLang(lang)) notFound();
  const article = await load(lang, slug, await readerCookie());
  if (!article) notFound();
  return <ArticleView article={article} lang={lang} />;
}
