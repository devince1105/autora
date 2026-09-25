// US stocks under the market strip (D-048), from TradingView's free quotes widget. No free source
// may be shown publicly for single US stocks; TradingView holds the exchanges' licences and lets
// any site embed this, as long as its credit stays. It is loaded in the reader's browser (the
// privacy policy says so) and drawn again when the reader switches light or dark, since it cannot
// change colours by itself.
//
// TradingView's ticker tape, beside the market strip's cards in the same block (under them on a
// phone). It moves by itself and its
// speed cannot be set, but it is the one widget whose text is all one size (name, price and change
// on one line, like the strip's cards); the still "tickers" widget draws the change very large
// and the name very small, and its fonts cannot be changed. It stops under the pointer.
"use client";

import { useEffect, useRef, useState } from "react";

import type { Lang } from "./i18n";
import { currentTheme, type Theme } from "./theme";

export const QUOTES_SCRIPT = "https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js";


/** What the tape shows, AI first: the chips (GPUs, custom chips, foundry, memory, tools), then
 * the cloud and model companies, then the rest of the big names, then the Nasdaq 100 as its ETF. */
export const US_TICKERS: readonly [symbol: string, zh: string][] = [
  ["NASDAQ:NVDA", "輝達"],
  ["NASDAQ:AVGO", "博通"],
  ["NYSE:TSM", "台積電 ADR"],
  ["NASDAQ:AMD", "超微"],
  ["NASDAQ:MU", "美光"],
  ["NASDAQ:ASML", "艾司摩爾"],
  ["NASDAQ:ARM", "安謀"],
  ["NASDAQ:MSFT", "微軟"],
  ["NASDAQ:GOOGL", "Alphabet"],
  ["NASDAQ:AMZN", "亞馬遜"],
  ["NASDAQ:META", "Meta"],
  ["NYSE:ORCL", "甲骨文"],
  ["NASDAQ:PLTR", "Palantir"],
  ["NASDAQ:AAPL", "蘋果"],
  ["NASDAQ:TSLA", "特斯拉"],
  ["NASDAQ:QQQ", "那斯達克100 ETF"],
];

export function quotesConfig(lang: Lang, theme: Theme) {
  return {
    symbols: US_TICKERS.map(([proName, zh]) => ({
      proName,
      title: lang === "zh-TW" ? zh : proName.split(":")[1],
    })),
    showSymbolLogo: true,
    isTransparent: true,
    displayMode: "regular",
    colorTheme: theme,
    locale: lang === "zh-TW" ? "zh_TW" : "en",
  };
}

function siteTheme(): Theme {
  return currentTheme(
    document.querySelector<HTMLElement>("[data-site]"),
    window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false,
  );
}

export function UsStocks({ lang }: { lang: Lang }) {
  const box = useRef<HTMLDivElement>(null);
  const [theme, setTheme] = useState<Theme | null>(null);

  // follow the site's light or dark: the toggle writes data-theme, the system can change too
  useEffect(() => {
    setTheme(siteTheme());
    const site = document.querySelector("[data-site]");
    const watch = new MutationObserver(() => setTheme(siteTheme()));
    if (site) watch.observe(site, { attributes: true, attributeFilter: ["data-theme"] });
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onMedia = () => setTheme(siteTheme());
    media?.addEventListener?.("change", onMedia);
    return () => {
      watch.disconnect();
      media?.removeEventListener?.("change", onMedia);
    };
  }, []);

  useEffect(() => {
    const target = box.current;
    if (!target || !theme) return;
    target.replaceChildren();
    const widget = document.createElement("div");
    widget.className = "tradingview-widget-container__widget";
    const script = document.createElement("script");
    script.src = QUOTES_SCRIPT;
    script.async = true;
    script.type = "text/javascript";
    script.text = JSON.stringify(quotesConfig(lang, theme));
    target.append(widget, script);
    return () => target.replaceChildren();
  }, [lang, theme]);

  return (
    <div aria-label={lang === "zh-TW" ? "美股報價" : "US stocks"} className="flex min-w-0 items-center gap-2">
      {/* color-scheme light: TradingView's page declares none (so light), and a browser paints an
          opaque light backdrop behind a frame whose scheme differs from its element's — in dark
          mode that was a white band. Its colours are the widget's colorTheme either way. */}
      <div
        ref={box}
        className="tradingview-widget-container min-w-0 flex-1 [color-scheme:light]"
        data-testid="us-stocks"
      />
      <a
        href="https://www.tradingview.com/"
        rel="noopener nofollow"
        target="_blank"
        className="shrink-0 text-[0.6rem] leading-tight text-muted hover:underline"
      >
        {lang === "zh-TW" ? "美股報價" : "US quotes by"}
        <br />
        TradingView
      </a>
    </div>
  );
}
