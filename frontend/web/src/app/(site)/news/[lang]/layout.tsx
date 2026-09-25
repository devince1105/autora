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
      <header className="sticky top-0 z-30 border-b border-line bg-surface/85 backdrop-blur-md print:static print:border-0">
        <nav className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-4">
          <Link href={`/news/${lang}`} className="flex min-w-0 items-baseline gap-3">
            <span className="font-display text-xl font-bold tracking-wide">{w.site}</span>
            <span className="hidden truncate text-xs text-muted sm:inline">{w.tagline}</span>
          </Link>
          <span className="flex shrink-0 items-center gap-1 text-sm print:hidden">
            {LANGS.filter((other) => other !== lang).map((other) => (
              <Link
                key={other}
                href={`/news/${other}`}
                hrefLang={other}
                className="rounded-md px-2 py-1.5 text-muted hover:bg-canvas hover:text-ink"
              >
                {LANG_NAMES[other]}
              </Link>
            ))}
            <ThemeToggle lang={lang} />
            <span className="ml-2">
              <MemberBadge lang={lang} />
            </span>
          </span>
        </nav>
      </header>
      <MarketStrip quotes={quotes} lang={lang} />
      <main className="flex-1">{children}</main>
      <SiteFooter lang={lang} operator={operator()} membershipOpen={membershipOpen()} marketSources={[...new Set(quotes.map((q) => q.source))]} />
    </div>
  );
}
