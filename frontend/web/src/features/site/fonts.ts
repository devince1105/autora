// The public site's type (D-047): Noto Sans TC, for headlines and reading alike; Noto Serif TC for
// the masthead's 矽鯨 alone (the browser fetches only the slices of it those two characters are in). Loaded by
// next/font, which serves the files from this site (no request goes to Google from a reader's
// browser) and only the slices of the Chinese character set a page uses.
import { Noto_Sans_TC, Noto_Serif_TC } from "next/font/google";

export const sans = Noto_Sans_TC({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-site-sans",
});

export const serif = Noto_Serif_TC({
  subsets: ["latin"],
  weight: "900",
  display: "swap",
  preload: false,
  variable: "--font-site-serif",
});
