// What a year costs when the API has not said yet (D-025).
//
// The paywall asks ``/api/checkout/offer`` and shows what the database actually charges (T-702).
// This number is only the fallback for the moment before that answer arrives, and for a site
// whose API is unreachable — a price in a sentence that disagrees with the price in the database
// is worse than no price at all, so nothing else should reach for it.
export const MEMBERSHIP_PRICE_TWD = 360;
export const MEMBERSHIP_CURRENCY = "TWD";
