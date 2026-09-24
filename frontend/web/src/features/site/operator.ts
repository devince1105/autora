// Who runs the site and how to reach them: the footer, the policies and the pricing page (D-034).
//
// Read on the server from the environment, not written here: the operator is a person (PAYUNi's
// individual membership, D-024), and a person's name and phone number do not belong in a public
// repository. A field left empty is left off the page rather than shown as a blank.
export interface Operator {
  /** The brand the site is run under; also what the policies call "we". */
  brand: string;
  /** The person responsible, as registered with PAYUNi. */
  owner: string | null;
  email: string;
  phone: string | null;
}

function field(value: string | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

export function operator(env: Record<string, string | undefined> = process.env): Operator {
  return {
    brand: field(env.SITE_OPERATOR) ?? "Nanguado",
    owner: field(env.SITE_OPERATOR_OWNER),
    email: field(env.SITE_CONTACT_EMAIL) ?? "service@nanguado.com",
    phone: field(env.SITE_CONTACT_PHONE),
  };
}
