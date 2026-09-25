// US stocks under the market strip (D-048), from TradingView's free quotes widget. No free source
// may be shown publicly for single US stocks; TradingView holds the exchanges' licences and lets
// any site embed this, as long as its credit stays. It is loaded in the reader's browser (the
// privacy policy says so) and drawn again when the reader switches light or dark, since it cannot
// change colours by itself.
//
// Not the scrolling ticker tape: its speed cannot be set and the reader found it too fast. These
// stand still at the end of the market strip's own row and scroll with it by hand. The widget
// draws 72px-tall cards and spreads them evenly over its width; it is given room for each and
// scaled down to the height of the strip's cards, so the two read as one row.
"use client";

import { useEffect, useRef, useState } from "react";

import type { Lang } from "./i18n";
import { currentTheme, type Theme } from "./theme";

export const QUOTES_SCRIPT = "https://s3.tradingview.com/external-embedding/embed-widget-tickers.js";

/** The widget's own size for one quote (px): room for a logo, a name, a price and a change. */
const PER_QUOTE = 168;
const HEIGHT = 72;
/** Drawn at this size, so its cards are as tall as the strip's (58px): large enough that its
 * names and prices read at the size of ours. */
export const SCALE = 0.8;

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

  const width = US_TICKERS.length * PER_QUOTE;
  return (
    <>
      {/* the room the scaled widget takes in the row; the widget itself keeps its own size
          (TradingView's script rewrites its container's style, so the size is set out here) */}
      <li
        aria-label={lang === "zh-TW" ? "美股報價" : "US stocks"}
        className="shrink-0 overflow-hidden"
        style={{ width: width * SCALE, height: HEIGHT * SCALE }}
      >
        <div style={{ width, height: HEIGHT, transform: `scale(${SCALE})`, transformOrigin: "left top" }}>
          {/* color-scheme light: TradingView's page declares none (so light), and a browser
              paints an opaque light backdrop behind a frame whose scheme differs from its
              element's — in dark mode that was a white band. Its colours are the widget's
              colorTheme either way. */}
          <div ref={box} className="tradingview-widget-container h-full [color-scheme:light]" data-testid="us-stocks" />
        </div>
      </li>
      <li className="flex shrink-0 items-center">
        <a
          href="https://www.tradingview.com/"
          rel="noopener nofollow"
          target="_blank"
          className="text-[0.65rem] leading-tight text-muted hover:underline"
        >
          {lang === "zh-TW" ? (
            <>
              美股報價
              <br />
              TradingView
            </>
          ) : (
            <>
              US quotes by
              <br />
              TradingView
            </>
          )}
        </a>
      </li>
    </>
  );
}
