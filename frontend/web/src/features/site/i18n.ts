// The public site's two languages (D-002: zh-TW first, then en) and its few words of UI.
export const LANGS = ["zh-TW", "en"] as const;
export type Lang = (typeof LANGS)[number];

export function isLang(value: string): value is Lang {
  return (LANGS as readonly string[]).includes(value);
}

export const LANG_NAMES: Record<Lang, string> = { "zh-TW": "中文", en: "English" };

const WORDS = {
  "zh-TW": {
    site: "Autora 新聞",
    latest: "最新報導",
    empty: "還沒有報導。",
    sources: "資料來源",
    published: "發布於",
    allStories: "所有報導",
    readIn: "閱讀其他語言：",
    notice: "本站為示範：報導由 AI 新聞室撰寫、事實查核並經人核准。",
    membersOnly: "這篇是會員專屬",
    membersOnlyWhy: "訂閱會員一年 NT$360，可閱讀所有會員專屬報導。",
    signIn: "登入",
    signOut: "登出",
    signedInAs: "已登入",
    member: "會員",
    memberUntil: "會員資格到期日",
    becomeMember: "成為會員",
    loginTitle: "登入",
    loginHint: "輸入你的 email，我們會寄一封登入連結給你。不需要密碼。",
    loginSend: "寄出登入連結",
    loginSent: "信寄出了。打開信箱裡的連結就能登入，連結 15 分鐘內有效。",
    loginFailed: "寄不出去，請稍後再試一次。",
    verifying: "登入中…",
    verifyFailed: "這個連結已經失效，請重新要求一次。",
  },
  en: {
    site: "Autora News",
    latest: "Latest stories",
    empty: "No stories yet.",
    sources: "Sources",
    published: "Published",
    allStories: "All stories",
    readIn: "Read in:",
    notice: "A demo: stories are written and fact-checked by an AI newsroom and approved by a person.",
    membersOnly: "This one is for members",
    membersOnlyWhy: "Membership is NT$360 a year and opens every members-only story.",
    signIn: "Sign in",
    signOut: "Sign out",
    signedInAs: "Signed in",
    member: "Member",
    memberUntil: "Member until",
    becomeMember: "Become a member",
    loginTitle: "Sign in",
    loginHint: "Type your email and we will send you a link. No password.",
    loginSend: "Send the link",
    loginSent: "Sent. Open the link in your inbox within 15 minutes.",
    loginFailed: "It could not be sent. Please try again.",
    verifying: "Signing you in…",
    verifyFailed: "This link no longer works. Ask for a new one.",
  },
} as const;

export function words(lang: Lang) {
  return WORDS[lang];
}

export function formatDate(lang: Lang, iso: string): string {
  return new Intl.DateTimeFormat(lang, { dateStyle: "long", timeZone: "Asia/Taipei" }).format(
    new Date(iso),
  );
}
