// Every public page ends with who runs the site and where its policies are (D-034). A payment
// provider reviewing the site looks here first, and so does a reader deciding whether to pay.
import Link from "next/link";

import type { Operator } from "./operator";
import { words, type Lang } from "./i18n";

export function SiteFooter({ lang, operator }: { lang: Lang; operator: Operator }) {
  const w = words(lang);
  const links = [
    ["pricing", w.pricing],
    ["terms", w.terms],
    ["privacy", w.privacy],
    ["refund", w.refund],
  ] as const;
  return (
    <footer data-testid="site-footer" className="mt-12 border-t border-line">
      <div className="mx-auto max-w-2xl px-4 py-6 text-sm text-muted">
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
      </div>
    </footer>
  );
}
