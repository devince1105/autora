// One policy page: a title, the date it last changed, numbered sections (D-034).
import type { LegalDoc } from "./legal";
import { words, type Lang } from "./i18n";

export function LegalView({ doc, lang }: { doc: LegalDoc; lang: Lang }) {
  return (
    <article className="mx-auto max-w-2xl px-4 py-8">
      <h1 className="text-2xl font-bold">{doc.title}</h1>
      <p className="mt-1 text-sm text-muted">
        {words(lang).updated}
        {words(lang).sep}
        {doc.updated}
      </p>
      {doc.sections.map((section) => (
        <section key={section.heading} className="mt-6">
          <h2 className="text-lg font-semibold">{section.heading}</h2>
          {section.body.map((block, i) =>
            typeof block === "string" ? (
              <p key={i} className="mt-2 leading-relaxed">
                {block}
              </p>
            ) : (
              <ul key={i} className="mt-2 list-disc space-y-1 pl-6 leading-relaxed">
                {block.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ),
          )}
        </section>
      ))}
    </article>
  );
}
