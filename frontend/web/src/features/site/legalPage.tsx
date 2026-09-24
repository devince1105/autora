// The three policy routes are the same page with a different document (D-034).
import { notFound } from "next/navigation";

import { isLang } from "./i18n";
import { legalDoc, type LegalPage } from "./legal";
import { LegalView } from "./LegalView";
import { operator } from "./operator";

type Params = { params: Promise<{ lang: string }> };

export function legalPage(page: LegalPage) {
  async function generateMetadata({ params }: Params) {
    const { lang } = await params;
    return isLang(lang) ? { title: `${legalDoc(page, lang, operator()).title} · Autora` } : {};
  }

  async function Page({ params }: Params) {
    const { lang } = await params;
    if (!isLang(lang)) notFound();
    return <LegalView doc={legalDoc(page, lang, operator())} lang={lang} />;
  }

  return { generateMetadata, Page };
}
