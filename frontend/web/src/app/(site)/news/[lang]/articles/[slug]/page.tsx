import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";

import { fetchArticle } from "@/features/site/api";
import { ArticleView } from "@/features/site/ArticleView";
import { isLang, words } from "@/features/site/i18n";

export const revalidate = 30;

type Params = Promise<{ lang: string; slug: string }>;

// generateMetadata and the page ask for the same article: one request.
const load = cache((lang: string, slug: string) => fetchArticle(lang, slug));

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { lang, slug } = await params;
  if (!isLang(lang)) return {};
  const article = await load(lang, slug);
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
  const article = await load(lang, slug);
  if (!article) notFound();
  return <ArticleView article={article} lang={lang} />;
}
