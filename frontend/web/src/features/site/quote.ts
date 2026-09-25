// How a market figure is written (D-048, D-049): shared by the strip, which runs in the browser,
// and the stock pages, which are rendered on the server — so it lives in neither.
import type { PublicQuote } from "./api";
import type { Lang } from "./i18n";

const DECIMALS: Record<string, number> = { btc: 0 };

/** A stock's code as readers look it up: ``tw:2330`` is ``2330.TW``, ``us:NVDA`` is ``NVDA``. */
export function stockCode(key: string): string | null {
  if (key.startsWith("tw:")) return `${key.slice(3)}.TW`;
  if (key.startsWith("us:")) return key.slice(3);
  return null;
}

/** A stock's page on the site (D-049): ``us:NVDA`` → ``/news/en/stocks/NVDA``; none for the rest. */
export function stockPage(key: string, lang: Lang): string | null {
  const [market, symbol] = key.split(":");
  return (market === "tw" || market === "us") && symbol ? `/news/${lang}/stocks/${encodeURIComponent(symbol)}` : null;
}

/** Its name in the reader's language, and its code when that is not the name already. */
export function label(key: string, names: Record<string, string>): [name: string, code: string | null] {
  const code = stockCode(key);
  const name = names[key] ?? code ?? key;
  return [name, code === name ? null : code];
}

const MONEY: Record<string, string> = { TWD: "NT$", USD: "US$" };

/** A market value, short: ``NT$64.2兆``, ``US$5.4T``. */
export function formatCap(value: number, currency: string | null | undefined, lang: Lang): string {
  const short = new Intl.NumberFormat(lang, { notation: "compact", maximumFractionDigits: 1 }).format(value);
  return `${MONEY[currency ?? ""] ?? ""}${short}`;
}

/** A price of the stock's, with its decimals (``formatValue``'s rule). */
export function formatPrice(quote: PublicQuote, price: number, lang: Lang): string {
  return formatValue({ ...quote, value: price }, lang);
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

export function direction(quote: PublicQuote): "rise" | "fall" | "flat" {
  const shown = quote.change_pct ?? quote.change ?? 0;
  return shown > 0 ? "rise" : shown < 0 ? "fall" : "flat";
}

export const ARROW = { rise: "↑", fall: "↓", flat: "" } as const;
export const TONE = { rise: "text-rise", fall: "text-fall", flat: "text-muted" } as const;
