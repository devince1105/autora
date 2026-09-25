import { notFound } from "next/navigation";

import { fetchArticles } from "@/features/site/api";
import { ArticleList, PAGE_SIZE } from "@/features/site/ArticleList";
import { filterName, isFilter, isLang, sectionsOf, words } from "@/features/site/i18n";

export const revalidate = 30;

type Params = Promise<{ lang: string }>;
type Search = Promise<{ [key: string]: string | string[] | undefined }>;

async function where(searchParams: Search) {
  const query = await searchParams;
  const section = isFilter(query.section) ? query.section : null;
  const page = Math.min(Math.max(Number.parseInt(String(query.page ?? "1"), 10) || 1, 1), 500);
  return { section, page };
}

export async function generateMetadata({ params, searchParams }: { params: Params; searchParams: Search }) {
  const { lang } = await params;
  if (!isLang(lang)) return {};
  const { section } = await where(searchParams);
  const w = words(lang);
  return { title: section ? `${filterName(lang, section)} · ${w.site}` : w.site };
}

export default async function Page({ params, searchParams }: { params: Params; searchParams: Search }) {
  const { lang } = await params;
  if (!isLang(lang)) notFound();
  const { section, page } = await where(searchParams);
  // one more than a page: whether it comes back says whether there is a next page
  const found = await fetchArticles(lang, {
    company: process.env.SITE_COMPANY || undefined,
    section: section ? sectionsOf(section) : undefined,
    limit: PAGE_SIZE + 1,
    offset: (page - 1) * PAGE_SIZE,
  });
  return (
    <ArticleList
      articles={found.slice(0, PAGE_SIZE)}
      lang={lang}
      section={section}
      page={page}
      hasMore={found.length > PAGE_SIZE}
    />
  );
}
