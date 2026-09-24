// Buying a month or a year, from the browser (T-702, D-024, D-034).
//
// Paying happens on PAYUNi's own page, not here: this file asks our API to open an order and
// hands back the sealed form that opens that page, then posts it. Nothing secret passes through
// the browser — the form is an encrypted envelope our server made — and nothing is granted by
// anything that happens here. The year arrives when PAYUNi tells our server, server to server,
// which may well be before the reader's browser finds its way back.
import { API_URL } from "@/config";

/** What one payment buys. Both are sold side by side (D-034); neither renews by itself. */
export type Interval = "month" | "year";

export interface Offer {
  amount: string;
  currency: string;
  interval: string;
  available: boolean;
}

export interface CheckoutPage {
  order_id: string;
  mer_trade_no: string;
  amount: string;
  currency: string;
  url: string;
  fields: Record<string, string>;
}

/** Why a checkout did not start. The caller says something different for each. */
export type CheckoutProblem = "signed-out" | "unavailable" | "failed";

export class CheckoutError extends Error {
  constructor(readonly problem: CheckoutProblem) {
    super(problem);
  }
}

/** What a month or a year costs here, or null when that one is not for sale. */
export async function fetchOffer(company?: string, interval: Interval = "year"): Promise<Offer | null> {
  const query = new URLSearchParams({ interval });
  if (company) query.set("company", company);
  try {
    const response = await fetch(`${API_URL}/api/checkout/offer?${query}`, {
      credentials: "include",
    });
    if (!response.ok) return null;
    const offer = (await response.json()) as Offer;
    return offer.available ? offer : null;
  } catch {
    return null; // a price we could not fetch is not an error the reader can do anything about
  }
}

/** Open an order. Throws ``CheckoutError`` rather than returning a half-answer. */
export async function startCheckout(
  lang: string,
  company?: string,
  interval: Interval = "year",
): Promise<CheckoutPage> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/checkout`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lang, company: company ?? null, interval }),
    });
  } catch {
    throw new CheckoutError("failed");
  }
  if (response.status === 401) throw new CheckoutError("signed-out");
  if (response.status === 404 || response.status === 503) {
    throw new CheckoutError("unavailable");
  }
  if (!response.ok) throw new CheckoutError("failed");
  return (await response.json()) as CheckoutPage;
}

/** Post the sealed form to PAYUNi, which takes the reader off this site.
 *
 * A real form submission rather than fetch: the reader has to *land* on the payment page, and a
 * cross-site POST read by script is not what that means. */
export function goToPaymentPage(page: CheckoutPage, doc: Document = document): void {
  const form = doc.createElement("form");
  form.method = "POST";
  form.action = page.url;
  form.style.display = "none";
  for (const [name, value] of Object.entries(page.fields)) {
    const input = doc.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }
  doc.body.appendChild(form);
  form.submit();
}

/** ``NT$360`` from what the API said. Written out rather than left to the locale, which renders
 * TWD as a bare "$" in zh-TW — a reader deciding whether to pay should not have to wonder which
 * dollar it is. */
export function formatOffer(offer: Offer, lang: string): string {
  const amount = new Intl.NumberFormat(lang, { maximumFractionDigits: 0 }).format(
    Number(offer.amount),
  );
  return offer.currency === "TWD" ? `NT$${amount}` : `${offer.currency} ${amount}`;
}
