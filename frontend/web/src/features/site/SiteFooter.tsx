// Every public page ends with who runs the site and where its policies are (D-034). A payment
// provider reviewing the site looks here first, and so does a reader deciding whether to pay.
import Link from "next/link";

import type { Operator } from "./operator";
import { words, type Lang } from "./i18n";

export function SiteFooter({
  lang,
  operator,
  membershipOpen = false,
  marketSources = [],
}: {
  lang: Lang;
  operator: Operator;
  membershipOpen?: boolean;
  /** Whose figures the market strip shows, to credit them (D-048; CoinGecko asks it). */
  marketSources?: string[];
}) {
  const w = words(lang);
  // while everything is free there is nothing to price and nothing to refund (D-035)
  const links = (
    [
      ["pricing", w.pricing, membershipOpen],
      ["terms", w.terms, true],
      ["privacy", w.privacy, true],
      ["refund", w.refund, membershipOpen],
    ] as const
  ).filter(([, , shown]) => shown);
  return (
    <footer data-testid="site-footer" className="mt-12 border-t border-line bg-canvas print:hidden">
      <div className="mx-auto max-w-5xl px-4 py-8 text-sm text-muted">
        <p className="mb-3 flex items-baseline gap-3">
          <span className="font-display text-base font-bold text-ink">{w.site}</span>
          <span className="text-xs">{w.tagline}</span>
        </p>
        <nav className="flex flex-wrap gap-x-4 gap-y-1">
          {links.map(([page, label]) => (
            <Link key={page} href={`/news/${lang}/${page}`} className="underline">
              {label}
            </Link>
          ))}
        </nav>
        <p className="mt-3">
          {w.operator}
          {w.sep}
          {operator.brand}
          {operator.owner ? w.aside(operator.owner) : null}
        </p>
        <p>
          {w.contact}
          {w.sep}
          <a href={`mailto:${operator.email}`} className="underline">{operator.email}</a>
          {operator.phone ? (
            <>
              {" ・ "}
              {w.phone}
              {w.sep}
              {operator.phone}
            </>
          ) : null}
        </p>
        {marketSources.length ? (
          <p className="mt-3 text-xs">{w.marketsCredit(marketSources.map((s) => w.sourceNames[s] ?? s))}</p>
        ) : null}
      </div>
    </footer>
  );
}
