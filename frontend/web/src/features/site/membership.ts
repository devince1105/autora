// What a month and a year cost when the API has not said yet (D-025, D-034).
//
// The paywall and the pricing page ask ``/api/checkout/offer`` and show what the database
// actually charges (T-702). These numbers are only the fallback for the moment before that
// answer arrives, and for a site whose API is unreachable — a price in a sentence that disagrees
// with the price in the database is worse than no price at all, so nothing else should reach
// for them. Keep them equal to what ``scripts/seed_membership.py`` puts on sale.
import type { Interval } from "./checkout";

export const MEMBERSHIP_PRICES_TWD: Record<Interval, number> = { month: 30, year: 330 };
export const MEMBERSHIP_CURRENCY = "TWD";

/** Whether membership is on sale at all (D-035). Off until the operator turns it on: the site
 * starts free, and a pricing page for something nobody can buy would only confuse. Read on the
 * server at build time, like the operator's details. */
export function membershipOpen(env: Record<string, string | undefined> = process.env): boolean {
  return env.SITE_MEMBERSHIP_OPEN?.trim().toLowerCase() === "true";
}
