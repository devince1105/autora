// The other end of the link (D-025): swap the token for a session, then go on reading.
//
// A one-time token and an effect that may run twice (React's development double-invoke, a
// refresh, a browser prefetch) do not get along: the second call finds the link used and would
// say "this no longer works" to somebody who is, in fact, signed in. So the exchange happens
// once per token, and a failure asks who is signed in before believing it.
"use client";

import { useRouter } from "next/navigation";
import { use, useEffect, useRef, useState } from "react";

import { fetchMe, verify } from "@/features/site/auth";
import { isLang, words, type Lang } from "@/features/site/i18n";

export default function VerifyPage({
  params,
  searchParams,
}: {
  params: Promise<{ lang: string }>;
  searchParams: Promise<{ token?: string; next?: string }>;
}) {
  const { lang } = use(params);
  const { token, next } = use(searchParams);
  const language: Lang = isLang(lang) ? lang : "zh-TW";
  const w = words(language);
  const router = useRouter();
  const [failed, setFailed] = useState(false);
  const tried = useRef<string | null>(null);

  useEffect(() => {
    if (!token) {
      setFailed(true);
      return;
    }
    if (tried.current === token) return;
    tried.current = token;
    const onwards = next?.startsWith("/") ? next : `/news/${language}`;
    verify(token)
      .then(() => router.replace(onwards))
      .catch(async () => {
        const me = await fetchMe().catch(() => null);
        if (me) router.replace(onwards);
        else setFailed(true);
      });
  }, [token, next, language, router]);

  return (
    <section className="mx-auto max-w-md px-4 py-12">
      <p data-testid="verify-state">{failed ? w.verifyFailed : w.verifying}</p>
    </section>
  );
}
