import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";

import { fetchStock } from "@/features/site/api";
import { isLang, words } from "@/features/site/i18n";
import { StockView } from "@/features/site/StockView";

// its figure changes every few minutes, its holders a few times a year: a minute is fresh enough
export const revalidate = 60;

type Params = Promise<{ lang: string; symbol: string }>;

const load = cache((symbol: string, lang: string) =>
  fetchStock(symbol, lang, { company: process.env.SITE_COMPANY || undefined }),
);

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { lang, symbol } = await params;
  if (!isLang(lang)) return {};
  const stock = await load(symbol, lang);
  if (!stock) return {};
  return { title: `${stock.name} ${stock.symbol} · ${words(lang).site}` };
}

export default async function Page({ params }: { params: Params }) {
  const { lang, symbol } = await params;
  if (!isLang(lang)) notFound();
  const stock = await load(symbol, lang);
  if (!stock) notFound();
  return <StockView stock={stock} lang={lang} />;
}
