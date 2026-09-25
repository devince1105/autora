// The strip of market figures under the site's header (D-048), after Bloomberg's: name, figure,
// change, red for up and green for down as Taiwanese readers expect. Ours first, then the US stocks
// from TradingView (UsStocks) in the same row, cards of the same height. It scrolls sideways by hand
// (arrows on a wide screen, a swipe on a phone) and never by itself: a moving line is hard to read
// and harder to tap. Every figure says what it is — a close, the previous close, 24 hours.
"use client";

import { useRef } from "react";

import type { PublicQuote } from "./api";
import { words, type Lang } from "./i18n";
import { UsStocks } from "./UsStocks";

const DECIMALS: Record<string, number> = { btc: 0 };

/** A Taiwan stock's exchange code as readers look it up: ``tw:2330`` is ``2330.TW``. */
export function twCode(key: string): string | null {
  return key.startsWith("tw:") ? `${key.slice(3)}.TW` : null;
}

export function formatValue(quote: PublicQuote, lang: Lang): string {
  // a Taiwan stock is quoted to its tick: whole dollars above 1,000, else two places
  const digits = DECIMALS[quote.key] ?? (twCode(quote.key) && quote.value >= 1000 ? 0 : 2);
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

const ARROW = { rise: "▲", fall: "▼", flat: "" } as const;
const TONE = { rise: "text-rise", fall: "text-fall", flat: "text-muted" } as const;

function Quote({ quote, lang }: { quote: PublicQuote; lang: Lang }) {
  const w = words(lang);
  const way = direction(quote);
  const change = formatChange(quote);
  const day = new Intl.DateTimeFormat(lang, { month: "numeric", day: "numeric", timeZone: "UTC" }).format(new Date(quote.as_of));
  // one line, as TradingView's tape draws its quotes beside it: the same height and size, so
  // the Taiwan figures and the US ones read as one strip. The day and source are in the title.
  return (
    <li
      className="flex h-11 shrink-0 items-center gap-1.5 border-r border-line pr-4 pl-1 text-sm whitespace-nowrap last:border-r-0"
      title={`${w.basis[quote.basis]} ${day}・${quote.source}`}
    >
      <span className="font-semibold">{w.quoteNames[quote.key] ?? quote.key}</span>
      {twCode(quote.key) ? <span className="text-xs text-muted">{twCode(quote.key)}</span> : null}
      <span className="tabular-nums">{formatValue(quote, lang)}</span>
      {change ? (
        <span className={`tabular-nums ${TONE[way]}`}>
          <span aria-hidden>{ARROW[way]}</span>
          <span className="sr-only">{way === "rise" ? "+" : way === "fall" ? "−" : ""}</span>
          {change}
        </span>
      ) : null}
    </li>
  );
}

export function MarketStrip({ quotes, lang }: { quotes: PublicQuote[]; lang: Lang }) {
  const w = words(lang);
  const list = useRef<HTMLUListElement>(null);
  // the US stocks are always there, so the strip is too: ours may be missing (a service down)
  const scroll = (by: number) => list.current?.scrollBy({ left: by, behavior: "smooth" });
  const arrow = "hidden size-7 shrink-0 place-items-center rounded-md text-muted hover:bg-canvas hover:text-ink sm:grid";
  return (
    <section aria-label={w.markets} className="border-b border-line print:hidden" data-testid="market-strip">
      {/* the whole width of the window, not the page's column: a wider window shows more. Our
          cards on the left, the US stocks beside them (under them on a phone), one block */}
      <div className="flex flex-col px-4 lg:flex-row lg:items-center lg:gap-4">
        <div className="flex min-w-0 items-center gap-2 lg:w-1/2">
          {/* relative: the screen-reader signs inside are absolutely positioned, and without a
              positioned row they escape its clipping and widen the whole page */}
          <ul ref={list} className="relative flex min-w-0 flex-1 gap-3 overflow-x-auto [scrollbar-width:none]">
            {quotes.map((quote) => (
              <Quote key={quote.key} quote={quote} lang={lang} />
            ))}
          </ul>
          <button type="button" aria-label={w.scrollLeft} onClick={() => scroll(-240)} className={arrow}>
            ‹
          </button>
          <button type="button" aria-label={w.scrollRight} onClick={() => scroll(240)} className={arrow}>
            ›
          </button>
        </div>
        <div className="min-w-0 lg:w-1/2">
          <UsStocks lang={lang} />
        </div>
      </div>
    </section>
  );
}
