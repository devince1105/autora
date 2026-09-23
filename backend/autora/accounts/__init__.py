"""Readers: who signs in to read, and nothing else about them (D-024, D-025).

This package is the only holder of a person's identity in Autora. Nothing below it may import
it — ``.importlinter`` has a contract that says so — so no agent, no domain and no part of the
company layer can read an address. The company's side of a reader is a customer whose
``external_ref`` is ``reader:<id>``.
"""

from autora.accounts.models import LoginToken, Reader, ReaderSession
from autora.accounts.service import (
    SESSION_COOKIE,
    AccountError,
    Link,
    by_email,
    customer_ref,
    normalise,
    reader_for,
    redeem,
    request_link,
    sign_out,
)

__all__ = [
    "SESSION_COOKIE",
    "AccountError",
    "Link",
    "LoginToken",
    "Reader",
    "ReaderSession",
    "by_email",
    "customer_ref",
    "normalise",
    "redeem",
    "reader_for",
    "request_link",
    "sign_out",
]
