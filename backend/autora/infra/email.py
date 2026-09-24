"""Sending email (D-025).

One thing is sent today — a login link — and it is sent through one of two senders:

- ``ConsoleSender`` prints the message and keeps it in memory. It is the default, so
  development, tests and the simulation never touch a third party, and the link is right there
  in the terminal to click.
- ``ResendSender`` posts it to Resend.

Which one runs is configuration (``EMAIL_PROVIDER``), never a branch in the caller. A failure to
send is raised, not swallowed: somebody waiting for a link should be told to try again, not left
watching an empty inbox.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from autora.infra.settings import Settings, get_settings

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


class EmailError(Exception):
    pass


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str | None = None


class Sender(Protocol):
    async def send(self, message: Message) -> None: ...


@dataclass
class ConsoleSender:
    """Prints instead of sending, and remembers what it printed (tests read ``sent``).

    It prints rather than logs, on purpose: in development this is the only way to reach the
    link, and a log line the server's logging configuration happens to swallow would leave
    somebody staring at a terminal that says nothing."""

    sent: list[Message] = field(default_factory=list)
    echo: bool = True

    async def send(self, message: Message) -> None:
        self.sent.append(message)
        log.info("email to %s: %s", message.to, message.subject)
        if self.echo:
            print(  # noqa: T201 - the point of this sender
                f"\n--- email to {message.to} ---\n{message.subject}\n{message.text}",
                flush=True,
            )


@dataclass
class ResendSender:
    api_key: str
    sender: str
    timeout_seconds: float = 10.0
    client: httpx.AsyncClient | None = None

    async def send(self, message: Message) -> None:
        payload = {
            "from": self.sender,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        if message.html:
            payload["html"] = message.html
        client = self.client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            response = await client.post(
                RESEND_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if response.status_code >= 400:
                # Resend says why (an unverified sender domain, a send-only key used for more):
                # keep it in the server log, where the operator can read it; readers only ever
                # see "could not be sent"
                try:
                    reason = str(response.json().get("message", ""))[:300]
                except ValueError:
                    reason = response.text[:300]
                log.warning("Resend refused a message (%s): %s", response.status_code, reason)
                raise EmailError(f"Resend refused the message ({response.status_code}): {reason}")
        except httpx.HTTPError as exc:
            raise EmailError(f"Resend could not be reached: {exc}") from exc
        finally:
            if self.client is None:
                await client.aclose()


def build_sender(settings: Settings | None = None) -> Sender:
    settings = settings or get_settings()
    if settings.email_provider == "resend":
        key = settings.resend_api_key
        if key is None:  # pragma: no cover - settings refuse this combination first
            raise EmailError("RESEND_API_KEY is required when EMAIL_PROVIDER=resend")
        return ResendSender(api_key=key.get_secret_value(), sender=settings.email_from)
    return ConsoleSender()
