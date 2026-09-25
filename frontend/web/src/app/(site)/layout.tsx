// Everything a reader sees (D-047): the site's type and its light or dark. Above the language's
// layout on purpose — switching language re-renders that one in the browser, where an inline
// script would not run; this one stays, and so does the reader's pick.
import type { ReactNode } from "react";

import { sans, serif } from "@/features/site/fonts";
import { THEME_SCRIPT } from "@/features/site/theme";

export default function SiteRoot({ children }: { children: ReactNode }) {
  return (
    // data-theme is written by the script below before React loads: not a mismatch to report
    <div data-site suppressHydrationWarning className={`${sans.variable} ${serif.variable} bg-surface font-reading text-ink`}>
      <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      {children}
    </div>
  );
}
