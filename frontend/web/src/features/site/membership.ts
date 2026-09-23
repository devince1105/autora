// What a year costs (D-025). One number in one place until the purchase page reads it from the
// API (T-702): a price in a sentence that disagrees with the price in the database is worse
// than no price at all.
import type { Lang } from "./i18n";

export const MEMBERSHIP_PRICE_TWD = 360;
export const MEMBERSHIP_CURRENCY = "TWD";

/** ``NT$360`` — written out rather than left to the locale, which renders TWD as a bare "$" in
 * zh-TW. A reader deciding whether to pay should not have to wonder which dollar it is. */
export function formatMembershipPrice(lang: Lang): string {
  const amount = new Intl.NumberFormat(lang, { maximumFractionDigits: 0 }).format(MEMBERSHIP_PRICE_TWD);
  return `NT$${amount}`;
}
