// Two things a reader can do with an article besides read it (D-047): hear it, and print it.
"use client";

import { useEffect, useState } from "react";

import { words, type Lang } from "./i18n";

const BUTTON =
  "inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-xs text-muted hover:border-accent hover:text-accent";

// Voices worth hearing first: the neural ones (Edge's "… Online (Natural)", macOS's enhanced and
// premium downloads, Chrome's Google voices), then the ones each system reads news with.
const BETTER = /natural|neural|premium|enhanced|online|google/i;
const GOOD = /meijia|mei-jia|hsiaochen|hsiaoyu|yating|hanhan|zhiwei|samantha|ava|allison|aria|jenny|guy|daniel|karen/i;
// Apple's character and novelty voices (Eddy, Flo, Grandma…, Zarvox…) come first in the list on
// a Mac and are the hardest to follow: never picked while anything else speaks the language.
const NOVELTY =
  /^(eddy|flo|grandma|grandpa|reed|rocko|sandy|shelley|albert|bad news|bahh|bells|boing|bubbles|cellos|fred|good news|jester|junior|kathy|organ|ralph|superstar|trinoids|whisper|wobble|zarvox)\b/i;

function speaks(voice: Pick<SpeechSynthesisVoice, "lang">, lang: Lang): boolean {
  const tag = voice.lang.replace("_", "-").toLowerCase();
  return lang === "en" ? tag.startsWith("en") : tag === "zh-tw" || tag === "cmn-hant-tw";
}

/** The clearest voice there is for ``lang``, or null to leave it to the browser. */
export function pickVoice<V extends Pick<SpeechSynthesisVoice, "name" | "lang">>(voices: readonly V[], lang: Lang): V | null {
  const score = (v: V) => (BETTER.test(v.name) ? 3 : 0) + (GOOD.test(v.name) ? 2 : 0) - (NOVELTY.test(v.name) ? 10 : 0);
  const candidates = voices.filter((v) => speaks(v, lang));
  // stable: among equals, the system's own order
  return candidates.reduce<V | null>((best, v) => (best === null || score(v) > score(best) ? v : best), null);
}

/** The browser's voices. Chrome fills the list a moment after the page loads: ask again then. */
function useVoices(): SpeechSynthesisVoice[] {
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  useEffect(() => {
    if (!("speechSynthesis" in window)) return;
    const synth = window.speechSynthesis;
    const load = () => setVoices(synth.getVoices());
    load();
    synth.addEventListener?.("voiceschanged", load);
    return () => synth.removeEventListener?.("voiceschanged", load);
  }, []);
  return voices;
}

/**
 * Read aloud with the browser's own voices. One utterance per paragraph: a single long one is cut
 * off by some browsers after a few seconds. Not shown where the browser cannot speak.
 */
export function ListenButton({ lang, texts }: { lang: Lang; texts: string[] }) {
  const w = words(lang);
  const [can, setCan] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const voices = useVoices();
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
    const voice = pickVoice(voices.length ? voices : synth.getVoices(), lang);
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
