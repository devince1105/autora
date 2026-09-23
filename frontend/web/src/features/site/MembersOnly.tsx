// What a reader sees instead of the rest of a members-only story (D-025).
//
// Two ways on: becoming a member, and signing in as one — a reader who has already paid should
// not have to work out that "sign in" is the one for them. Paying is not open yet (T-702), and
// the button says so when it is pressed rather than pretending to be a dead end.
"use client";

import Link from "next/link";
import { useState } from "react";

import { formatMembershipPrice } from "./membership";
import { words, type Lang } from "./i18n";

export function MembersOnly({ lang, path }: { lang: Lang; path: string }) {
  const w = words(lang);
  const [soon, setSoon] = useState(false);
  return (
    <aside data-testid="members-only" className="mt-8 rounded-lg border border-line bg-surface p-6 text-center">
      <h2 className="text-lg font-semibold">{w.membersOnly}</h2>
      <p className="mt-2 text-muted">{w.membersOnlyWhy(formatMembershipPrice(lang))}</p>
      <p className="mt-1 text-sm text-muted">{w.membersOnlyAlready}</p>
      <div className="mt-4 flex justify-center gap-3">
        <button
          type="button"
          onClick={() => setSoon(true)}
          aria-describedby={soon ? "members-soon" : undefined}
          className="rounded-lg bg-accent px-4 py-2 font-medium text-canvas"
        >
          {w.becomeMember}
        </button>
        <Link href={`/news/${lang}/login?next=${encodeURIComponent(path)}`} className="rounded-lg border border-line px-4 py-2">
          {w.signIn}
        </Link>
      </div>
      {soon ? (
        <p id="members-soon" role="status" className="mt-3 text-sm text-muted">
          {w.membersSoon}
        </p>
      ) : null}
    </aside>
  );
}
