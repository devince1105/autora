import { notFound } from "next/navigation";

import { fetchArticles } from "@/features/site/api";
import { ArticleList } from "@/features/site/ArticleList";
import { isLang, words } from "@/features/site/i18n";

export const revalidate = 30;

export async function generateMetadata({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  return isLang(lang) ? { title: words(lang).site } : {};
}

export default async function Page({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!isLang(lang)) notFound();
  const articles = await fetchArticles(lang, { company: process.env.SITE_COMPANY || undefined });
  return <ArticleList articles={articles} lang={lang} />;
}
