import { notFound } from "next/navigation";

import { isLang, words } from "@/features/site/i18n";
import { operator } from "@/features/site/operator";
import { PaymentDone } from "@/features/site/PaymentDone";

export async function generateMetadata({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  return isLang(lang) ? { title: `${words(lang).doneTitle} · ${words(lang).site}` } : {};
}

export default async function Page({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!isLang(lang)) notFound();
  return <PaymentDone lang={lang} email={operator().email} />;
}
