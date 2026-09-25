// The strip of market figures under the site's header (D-048), after Bloomberg's: name, figure,
// change, red for up and green for down as Taiwanese readers expect. Taiwan, US indices and
// stocks, rates, oil and crypto, all from the API in one form. It drifts slowly to the left and
// loops (drift.ts), stopping under the pointer; it also scrolls by hand (arrows on a wide screen,
// a swipe on a phone). Every figure says what it is — a close, the previous close, the latest,
// 24 hours.
"use client";

import Link from "next/link";
import { useRef } from "react";

import type { PublicQuote } from "./api";
import { useDrift } from "./drift";
import { words, type Lang } from "./i18n";
import { ARROW, direction, formatChange, formatValue, label, stockPage, TONE } from "./quote";


function Quote({ quote, lang, copy = false }: { quote: PublicQuote; lang: Lang; copy?: boolean }) {
  const w = words(lang);
  const way = direction(quote);
  const change = formatChange(quote);
  const [name, code] = label(quote.key, w.quoteNames);
  const day = new Intl.DateTimeFormat(lang, { month: "numeric", day: "numeric", timeZone: "UTC" }).format(new Date(quote.as_of));
  // one line, after the WSJ's: name, code, value and change at 12px. The day and source are in
  // the title.
  const body = (
    <>
      <span className="font-semibold">{name}</span>
      {code ? <span className="text-muted">{code}</span> : null}
      <span className={`font-medium tabular-nums ${TONE[way]}`}>{formatValue(quote, lang)}</span>
      {change ? (
        <span className={`tabular-nums ${TONE[way]}`}>
          <span className="sr-only">{way === "rise" ? "+" : way === "fall" ? "−" : ""}</span>
          {change}
          <span aria-hidden className="ml-0.5">
            {ARROW[way]}
          </span>
        </span>
      ) : null}
    </>
  );
  const page = stockPage(quote.key, lang);
  return (
    <li
      aria-hidden={copy || undefined}
      className="flex h-9 shrink-0 items-center pr-3 text-xs whitespace-nowrap"
      title={`${w.basis[quote.basis]} ${day}・${quote.source}`}
    >
      {/* a stock opens its page (D-049); an index, a rate or a coin has none */}
      {page ? (
        <Link
          href={page}
          tabIndex={copy ? -1 : undefined}
          className="flex items-center gap-1.5 rounded px-1 py-1 hover:bg-canvas"
        >
          {body}
        </Link>
      ) : (
        <span className="flex items-center gap-1.5 px-1">{body}</span>
      )}
    </li>
  );
}

export function MarketStrip({ quotes, lang }: { quotes: PublicQuote[]; lang: Lang }) {
  const w = words(lang);
  const list = useRef<HTMLUListElement>(null);
  // it drifts slowly and loops: the figures are drawn a second time, for the eye only
  const loops = useDrift(list, quotes.length);
  if (quotes.length === 0) return null;
  const scroll = (by: number) => {
    list.current?.dispatchEvent(new Event("drift:rest"));
    list.current?.scrollBy({ left: by, behavior: "smooth" });
  };
  const arrow = "hidden size-7 shrink-0 place-items-center rounded-md text-muted hover:bg-canvas hover:text-ink sm:grid";
  return (
    <section aria-label={w.markets} className="border-b border-line print:hidden" data-testid="market-strip">
      {/* the whole width of the window, not the page's column: a wider window shows more */}
      <div className="flex items-center px-2">
        <button type="button" aria-label={w.scrollLeft} onClick={() => scroll(-240)} className={arrow}>
          ‹
        </button>
        {/* relative: the screen-reader signs inside are absolutely positioned, and without a
            positioned row they escape its clipping and widen the whole page */}
        <ul ref={list} className="relative flex min-w-0 flex-1 gap-3 overflow-x-auto px-2 [scrollbar-width:none]">
          {quotes.map((quote) => (
            <Quote key={quote.key} quote={quote} lang={lang} />
          ))}
          {loops
            ? quotes.map((quote) => <Quote key={`${quote.key}:again`} quote={quote} lang={lang} copy />)
            : null}
        </ul>
        <button type="button" aria-label={w.scrollRight} onClick={() => scroll(240)} className={arrow}>
          ›
        </button>
      </div>
    </section>
  );
}
