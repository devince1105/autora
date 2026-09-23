// What a year costs (D-025). One number in one place until the purchase page reads it from the
// API (T-702): a price in a sentence that disagrees with the price in the database is worse
// than no price at all.
import type { Lang } from "./i18n";

export const MEMBERSHIP_PRICE_TWD = 360;
export const MEMBERSHIP_CURRENCY = "TWD";

export function formatMembershipPrice(lang: Lang): string {
  return new Intl.NumberFormat(lang, {
    style: "currency",
    currency: MEMBERSHIP_CURRENCY,
    maximumFractionDigits: 0,
  }).format(MEMBERSHIP_PRICE_TWD);
}
