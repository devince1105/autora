// Pieces the newsroom pages share (T-517): state badges, the section header and navigation, the
// claim list (every claim with its quotes, each shown in place in its evidence), the timeline.
import type { EventEnvelope } from "@autora/event-schema";
import Link from "next/link";
import type { ReactNode } from "react";

import { withCompany } from "@/features/company/CompanyScope";
import { describeEvent, TONE_DOT, type Tone } from "@/events/describe";

import { CLAIM_STATUS, CLAIM_TYPE, formatTime, label, SUPPORT, TONE_BADGE, type ClaimView } from "./model";

export function Badge({ text, tone }: { text: string; tone: Tone }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${TONE_BADGE[tone]}`}>{text}</span>;
}

const TABS = [
  ["stories", "題材", "/newsroom/stories"],
  ["articles", "文章", "/newsroom/articles"],
  ["sources", "來源", "/newsroom/sources"],
] as const;

export function NewsroomHeader({
  companyId,
  current,
  title,
  eyebrow = "Newsroom",
  children,
}: {
  companyId: string;
  current?: (typeof TABS)[number][0];
  title: ReactNode;
  eyebrow?: string;
  children?: ReactNode;
}) {
  return (
    <header className="mb-6">
      <nav className="mb-4 flex flex-wrap items-center gap-4 text-sm">
        {TABS.map(([key, text, path]) => (
          <Link
            key={key}
            href={withCompany(path, companyId)}
            aria-current={current === key ? "page" : undefined}
            className={current === key ? "font-semibold" : "text-accent underline"}
          >
            {text}
          </Link>
        ))}
        <span className="grow" />
        <Link href={withCompany("/dashboard", companyId)} className="text-accent underline">
          Dashboard
        </Link>
        <Link href={withCompany("/office", companyId)} className="text-accent underline">
          辦公室
        </Link>
      </nav>
      <p className="text-xs tracking-widest text-muted uppercase">{eyebrow}</p>
      <h1 className="mt-1 text-2xl font-semibold">{title}</h1>
      {children}
    </header>
  );
}

export function Section({ id, title, children }: { id?: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="mt-8 scroll-mt-4">
      <h2 className="mb-3 text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted">{children}</p>;
}

/** Claims with their quotes. ``numbers``: the article's citation numbers, when shown under one. */
export function ClaimList({ claims, numbers }: { claims: readonly ClaimView[]; numbers?: Map<string, number> }) {
  if (claims.length === 0) return <Empty>還沒有主張。</Empty>;
  return (
    <ol className="space-y-3">
      {claims.map((claim) => {
        const [status, tone] = label(CLAIM_STATUS, claim.status);
        const number = numbers?.get(claim.id);
        return (
          <li key={claim.id} id={`claim-${claim.id}`} className="scroll-mt-4 rounded border border-line bg-surface p-3">
            <details>
              <summary className="cursor-pointer list-none">
                <span className="mr-2 text-sm text-muted">{number ? `[${number}]` : "・"}</span>
                <span className="mr-2 text-xs text-muted">{CLAIM_TYPE[claim.claim_type] ?? claim.claim_type}</span>
                <Badge text={status} tone={tone} />
                <p className="mt-1">{claim.text}</p>
                <p className="mt-1 text-xs text-accent">{claim.quotes.length} 段引文（展開）</p>
              </summary>
              <ul className="mt-3 space-y-3">
                {claim.quotes.map((quote, index) => (
                  <li key={index} className="text-sm">
                    <p className="text-xs text-muted">
                      {SUPPORT[quote.support_type] ?? quote.support_type}・
                      <a href={quote.url} rel="noopener noreferrer" target="_blank" className="text-accent underline">
                        {quote.evidence_title ?? quote.url}
                      </a>
                    </p>
                    <blockquote className="mt-1 border-l-2 border-line pl-3 text-muted">
                      …{quote.before}
                      <mark className="bg-transparent font-semibold text-ink underline decoration-warn decoration-2">
                        {quote.quote}
                      </mark>
                      {quote.after}…
                    </blockquote>
                  </li>
                ))}
              </ul>
            </details>
          </li>
        );
      })}
    </ol>
  );
}

export function EventList({ events }: { events: readonly EventEnvelope[] }) {
  if (events.length === 0) return <Empty>還沒有事件。</Empty>;
  return (
    <ol className="space-y-1 text-sm">
      {events.map((event) => {
        const described = describeEvent(event.event_type, event.payload);
        return (
          <li key={event.event_id} className="flex gap-3">
            <time className="w-36 shrink-0 whitespace-nowrap text-muted tabular-nums" dateTime={event.occurred_at}>
              {formatTime(event.occurred_at)}
            </time>
            <span className={`mt-1.5 size-2 shrink-0 rounded-full ${TONE_DOT[described.tone]}`} aria-hidden="true" />
            <span className="font-medium">{described.label}</span>
            {described.summary ? <span className="truncate text-muted">{described.summary}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}
