// What a reader sees instead of the rest of a members-only story (D-025, D-034).
//
// Two ways on: becoming a member, a month or a year at a time, and signing in as one — a reader
// who has already paid should not have to work out that "sign in" is the one for them. Choosing
// a plan opens an order and hands the reader to PAYUNi's payment page (T-702); a site with no
// store yet says so instead of pretending to be a dead end, and a reader who is not signed in is
// sent to sign in first, because an order has to be for somebody.
"use client";

import Link from "next/link";

import { PlanPicker } from "./PlanPicker";
import { words, type Lang } from "./i18n";

export function MembersOnly({ lang, path, company }: { lang: Lang; path: string; company?: string }) {
  const w = words(lang);
  const loginHref = `/news/${lang}/login?next=${encodeURIComponent(path)}`;

  return (
    <aside data-testid="members-only" className="mt-8 rounded-lg border border-line bg-surface p-6">
      <h2 className="text-center text-lg font-semibold">{w.membersOnly}</h2>
      <p className="mt-2 text-center text-muted">{w.membersOnlyWhy}</p>
      <div className="mt-4">
        <PlanPicker lang={lang} loginHref={loginHref} company={company} />
      </div>
      <p className="mt-4 text-center text-sm text-muted">
        {w.membersOnlyAlready}{" "}
        <Link href={loginHref} className="text-accent underline">
          {w.signIn}
        </Link>
      </p>
    </aside>
  );
}
