// Two things a reader can do with an article besides read it (D-047): hear it, and print it.
"use client";

import { useEffect, useState } from "react";

import { words, type Lang } from "./i18n";

const BUTTON =
  "inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-xs text-muted hover:border-accent hover:text-accent";

/**
 * Read aloud with the browser's own voices. One utterance per paragraph: a single long one is cut
 * off by some browsers after a few seconds. Not shown where the browser cannot speak.
 */
export function ListenButton({ lang, texts }: { lang: Lang; texts: string[] }) {
  const w = words(lang);
  const [can, setCan] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  useEffect(() => {
    setCan(typeof window !== "undefined" && "speechSynthesis" in window);
    return () => {
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    };
  }, []);
  if (!can) return null;

  function start() {
    const synth = window.speechSynthesis;
    synth.cancel();
    const voice = synth.getVoices().find((v) => v.lang.replace("_", "-").startsWith(lang === "en" ? "en" : "zh-TW"));
    texts.forEach((text, i) => {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = lang;
      if (voice) utterance.voice = voice;
      if (i === texts.length - 1) utterance.onend = () => setSpeaking(false);
      utterance.onerror = () => setSpeaking(false);
      synth.speak(utterance);
    });
    setSpeaking(true);
  }

  return (
    <button
      type="button"
      aria-pressed={speaking}
      onClick={() => {
        if (speaking) {
          window.speechSynthesis.cancel();
          setSpeaking(false);
        } else start();
      }}
      className={BUTTON}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        {speaking ? (
          <rect x="6" y="6" width="12" height="12" rx="1" />
        ) : (
          <path d="M11 5 6 9H2v6h4l5 4V5ZM15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14" />
        )}
      </svg>
      {speaking ? w.stopListening : w.listen}
    </button>
  );
}

export function PrintButton({ lang }: { lang: Lang }) {
  return (
    <button type="button" onClick={() => window.print()} className={BUTTON}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v8H6z" />
      </svg>
      {words(lang).print}
    </button>
  );
}
