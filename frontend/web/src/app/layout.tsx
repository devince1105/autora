import type { ReactNode } from "react";

import "./globals.css";
import { Providers } from "./providers";

export const metadata = {
  title: "Autora",
  description: "AI Autonomous Company",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-TW">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
