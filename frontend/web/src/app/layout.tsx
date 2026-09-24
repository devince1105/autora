import type { ReactNode } from "react";

import "./globals.css";
import { Providers } from "./providers";

export const metadata = {
  title: "Autora",
  description: "AI Autonomous Company",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // Browser extensions write attributes onto <html> before React loads (Immersive Translate
    // adds data-immersive-translate-page-theme), which React reports as a hydration mismatch.
    // This ignores attribute differences on this one element only, not on anything inside it.
    <html lang="zh-TW" suppressHydrationWarning>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
