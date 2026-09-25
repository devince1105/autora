// The public site (T-515): /news/{lang}/..., readable without signing in. The document's language is
// set here for its part of the page (the root layout serves the admin pages too). Its type and its
// light or dark are the layout above's (D-047).
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { fetchMarkets } from "@/features/site/api";
import { MarketStrip } from "@/features/site/MarketStrip";
import { MemberBadge } from "@/features/site/MemberBadge";
import { isLang, LANG_NAMES, LANGS, words } from "@/features/site/i18n";
import { membershipOpen } from "@/features/site/membership";
import { operator } from "@/features/site/operator";
import { SectionNav } from "@/features/site/SectionNav";
import { SiteFooter } from "@/features/site/SiteFooter";
import { ThemeToggle } from "@/features/site/ThemeToggle";

export default async function SiteLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ lang: string }>;
}) {
  const { lang } = await params;
  if (!isLang(lang)) notFound();
  const w = words(lang);
  const quotes = await fetchMarkets();
  return (
    <div lang={lang} className="flex min-h-screen flex-col">
      {/* after the WSJ's: the market strip on top, the masthead, then the sections */}
      <MarketStrip quotes={quotes} lang={lang} />
      <header className="relative px-4 pt-5 pb-3 text-center print:pt-0">
        <span className="absolute top-3 right-4 flex items-center gap-1 text-sm print:hidden">
          <ThemeToggle lang={lang} />
          <MemberBadge lang={lang} />
        </span>
        <Link href={`/news/${lang}`} className="inline-block px-20 sm:px-0">
          <span className="block font-display text-3xl font-black tracking-wider sm:text-5xl">{w.site}</span>
        </Link>
        <p className="mt-1.5 flex items-center justify-center gap-3 text-xs text-muted">
          <span>{w.tagline}</span>
          <span aria-hidden className="text-line">|</span>
          {LANGS.filter((other) => other !== lang).map((other) => (
            <Link key={other} href={`/news/${other}`} hrefLang={other} className="hover:text-ink print:hidden">
              {LANG_NAMES[other]}
            </Link>
          ))}
        </p>
      </header>
      <SectionNav lang={lang} />
      <main className="flex-1">{children}</main>
      <SiteFooter lang={lang} operator={operator()} membershipOpen={membershipOpen()} marketSources={[...new Set(quotes.map((q) => q.source))]} />
    </div>
  );
}
