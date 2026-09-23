// What a reader sees instead of the rest of a members-only story (D-025).
//
// Two ways on: becoming a member, and signing in as one — a reader who has already paid should
// not have to work out that "sign in" is the one for them. Pressing "become a member" opens an
// order and hands the reader to PAYUNi's payment page (T-702); a site with no store yet says so
// instead of pretending to be a dead end, and a reader who is not signed in is sent to sign in
// first, because an order has to be for somebody.
"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { CheckoutError, fetchOffer, formatOffer, goToPaymentPage, startCheckout, type Offer } from "./checkout";
import { MEMBERSHIP_PRICE_TWD } from "./membership";
import { words, type Lang } from "./i18n";

type State = "idle" | "starting" | "unavailable" | "failed";

export function MembersOnly({ lang, path, company }: { lang: Lang; path: string; company?: string }) {
  const w = words(lang);
  const [offer, setOffer] = useState<Offer | null>(null);
  const [state, setState] = useState<State>("idle");
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    void fetchOffer(company).then((found) => {
      if (alive.current) setOffer(found);
    });
    return () => {
      alive.current = false;
    };
  }, [company]);

  const price = offer
    ? formatOffer(offer, lang)
    : `NT$${new Intl.NumberFormat(lang, { maximumFractionDigits: 0 }).format(MEMBERSHIP_PRICE_TWD)}`;
  const loginHref = `/news/${lang}/login?next=${encodeURIComponent(path)}`;

  async function buy() {
    setState("starting");
    try {
      goToPaymentPage(await startCheckout(lang, company));
    } catch (error) {
      if (!alive.current) return;
      if (error instanceof CheckoutError && error.problem === "signed-out") {
        window.location.assign(loginHref); // sign in, then come back and press it again
        return;
      }
      setState(error instanceof CheckoutError && error.problem === "unavailable" ? "unavailable" : "failed");
    }
  }

  const note = state === "unavailable" ? w.membersSoon : state === "failed" ? w.membersFailed : null;

  return (
    <aside data-testid="members-only" className="mt-8 rounded-lg border border-line bg-surface p-6 text-center">
      <h2 className="text-lg font-semibold">{w.membersOnly}</h2>
      <p className="mt-2 text-muted">{w.membersOnlyWhy(price)}</p>
      <p className="mt-1 text-sm text-muted">{w.membersOnlyAlready}</p>
      <div className="mt-4 flex justify-center gap-3">
        <button
          type="button"
          onClick={() => void buy()}
          disabled={state === "starting"}
          aria-describedby={note ? "members-note" : undefined}
          className="rounded-lg bg-accent px-4 py-2 font-medium text-canvas disabled:opacity-60"
        >
          {state === "starting" ? w.membersStarting : w.becomeMember}
        </button>
        <Link href={loginHref} className="rounded-lg border border-line px-4 py-2">
          {w.signIn}
        </Link>
      </div>
      {note ? (
        <p id="members-note" role="status" className="mt-3 text-sm text-muted">
          {note}
        </p>
      ) : null}
    </aside>
  );
}
