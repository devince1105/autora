// Who is reading, in the site's header (D-025). Asked from the browser so the pages themselves
// stay cacheable: a signed-out visitor and a member get the same HTML, and this fills in after.
"use client";

import { useEffect, useState } from "react";

import { SITE_COMPANY } from "@/config";

import { fetchMe, isMember, signOut, type Me } from "./auth";
import { formatDate, words, type Lang } from "./i18n";

type State = { status: "loading" | "ready"; me: Me | null };

export function MemberBadge({ lang }: { lang: Lang }) {
  const w = words(lang);
  const [{ status, me }, setState] = useState<State>({ status: "loading", me: null });

  useEffect(() => {
    let live = true;
    fetchMe(SITE_COMPANY)
      .then((answer) => live && setState({ status: "ready", me: answer }))
      .catch(() => live && setState({ status: "ready", me: null }));
    return () => {
      live = false;
    };
  }, []);

  if (status === "loading") return <span className="text-sm text-muted" aria-hidden />;

  if (!me) {
    return (
      <a
        href={`/news/${lang}/login`}
        className="rounded-full border border-line px-3 py-1 text-sm hover:border-accent hover:text-accent"
        data-testid="sign-in"
      >
        {w.signIn}
      </a>
    );
  }

  return (
    <span className="flex items-center gap-2 text-sm" data-testid="member-badge">
      {isMember(me) ? (
        <span className="rounded-full border border-line px-2 py-0.5" title={`${w.memberUntil} ${formatDate(lang, me.member_until!)}`}>
          {w.member}・{formatDate(lang, me.member_until!)}
        </span>
      ) : (
        <span className="hidden max-w-48 truncate text-muted sm:inline">{me.email}</span>
      )}
      <button
        type="button"
        className="text-accent underline"
        onClick={async () => {
          await signOut();
          setState({ status: "ready", me: null });
        }}
      >
        {w.signOut}
      </button>
    </span>
  );
}
