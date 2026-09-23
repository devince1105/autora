"""PAYUNi's envelope: how a request and a notification are sealed (T-702, D-024).

Every message between a shop and PAYUNi is the same shape — the fields as a query string,
encrypted, and a hash of that so both sides can tell it was not edited:

    EncryptInfo = hex( base64(AES-256-GCM ciphertext) + ":::" + base64(tag) )
    HashInfo    = SHA256( HashKey + EncryptInfo + HashIV ).upper()

The key is the shop's HashKey (32 characters) and the IV its HashIV (16). Both come from the
merchant back office and live only in the environment (``PAYUNI_HASH_KEY``, ``PAYUNI_HASH_IV``).

This module is the whole of what is provider-specific about taking money. It knows nothing about
memberships, orders or companies: it turns fields into an envelope and an envelope back into
fields, and says no when the envelope has been tampered with. What the fields *mean* is decided
above it, which is what keeps ``provider`` a token everywhere else (D-022).

Written against the official PHP SDK (github.com/payuni/PHP_SDK, ``PayuniApi.php``); the tests
check our two directions against each other and against a vector produced by an independent
implementation, because being almost right about an envelope is the same as being wrong.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from base64 import b64decode, b64encode
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SEPARATOR = b":::"
"""What PAYUNi puts between the ciphertext and the tag, before the whole thing becomes hex."""


class EnvelopeError(Exception):
    """The message is not one we can open, or not one this shop sealed."""


@dataclass(frozen=True)
class Envelope:
    """What goes on the wire, both ways."""

    encrypt_info: str
    hash_info: str

    def as_form(self, mer_id: str, version: str = "1.0") -> dict[str, str]:
        """The fields a browser posts, or an API call sends."""
        return {
            "MerID": mer_id,
            "Version": version,
            "EncryptInfo": self.encrypt_info,
            "HashInfo": self.hash_info,
        }


def _material(key: str, iv: str) -> tuple[bytes, bytes]:
    key_bytes = key.strip().encode()
    iv_bytes = iv.strip().encode()
    if len(key_bytes) != 32:
        raise EnvelopeError(f"HashKey must be 32 characters, got {len(key_bytes)}")
    if len(iv_bytes) != 16:
        raise EnvelopeError(f"HashIV must be 16 characters, got {len(iv_bytes)}")
    return key_bytes, iv_bytes


def hash_info(encrypt_info: str, *, key: str, iv: str) -> str:
    return hashlib.sha256(f"{key.strip()}{encrypt_info}{iv.strip()}".encode()).hexdigest().upper()


def seal(fields: dict[str, str | int], *, key: str, iv: str) -> Envelope:
    """Fields → the envelope PAYUNi expects."""
    key_bytes, iv_bytes = _material(key, iv)
    payload = urlencode({name: str(value) for name, value in fields.items()}).encode()
    sealed = AESGCM(key_bytes).encrypt(iv_bytes, payload, None)
    # the library appends the 16-byte tag; PAYUNi keeps the two apart, base64 each
    ciphertext, tag = sealed[:-16], sealed[-16:]
    joined = b64encode(ciphertext) + SEPARATOR + b64encode(tag)
    encrypt_info = joined.hex()
    return Envelope(encrypt_info=encrypt_info, hash_info=hash_info(encrypt_info, key=key, iv=iv))


def open_envelope(encrypt_info: str, *, key: str, iv: str) -> dict[str, str]:
    """The envelope → its fields. Raises when it cannot be opened."""
    key_bytes, iv_bytes = _material(key, iv)
    try:
        joined = bytes.fromhex(encrypt_info.strip())
    except ValueError as exc:
        raise EnvelopeError("EncryptInfo is not hexadecimal") from exc
    if SEPARATOR not in joined:
        raise EnvelopeError("EncryptInfo has no tag")
    ciphertext_b64, _, tag_b64 = joined.partition(SEPARATOR)
    try:
        ciphertext = b64decode(ciphertext_b64, validate=True)
        tag = b64decode(tag_b64, validate=True)
    except ValueError as exc:
        raise EnvelopeError("EncryptInfo is not base64 inside") from exc
    try:
        payload = AESGCM(key_bytes).decrypt(iv_bytes, ciphertext + tag, None)
    except InvalidTag as exc:
        # the tag is the point: an edited message fails here rather than decoding to nonsense
        raise EnvelopeError("this message was edited after it was sealed") from exc
    return dict(parse_qsl(payload.decode(), keep_blank_values=True))


def unseal(encrypt_info: str, given_hash: str, *, key: str, iv: str) -> dict[str, str]:
    """Check the hash, then open it. Both, in that order, or nothing.

    The hash says the shop and PAYUNi share a secret; the tag says the message is intact. A
    notification that fails either is not a payment, however much it looks like one.
    """
    expected = hash_info(encrypt_info, key=key, iv=iv)
    if not hmac.compare_digest(expected, given_hash.strip().upper()):
        raise EnvelopeError("HashInfo does not match: this did not come from PAYUNi")
    return open_envelope(encrypt_info, key=key, iv=iv)


# --- the payment page, and what comes back from it ----------------------------------------------

SUCCEEDED = "SUCCESS"
"""The outer ``Status``: PAYUNi accepted the request. Not the same as "the money arrived"."""

TRADE_PAID = "1"
TRADE_AWAITING = "0"
"""``TradeStatus``: 1 is paid, 0 is waiting (an ATM code was issued, say). Only 1 buys anything."""


class Unsupported(Exception):
    """Something we cannot send to PAYUNi at all, found before anybody is sent anywhere."""


@dataclass(frozen=True)
class PaymentPage:
    """Where to send somebody to pay, and what to send with them.

    A browser posts ``fields`` to ``url`` as a form. Nothing here is secret — the fields are the
    sealed envelope — so it is safe to hand the whole thing to the reader's page.
    """

    url: str
    fields: dict[str, str]


def payment_page(
    *,
    base_url: str,
    mer_id: str,
    key: str,
    iv: str,
    mer_trade_no: str,
    amount: Decimal | int,
    description: str,
    return_url: str,
    notify_url: str,
    email: str | None = None,
    lang: str = "zh-tw",
    methods: Sequence[str] = ("Credit",),
    now: int | None = None,
) -> PaymentPage:
    """Build the form that opens PAYUNi's own payment page (UPP).

    ``amount`` is in whole New Taiwan dollars, because that is the only thing PAYUNi's field
    holds; a price with cents in it is a mistake upstream, not something to round away here.
    ``Timestamp`` is checked against PAYUNi's clock, so it is made at the moment of asking.
    """
    whole = Decimal(amount)
    if whole != whole.to_integral_value():
        raise Unsupported(f"TradeAmt is whole dollars; {amount} is not one")
    if whole <= 0:
        raise Unsupported("nobody is sent to a payment page to pay nothing")
    fields: dict[str, str | int] = {
        "MerID": mer_id,
        "MerTradeNo": mer_trade_no,
        "TradeAmt": int(whole),
        "Timestamp": int(time.time()) if now is None else now,
        "ProdDesc": description,
        "ReturnURL": return_url,
        "NotifyURL": notify_url,
        "Lang": lang,
    }
    if email:
        fields["UsrMail"] = email
    for method in methods:
        fields[method] = 1
    envelope = seal(fields, key=key, iv=iv)
    return PaymentPage(url=f"{base_url.rstrip('/')}/upp", fields=envelope.as_form(mer_id))


@dataclass(frozen=True)
class Notification:
    """What PAYUNi posts to NotifyURL once somebody has paid (or not)."""

    status: str
    mer_trade_no: str
    trade_no: str
    trade_amt: Decimal
    trade_status: str
    payment_type: str
    message: str
    fields: dict[str, str]

    @property
    def paid(self) -> bool:
        return self.status == SUCCEEDED and self.trade_status == TRADE_PAID


def read_notification(form: Mapping[str, str], *, key: str, iv: str) -> Notification:
    """A posted form → the notification it carries, or ``EnvelopeError``.

    The envelope is opened first and everything is read out of it. The fields posted beside it
    (``Status``, ``MerID``) are what anybody could have typed; only what was sealed counts.
    """
    encrypt_info = form.get("EncryptInfo", "")
    given_hash = form.get("HashInfo", "")
    if not encrypt_info or not given_hash:
        raise EnvelopeError("a notification is an EncryptInfo and a HashInfo; this is neither")
    fields = unseal(encrypt_info, given_hash, key=key, iv=iv)
    try:
        amount = Decimal(fields.get("TradeAmt", ""))
    except InvalidOperation as exc:
        raise EnvelopeError(f"TradeAmt {fields.get('TradeAmt')!r} is not a number") from exc
    return Notification(
        status=fields.get("Status", form.get("Status", "")),
        mer_trade_no=fields.get("MerTradeNo", ""),
        trade_no=fields.get("TradeNo", ""),
        trade_amt=amount,
        trade_status=fields.get("TradeStatus", ""),
        payment_type=fields.get("PaymentType", ""),
        message=fields.get("Message", ""),
        fields=fields,
    )


def acknowledge() -> str:
    """What PAYUNi wants back when a notification has been dealt with. Anything else is a retry."""
    return "1|OK"
