"""What a reader is sent (D-025). One message today: the link that signs them in.

Written in zh-TW, the product's language (D-002). It says what it is, how long it lasts, and
what to do if it was not them — which is nothing, because a link nobody opens does nothing.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from autora.infra.email import Message

SUBJECT = "艾矽鯨｜你的登入連結"
"""The site's name first: a reader should know who a sign-in link is from before opening it."""


DEFAULT_LANG = "zh-TW"


def login_url(
    site_base_url: str, token: str, *, lang: str = DEFAULT_LANG, next_path: str | None = None
) -> str:
    """Where the link goes: the site's verify page, in the language they were reading."""
    site = site_base_url.rstrip("/")
    url = f"{site}/news/{quote(lang)}/login/verify?token={quote(token)}"
    return f"{url}&next={quote(next_path)}" if next_path else url


def login_email(to: str, url: str, expires_at: datetime, *, minutes: int = 15) -> Message:
    text = (
        "你好，\n\n"
        f"點下面的連結就能登入艾矽鯨，{minutes} 分鐘內有效，而且只能用一次：\n\n"
        f"{url}\n\n"
        "如果這不是你本人要求的，不用做任何事，這封信可以直接刪除。\n"
    )
    html = (
        "<p>你好，</p>"
        f"<p>點下面的連結就能登入艾矽鯨，{minutes} 分鐘內有效，而且只能用一次：</p>"
        f'<p><a href="{url}">立即登入</a></p>'
        f'<p style="color:#666;font-size:12px">{url}</p>'
        "<p>如果這不是你本人要求的，不用做任何事，這封信可以直接刪除。</p>"
    )
    return Message(to=to, subject=SUBJECT, text=text, html=html)


ADMIN_SUBJECT = "艾矽鯨後台｜你的登入連結"


def admin_login_url(site_base_url: str, token: str, *, next_path: str | None = None) -> str:
    """The back office's verify page (D-055)."""
    url = f"{site_base_url.rstrip('/')}/admin/login/verify?token={quote(token)}"
    return f"{url}&next={quote(next_path)}" if next_path else url


def admin_login_email(to: str, url: str, expires_at: datetime, *, minutes: int = 15) -> Message:
    """Like a reader's, but it says it opens the back office: somebody who did not ask to run
    the company should know at once that this one matters."""
    text = (
        "你好，\n\n"
        f"點下面的連結就能登入艾矽鯨後台，{minutes} 分鐘內有效，而且只能用一次：\n\n"
        f"{url}\n\n"
        "如果這不是你本人要求的，請不要點，並檢查你的信箱是否安全。\n"
    )
    html = (
        "<p>你好，</p>"
        "<p>點下面的連結就能登入<strong>艾矽鯨後台</strong>，"
        f"{minutes} 分鐘內有效，而且只能用一次：</p>"
        f'<p><a href="{url}">登入後台</a></p>'
        f'<p style="color:#666;font-size:12px">{url}</p>'
        "<p>如果這不是你本人要求的，請不要點，並檢查你的信箱是否安全。</p>"
    )
    return Message(to=to, subject=ADMIN_SUBJECT, text=text, html=html)
