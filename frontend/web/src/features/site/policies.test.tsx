// @vitest-environment jsdom
// D-034: the pages a payment provider reviews before it lets the site take money — who runs it,
// what is sold on what terms, and what happens after paying.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/(site)/news/[lang]/membership/return/route";

import { LANGS } from "./i18n";
import { LEGAL_PAGES, legalDoc } from "./legal";
import { LegalView } from "./LegalView";
import { operator } from "./operator";
import { PaymentDone } from "./PaymentDone";
import { yearlySaving } from "./PlanPicker";
import { SiteFooter } from "./SiteFooter";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const PERSON = operator({
  SITE_OPERATOR: "Nanguado",
  SITE_OPERATOR_OWNER: "王小明",
  SITE_CONTACT_EMAIL: "service@nanguado.com",
  SITE_CONTACT_PHONE: "02-1234-5678",
});

describe("who runs the site", () => {
  it("defaults to the brand and its address, and leaves a person's details to the environment", () => {
    expect(operator({})).toEqual({ brand: "Nanguado", owner: null, email: "service@nanguado.com", phone: null });
    expect(operator({ SITE_OPERATOR_OWNER: "  ", SITE_CONTACT_PHONE: "" }).owner).toBeNull();
  });

  it("is on every page's footer, with the four links", () => {
    render(<SiteFooter lang="zh-TW" operator={PERSON} />);
    const footer = screen.getByTestId("site-footer");
    expect(footer.textContent).toContain("經營者：Nanguado（王小明）");
    expect(footer.textContent).toContain("02-1234-5678");
    const hrefs = Array.from(footer.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      "/news/zh-TW/pricing",
      "/news/zh-TW/terms",
      "/news/zh-TW/privacy",
      "/news/zh-TW/refund",
      "mailto:service@nanguado.com",
    ]);
  });

  it("says nothing it was not given", () => {
    render(<SiteFooter lang="en" operator={operator({})} />);
    const text = screen.getByTestId("site-footer").textContent!;
    expect(text).toContain("Operated by: Nanguado");
    expect(text).not.toContain("Phone");
    expect(text).not.toContain("(");
  });
});

describe("the policies", () => {
  it.each(LEGAL_PAGES.flatMap((page) => LANGS.map((lang) => [page, lang] as const)))(
    "%s in %s names the operator's address and renders",
    (page, lang) => {
      const doc = legalDoc(page, lang, PERSON);
      expect(doc.sections.length).toBeGreaterThan(2);
      render(<LegalView doc={doc} lang={lang} />);
      expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(doc.title);
      expect(document.body.textContent).toContain("service@nanguado.com");
    },
  );

  it("name no price, so a new price cannot make them wrong", () => {
    for (const page of LEGAL_PAGES) {
      for (const lang of LANGS) {
        expect(JSON.stringify(legalDoc(page, lang, PERSON))).not.toMatch(/NT\$|\d+ ?元/);
      }
    }
  });

  it("promise what the code does: one payment, nothing renewed, 7 days to change your mind", () => {
    const text = JSON.stringify(legalDoc("terms", "zh-TW", PERSON)) + JSON.stringify(legalDoc("refund", "zh-TW", PERSON));
    expect(text).toContain("不會自動續約或自動扣款");
    expect(text).toContain("7 天內");
  });
});

describe("a year against twelve months", () => {
  it("is a whole percent, and nothing when it saves nothing", () => {
    expect(yearlySaving(30, 330)).toBe(8);
    expect(yearlySaving(30, 360)).toBeNull();
    expect(yearlySaving(0, 330)).toBeNull();
  });
});

describe("coming back from PAYUNi", () => {
  it("answers PAYUNi's form POST with the done page, in the reader's language", async () => {
    const response = await POST(new Request("http://site/news/en/membership/return", { method: "POST" }), {
      params: Promise.resolve({ lang: "en" }),
    });
    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe("/news/en/membership/done");
  });

  it("does not follow a language it does not know", async () => {
    const response = await POST(new Request("http://site/x", { method: "POST" }), {
      params: Promise.resolve({ lang: "evil.example" }),
    });
    expect(response.headers.get("Location")).toBe("/news/zh-TW/membership/done");
  });

  function me(body: unknown, status = 200) {
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }

  it("says paid once the API says they are a member, even if it takes a few asks", async () => {
    const until = "2026-10-24T08:00:00Z";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(me({ reader_id: "r", email: "a@b.c", member_until: null }))
      .mockResolvedValue(me({ reader_id: "r", email: "a@b.c", member_until: until }));
    render(<PaymentDone lang="zh-TW" email="service@nanguado.com" every={1} />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("付款完成"));
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("says not yet, and where to write, when the notification never comes", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(me({ reader_id: "r", email: "a@b.c", member_until: null })),
    );
    render(<PaymentDone lang="zh-TW" email="service@nanguado.com" every={1} />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("還沒收到付款確認"));
    expect(screen.getByRole("status").textContent).toContain("service@nanguado.com");
  });

  it("asks a signed-out reader to sign in rather than wait", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(me({}, 401)));
    render(<PaymentDone lang="en" email="service@nanguado.com" every={1} />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Sign in"));
  });
});
