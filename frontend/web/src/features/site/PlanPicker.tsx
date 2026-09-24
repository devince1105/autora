// A month or a year, side by side, each a button that opens PAYUNi's page (D-034).
//
// Used by the paywall under a locked story and by the pricing page. Each plan's price is what
// the API charges; the fallback numbers only fill the moment before it answers. A plan the API
// says is not for sale is left out rather than shown at a price nobody can pay — and when
// neither answered, both are shown with the fallback, so pressing one says "not open yet"
// instead of the page having nothing to press.
"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { CheckoutError, fetchOffer, formatOffer, goToPaymentPage, startCheckout, type Interval, type Offer } from "./checkout";
import { MEMBERSHIP_PRICES_TWD } from "./membership";
import { words, type Lang } from "./i18n";

const INTERVALS: readonly Interval[] = ["month", "year"];

type State = "idle" | "starting" | "unavailable" | "failed";
type Offers = Record<Interval, Offer | null>;

function fallback(lang: Lang, interval: Interval): string {
  return `NT$${new Intl.NumberFormat(lang, { maximumFractionDigits: 0 }).format(MEMBERSHIP_PRICES_TWD[interval])}`;
}

/** How much a year saves against twelve months, in whole percent; null when it saves nothing. */
export function yearlySaving(month: number, year: number): number | null {
  const twelve = month * 12;
  if (!(twelve > 0) || year >= twelve) return null;
  return Math.round(((twelve - year) / twelve) * 100);
}

export function PlanPicker({ lang, loginHref, company }: { lang: Lang; loginHref: string; company?: string }) {
  const w = words(lang);
  const [offers, setOffers] = useState<Offers | null>(null);
  const [state, setState] = useState<State>("idle");
  const [chosen, setChosen] = useState<Interval | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    void Promise.all(INTERVALS.map((interval) => fetchOffer(company, interval))).then(([month, year]) => {
      if (alive.current) setOffers({ month, year });
    });
    return () => {
      alive.current = false;
    };
  }, [company]);

  async function buy(interval: Interval) {
    setChosen(interval);
    setState("starting");
    try {
      goToPaymentPage(await startCheckout(lang, company, interval));
    } catch (error) {
      if (!alive.current) return;
      if (error instanceof CheckoutError && error.problem === "signed-out") {
        window.location.assign(loginHref); // sign in, then come back and press it again
        return;
      }
      setState(error instanceof CheckoutError && error.problem === "unavailable" ? "unavailable" : "failed");
    }
  }

  const answered = offers !== null && (offers.month !== null || offers.year !== null);
  const shown = INTERVALS.filter((interval) => !answered || offers[interval] !== null);
  const price = (interval: Interval) => {
    const offer = offers?.[interval];
    return offer ? formatOffer(offer, lang) : fallback(lang, interval);
  };
  const amount = (interval: Interval) => Number(offers?.[interval]?.amount ?? MEMBERSHIP_PRICES_TWD[interval]);
  const saving = shown.length === 2 ? yearlySaving(amount("month"), amount("year")) : null;
  const note = state === "unavailable" ? w.membersSoon : state === "failed" ? w.membersFailed : null;

  return (
    <div>
      <ul className="grid gap-3 sm:grid-cols-2">
        {shown.map((interval) => (
          <li
            key={interval}
            data-testid={`plan-${interval}`}
            className="flex flex-col rounded-lg border border-line bg-canvas p-4 text-left"
          >
            <span className="flex items-baseline justify-between gap-2">
              <span className="font-semibold">{w.planName[interval]}</span>
              {interval === "year" && saving ? (
                <span className="text-xs text-accent">{w.planSaving(saving)}</span>
              ) : null}
            </span>
            <span className="mt-2 text-2xl font-bold">
              {price(interval)}
              <span className="ml-1 text-sm font-normal text-muted">/ {w.planTerm[interval]}</span>
            </span>
            <button
              type="button"
              onClick={() => void buy(interval)}
              disabled={state === "starting"}
              aria-describedby={note ? "plan-note" : undefined}
              className="mt-4 rounded-lg bg-accent px-4 py-2 font-medium text-canvas disabled:opacity-60"
            >
              {state === "starting" && chosen === interval ? w.membersStarting : w.planChoose[interval]}
            </button>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-sm text-muted">{w.planOnce}</p>
      <p className="mt-1 text-sm text-muted">
        {w.planAgree[0]}
        <Link href={`/news/${lang}/terms`} className="text-accent underline">
          {w.terms}
        </Link>
        {w.planAgree[1]}
        <Link href={`/news/${lang}/refund`} className="text-accent underline">
          {w.refund}
        </Link>
        {w.planAgree[2]}
      </p>
      {note ? (
        <p id="plan-note" role="status" className="mt-2 text-sm text-muted">
          {note}
        </p>
      ) : null}
    </div>
  );
}
