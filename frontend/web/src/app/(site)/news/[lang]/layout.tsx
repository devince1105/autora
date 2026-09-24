// The public site (T-515): /news/{lang}/..., readable without signing in. The document's language is
// set here for its part of the page (the root layout serves the admin pages too).
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { MemberBadge } from "@/features/site/MemberBadge";
import { isLang, LANG_NAMES, LANGS, words } from "@/features/site/i18n";
import { operator } from "@/features/site/operator";
import { SiteFooter } from "@/features/site/SiteFooter";

export default async function SiteLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ lang: string }>;
}) {
  const { lang } = await params;
  if (!isLang(lang)) notFound();
  return (
    <div lang={lang} className="min-h-screen bg-surface text-ink">
      <header className="border-b border-line">
        <nav className="mx-auto flex max-w-2xl items-center justify-between px-4 py-3">
          <Link href={`/news/${lang}`} className="font-bold">
            {words(lang).site}
          </Link>
          <span className="flex items-center gap-4 text-sm">
            {LANGS.filter((other) => other !== lang).map((other) => (
              <Link key={other} href={`/news/${other}`} hrefLang={other} className="text-accent underline">
                {LANG_NAMES[other]}
              </Link>
            ))}
            <MemberBadge lang={lang} />
          </span>
        </nav>
      </header>
      <main>{children}</main>
      <SiteFooter lang={lang} operator={operator()} />
    </div>
  );
}
