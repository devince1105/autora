"""The Resend sender: what it sends, and that a refusal says why (D-043)."""

import json
import logging

import httpx
import pytest

from autora.infra.email import RESEND_URL, EmailError, Message, ResendSender

MESSAGE = Message(to="reader@example.com", subject="你的登入連結", text="點下面的連結")


def _sender(status: int, body: dict, seen: list) -> ResendSender:
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return ResendSender(
        api_key="re_test",
        sender="Autora <service@nanguado.com>",
        client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
    )


async def test_it_sends_what_resend_expects():
    seen: list[httpx.Request] = []
    await _sender(200, {"id": "e1"}, seen).send(MESSAGE)
    [request] = seen
    assert str(request.url) == RESEND_URL
    assert request.headers["Authorization"] == "Bearer re_test"
    assert json.loads(request.content) == {
        "from": "Autora <service@nanguado.com>",
        "to": ["reader@example.com"],
        "subject": "你的登入連結",
        "text": "點下面的連結",
    }


async def test_a_refusal_says_why_in_the_error_and_the_server_log(caplog):
    """The first real send failed with only "(403)" to go on; Resend had said why."""
    reason = "The nanguado.com domain is not verified. Please, add and verify your domain"
    sender = _sender(403, {"statusCode": 403, "name": "validation_error", "message": reason}, [])
    with caplog.at_level(logging.WARNING), pytest.raises(EmailError, match="not verified"):
        await sender.send(MESSAGE)
    assert reason in caplog.text
