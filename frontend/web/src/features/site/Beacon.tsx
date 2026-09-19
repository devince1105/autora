"use client";

// Counts the reader (a view on arrival, a completed read at the end of the article) without
// knowing who they are: see session.ts.
import { useEffect, useRef } from "react";

import { sendBeacon, sessionHash, type BeaconKind, type SessionStore } from "./session";

function store(): SessionStore | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function Beacon({ articleId, lang }: { articleId: string; lang: string }) {
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const send = (kind: BeaconKind) =>
      sendBeacon({
        article_id: articleId,
        lang,
        event_type: kind,
        session_hash: sessionHash(new Date(), store()),
      });
    send("view");
    const target = end.current;
    if (!target || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        send("read_complete");
        observer.disconnect();
      }
    });
    observer.observe(target);
    return () => observer.disconnect();
  }, [articleId, lang]);

  return <div ref={end} data-testid="read-end" aria-hidden="true" />;
}
