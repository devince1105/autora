// The strip of market figures under the site's header (D-048), after Bloomberg's: name, figure,
// change, red for up and green for down as Taiwanese readers expect. Taiwan, US indices and
// stocks, rates, oil and crypto, all from the API in one form. It drifts slowly to the left and
// loops (drift.ts), stopping under the pointer; it also scrolls by hand (arrows on a wide screen,
// a swipe on a phone). Every figure says what it is — a close, the previous close, the latest,
// 24 hours.
"use client";

import { useRef } from "react";

import type { PublicQuote } from "./api";
import { useDrift } from "./drift";
import { words, type Lang } from "./i18n";

const DECIMALS: Record<string, number> = { btc: 0 };

/** A stock's code as readers look it up: ``tw:2330`` is ``2330.TW``, ``us:NVDA`` is ``NVDA``. */
export function stockCode(key: string): string | null {
  if (key.startsWith("tw:")) return `${key.slice(3)}.TW`;
  if (key.startsWith("us:")) return key.slice(3);
  return null;
}

/** Its name in the reader's language, and its code when that is not the name already. */
function label(key: string, names: Record<string, string>): [name: string, code: string | null] {
  const code = stockCode(key);
  const name = names[key] ?? code ?? key;
  return [name, code === name ? null : code];
}

export function formatValue(quote: PublicQuote, lang: Lang): string {
  // a Taiwan stock is quoted to its tick: whole dollars above 1,000, else two places
  const digits = DECIMALS[quote.key] ?? (quote.key.startsWith("tw:") && quote.value >= 1000 ? 0 : 2);
  const value = new Intl.NumberFormat(lang, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(
    quote.value,
  );
  return quote.key === "us10y" ? `${value}%` : value;
}

/** The change as shown: a percentage, or for the yield its points (a change in a percentage). */
export function formatChange(quote: PublicQuote): string | null {
  const shown = quote.change_pct ?? quote.change;
  if (shown === null || shown === undefined) return null;
  const size = Math.abs(shown).toFixed(2);
  return quote.change_pct !== null && quote.change_pct !== undefined ? `${size}%` : size;
}

function direction(quote: PublicQuote): "rise" | "fall" | "flat" {
  const shown = quote.change_pct ?? quote.change ?? 0;
  return shown > 0 ? "rise" : shown < 0 ? "fall" : "flat";
}

const ARROW = { rise: "↑", fall: "↓", flat: "" } as const;
const TONE = { rise: "text-rise", fall: "text-fall", flat: "text-muted" } as const;

function Quote({ quote, lang, copy = false }: { quote: PublicQuote; lang: Lang; copy?: boolean }) {
  const w = words(lang);
  const way = direction(quote);
  const change = formatChange(quote);
  const [name, code] = label(quote.key, w.quoteNames);
  const day = new Intl.DateTimeFormat(lang, { month: "numeric", day: "numeric", timeZone: "UTC" }).format(new Date(quote.as_of));
  // one line, after the WSJ's: name, code, value and change at 12px. The day and source are in
  // the title.
  return (
    <li
      aria-hidden={copy || undefined}
      className="flex h-9 shrink-0 items-center gap-1.5 pr-3 text-xs whitespace-nowrap"
      title={`${w.basis[quote.basis]} ${day}・${quote.source}`}
    >
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
