// A stock's page (D-049): today's figure, which of the investors we follow hold it and what they
// did with it this quarter (their 13F filings, as the newsroom reads them), and our stories that
// name it. Server-rendered; nothing on it is advice, and it says so.
import Link from "next/link";

import type { PublicHolder, PublicStock } from "./api";
import { formatDate, words, type Lang } from "./i18n";
import { ARROW, direction, formatChange, formatValue, stockCode, TONE } from "./quote";

function usd(lang: Lang, value: number): string {
  return `US$${new Intl.NumberFormat(lang, { notation: "compact", maximumFractionDigits: 1 }).format(value)}`;
}

function count(lang: Lang, value: number): string {
  return new Intl.NumberFormat(lang).format(value);
}

const CHANGE_TONE: Record<string, string> = {
  new: "text-rise border-rise/40",
  increased: "text-rise border-rise/40",
  decreased: "text-fall border-fall/40",
  sold_out: "text-fall border-fall/40",
  unchanged: "text-muted border-line",
};

function Holder({ holder, lang }: { holder: PublicHolder; lang: Lang }) {
  const w = words(lang).stock;
  // an option is a bet, not a holding: its badge is not coloured as buying or selling the stock,
  // it says which way the bet goes, and its count is of the shares underneath
  const option = holder.put_call ? (w.options[holder.put_call] ?? holder.put_call) : null;
  const tone = option ? CHANGE_TONE.unchanged : (CHANGE_TONE[holder.change] ?? CHANGE_TONE.unchanged);
  return (
    <li className="grid gap-2 py-4 sm:grid-cols-[1fr_auto] sm:items-center" data-testid="holder">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2">
          <span className="font-semibold">{holder.investor}</span>
          <span className={`rounded-full border px-2 py-px text-xs ${tone}`}>
            {w.changes[holder.change] ?? holder.change}
            {option ? `・${option}` : null}
          </span>
          <span className="truncate text-xs text-muted">{holder.title_of_class}</span>
        </p>
        <p className="mt-1 text-xs text-muted">{holder.filer}</p>
      </div>
      <dl className="grid grid-cols-3 gap-3 text-sm tabular-nums sm:w-96 sm:text-right">
        <div>
          <dt className="text-xs text-muted">{option ? w.underlying : w.shares}</dt>
          <dd>{count(lang, holder.shares)}</dd>
          {holder.change !== "unchanged" && holder.previous_shares ? (
            <dd className="text-xs text-muted">{w.was(count(lang, holder.previous_shares))}</dd>
          ) : null}
        </div>
        <div>
          <dt className="text-xs text-muted">{w.value}</dt>
          <dd>{holder.value_usd ? usd(lang, holder.value_usd) : "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">{w.weight}</dt>
          <dd>{holder.portfolio_pct !== null && holder.portfolio_pct !== undefined ? `${holder.portfolio_pct}%` : "—"}</dd>
          <dd className="text-xs">
            <a href={holder.filing_url} rel="noopener nofollow" className="text-accent hover:underline">
              {w.filing} ↗
            </a>
          </dd>
        </div>
      </dl>
    </li>
  );
}

export function StockView({ stock, lang }: { stock: PublicStock; lang: Lang }) {
  const w = words(lang);
  const s = w.stock;
  const quote = stock.quote;
  const way = quote ? direction(quote) : "flat";
  const change = quote ? formatChange(quote) : null;
  const code = stockCode(`${stock.market}:${stock.symbol}`);
  const period = stock.holders[0]?.period;
  const before = stock.holders[0]?.previous_period ?? null;
  return (
    <article className="mx-auto max-w-3xl px-4 pt-6 pb-10">
      <header className="border-b border-line pb-6">
        <h1 className="flex flex-wrap items-baseline gap-x-3 text-3xl font-bold">
          {stock.name}
          {code && code !== stock.name ? <span className="text-lg font-medium text-muted">{code}</span> : null}
        </h1>
        {quote ? (
          <p className="mt-3 flex flex-wrap items-baseline gap-x-3">
            <span className={`text-4xl font-semibold tabular-nums ${TONE[way]}`}>{formatValue(quote, lang)}</span>
            {change ? (
              <span className={`text-lg tabular-nums ${TONE[way]}`}>
                {way === "rise" ? "+" : way === "fall" ? "−" : ""}
                {change} {ARROW[way]}
              </span>
            ) : null}
            <span className="text-xs text-muted">
              {w.basis[quote.basis]} {formatDate(lang, quote.as_of)}・{w.sourceNames[quote.source] ?? quote.source}
            </span>
          </p>
        ) : (
          <p className="mt-3 text-muted">{s.noQuote}</p>
        )}
      </header>

      <section className="mt-8" aria-labelledby="holders">
        <h2 id="holders" className="text-xl font-bold">
          {s.holders}
        </h2>
        {stock.holders.length ? (
          <>
            <p className="mt-2 text-xs leading-relaxed text-muted">
              {stock.market === "tw" ? `${s.holdersUs} ` : ""}
              {period ? s.holdersNote(formatDate(lang, period), before ? formatDate(lang, before) : null) : null}
            </p>
            <ul className="mt-2 divide-y divide-line">
              {stock.holders.map((holder, i) => (
                <Holder key={`${holder.investor}-${holder.title_of_class}-${holder.put_call}-${i}`} holder={holder} lang={lang} />
              ))}
            </ul>
          </>
        ) : (
          <p className="mt-3 text-muted">{stock.market === "tw" ? s.twNo13f : s.holdersNone}</p>
        )}
      </section>

      <section className="mt-10" aria-labelledby="coverage">
        <h2 id="coverage" className="text-xl font-bold">
          {s.coverage}
        </h2>
        {stock.articles.length ? (
          <ul className="mt-2 divide-y divide-line">
            {stock.articles.map((article) => (
              <li key={article.article_id} className="py-4">
                <Link href={article.path} className="font-semibold hover:text-accent">
                  {article.title}
                </Link>
                <p className="mt-1 text-xs text-muted">
                  <time dateTime={article.published_at}>{formatDate(lang, article.published_at)}</time>
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-muted">{s.coverageNone}</p>
        )}
      </section>

      <p className="mt-10 rounded-lg bg-canvas p-4 text-xs leading-relaxed text-muted">{s.notice}</p>
      <p className="mt-6 text-sm">
        <Link href={`/news/${lang}`} className="text-accent hover:underline">
          {s.back}
        </Link>
      </p>
    </article>
  );
}
