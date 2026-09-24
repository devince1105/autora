// What membership costs and what it gets you (D-034). The prices come from the API, through
// the same plan cards the paywall shows, so this page and the payment page never disagree.
import { notFound } from "next/navigation";

import { SITE_COMPANY } from "@/config";
import { isLang, words } from "@/features/site/i18n";
import { membershipOpen } from "@/features/site/membership";
import { PlanPicker } from "@/features/site/PlanPicker";

export async function generateMetadata({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  return isLang(lang) ? { title: `${words(lang).pricing} · ${words(lang).site}` } : {};
}

export default async function Page({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!isLang(lang)) notFound();
  const w = words(lang);
  const here = `/news/${lang}/pricing`;
  if (!membershipOpen()) {
    return (
      <article className="mx-auto max-w-2xl px-4 py-8">
        <h1 className="text-2xl font-bold">{w.pricing}</h1>
        <p data-testid="membership-closed" className="mt-2 leading-relaxed">
          {w.pricingClosed}
        </p>
      </article>
    );
  }
  return (
    <article className="mx-auto max-w-2xl px-4 py-8">
      <h1 className="text-2xl font-bold">{w.pricing}</h1>
      <p className="mt-2 leading-relaxed">{w.pricingIntro}</p>
      <ul className="mt-4 list-disc space-y-1 pl-6">
        {w.pricingIncludes.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <div className="mt-6">
        <PlanPicker lang={lang} loginHref={`/news/${lang}/login?next=${encodeURIComponent(here)}`} company={SITE_COMPANY} />
      </div>
      <p className="mt-4 text-sm text-muted">{w.pricingSignIn}</p>
      <p className="mt-1 text-sm text-muted">{w.pricingPayment}</p>
    </article>
  );
}
