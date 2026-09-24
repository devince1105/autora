// After PAYUNi's page: has the payment made them a member yet? (D-034)
//
// The browser often gets back before PAYUNi's notification reaches our server, so this asks the
// API a few times before saying "not yet". It only ever reports what the API says; nothing the
// browser brought back from PAYUNi counts.
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { SITE_COMPANY } from "@/config";

import { fetchMe, isMember } from "./auth";
import { formatDate, words, type Lang } from "./i18n";

type State = { status: "checking" } | { status: "member"; until: string } | { status: "waiting" | "signed-out" };

const TRIES = 10;
const EVERY_MS = 2000;

export function PaymentDone({ lang, email, every = EVERY_MS }: { lang: Lang; email: string; every?: number }) {
  const w = words(lang);
  const [state, setState] = useState<State>({ status: "checking" });

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function ask(tries: number) {
      const me = await fetchMe(SITE_COMPANY).catch(() => null);
      if (!live) return;
      if (me && isMember(me)) return setState({ status: "member", until: me.member_until! });
      if (!me && tries === TRIES) return setState({ status: "signed-out" });
      if (tries <= 1) return setState({ status: "waiting" });
      timer = setTimeout(() => void ask(tries - 1), every);
    }
    void ask(TRIES);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [every]);

  const message =
    state.status === "checking"
      ? w.doneChecking
      : state.status === "member"
        ? w.doneOk(formatDate(lang, state.until))
        : state.status === "signed-out"
          ? w.doneSignedOut
          : w.doneWaiting(email);

  return (
    <article className="mx-auto max-w-2xl px-4 py-8">
      <h1 className="text-2xl font-bold">{w.doneTitle}</h1>
      <p role="status" data-testid="payment-done" className="mt-3 leading-relaxed">
        {message}
      </p>
      <Link href={`/news/${lang}`} className="mt-6 inline-block text-accent underline">
        {w.doneBack}
      </Link>
    </article>
  );
}
