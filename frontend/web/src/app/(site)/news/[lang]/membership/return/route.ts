// Where PAYUNi sends the reader's browser after paying (PAYUNI_RETURN_URL, D-034).
//
// PAYUNi comes back with a form POST, and a page cannot answer a POST, so this answers it with
// "see the done page". What the form says is not read: it grants nothing and proves nothing —
// the membership comes from PAYUNi's server-to-server notification, and the done page asks the
// API what that has made true. A relative Location keeps it right behind any proxy.
import { isLang } from "@/features/site/i18n";

type Context = { params: Promise<{ lang: string }> };

async function toDone(_request: Request, { params }: Context): Promise<Response> {
  const { lang } = await params;
  return new Response(null, {
    status: 303,
    headers: { Location: `/news/${isLang(lang) ? lang : "zh-TW"}/membership/done` },
  });
}

export const POST = toDone;
export const GET = toDone;
