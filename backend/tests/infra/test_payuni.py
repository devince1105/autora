"""T-702: PAYUNi's envelope — sealing what we send, and refusing what was edited.

The stakes are one-sided: getting the crypto nearly right means a notification we cannot read
(a payment lost) or, worse, one we accept that PAYUNi never sent (a membership given away). So
these tests do not only check our two directions against each other — which would pass happily
if both were wrong in the same way — but against a vector produced by an independent
implementation of the documented algorithm (``vector.json``, written by a Node script from the
official PHP SDK's steps).
"""

import json
import time
from decimal import Decimal
from pathlib import Path

import pytest

from autora.infra.payments.payuni import (
    SUCCEEDED,
    TRADE_AWAITING,
    TRADE_PAID,
    EnvelopeError,
    Unsupported,
    acknowledge,
    hash_info,
    open_envelope,
    payment_page,
    read_notification,
    seal,
    unseal,
)

VECTOR = json.loads((Path(__file__).parent / "payuni_vector.json").read_text())
KEY: str = VECTOR["key"]
IV: str = VECTOR["iv"]
FIELDS: dict[str, str] = VECTOR["fields"]


def test_we_can_open_an_envelope_somebody_else_sealed():
    """The one that matters: a real notification is sealed by PAYUNi, not by us."""
    assert unseal(VECTOR["encryptInfo"], VECTOR["hashInfo"], key=KEY, iv=IV) == FIELDS


def test_what_we_seal_is_what_they_sealed():
    """Same fields, same key, same envelope — byte for byte."""
    ours = seal(FIELDS, key=KEY, iv=IV)
    assert ours.encrypt_info == VECTOR["encryptInfo"]
    assert ours.hash_info == VECTOR["hashInfo"]


def test_the_form_a_shop_posts():
    form = seal(FIELDS, key=KEY, iv=IV).as_form("SHOP123")
    assert form["MerID"] == "SHOP123"
    assert form["Version"] == "1.0"
    assert form["HashInfo"] == hash_info(form["EncryptInfo"], key=KEY, iv=IV)
    assert form["HashInfo"] == form["HashInfo"].upper()


def test_a_round_trip_keeps_every_field_including_the_empty_ones():
    fields = {"MerID": "SHOP123", "MerTradeNo": "T-1", "TradeAmt": "360", "Note": ""}
    sealed = seal(fields, key=KEY, iv=IV)
    assert unseal(sealed.encrypt_info, sealed.hash_info, key=KEY, iv=IV) == fields


def test_numbers_travel_as_the_strings_they_are_on_the_wire():
    sealed = seal({"TradeAmt": 360, "Timestamp": 1790000000}, key=KEY, iv=IV)
    assert open_envelope(sealed.encrypt_info, key=KEY, iv=IV) == {
        "TradeAmt": "360",
        "Timestamp": "1790000000",
    }


def test_an_edited_message_is_refused_by_its_tag():
    """Change the ciphertext and keep the hash right: GCM still says no."""
    sealed = seal(FIELDS, key=KEY, iv=IV)
    raw = bytes.fromhex(sealed.encrypt_info)
    edited = (raw[:20] + bytes([raw[20] ^ 0x01]) + raw[21:]).hex()
    with pytest.raises(EnvelopeError, match="edited"):
        unseal(edited, hash_info(edited, key=KEY, iv=IV), key=KEY, iv=IV)


def test_a_message_from_somebody_without_the_secret_is_refused_by_its_hash():
    sealed = seal(FIELDS, key=KEY, iv=IV)
    with pytest.raises(EnvelopeError, match="did not come from PAYUNi"):
        unseal(sealed.encrypt_info, "0" * 64, key=KEY, iv=IV)


def test_another_shop_s_key_does_not_open_it():
    sealed = seal(FIELDS, key=KEY, iv=IV)
    other = "fedcba9876543210fedcba9876543210"
    with pytest.raises(EnvelopeError):
        unseal(sealed.encrypt_info, sealed.hash_info, key=other, iv=IV)


@pytest.mark.parametrize(
    ("key", "iv", "why"),
    [("short", IV, "HashKey"), (KEY, "short", "HashIV"), (KEY + "x", IV, "HashKey")],
)
def test_the_wrong_size_of_secret_is_a_configuration_mistake_not_a_crash(key, iv, why):
    with pytest.raises(EnvelopeError, match=why):
        seal(FIELDS, key=key, iv=iv)


@pytest.mark.parametrize("rubbish", ["", "zzzz", "abcd", "6e6f742d68657865"])
def test_rubbish_in_place_of_an_envelope_is_refused_politely(rubbish):
    with pytest.raises(EnvelopeError):
        open_envelope(rubbish, key=KEY, iv=IV)


# --- the payment page, and what comes back from it ----------------------------------------------


def test_the_payment_page_form_is_the_envelope_and_nothing_else():
    page = payment_page(
        base_url="https://sandbox-api.payuni.com.tw/api",
        mer_id="SHOP123",
        key=KEY,
        iv=IV,
        mer_trade_no="AU0001",
        amount=Decimal("360"),
        description="Autora 會員一年",
        return_url="https://example.com/done",
        notify_url="https://example.com/notify",
        email="reader@example.com",
        now=1790000000,
    )
    assert page.url == "https://sandbox-api.payuni.com.tw/api/upp"
    assert set(page.fields) == {"MerID", "Version", "EncryptInfo", "HashInfo"}
    sent = unseal(page.fields["EncryptInfo"], page.fields["HashInfo"], key=KEY, iv=IV)
    assert sent == {
        "MerID": "SHOP123",
        "MerTradeNo": "AU0001",
        "TradeAmt": "360",
        "Timestamp": "1790000000",
        "ProdDesc": "Autora 會員一年",
        "ReturnURL": "https://example.com/done",
        "NotifyURL": "https://example.com/notify",
        "Lang": "zh-tw",
        "UsrMail": "reader@example.com",
        "Credit": "1",
    }


def test_nothing_about_the_reader_is_sent_unless_there_is_something_to_send():
    page = _page(email=None)
    assert "UsrMail" not in open_envelope(page.fields["EncryptInfo"], key=KEY, iv=IV)


@pytest.mark.parametrize("amount", ["360.50", "0.01"])
def test_a_price_with_cents_is_a_mistake_upstream_not_something_to_round(amount):
    with pytest.raises(Unsupported, match="whole dollars"):
        _page(amount=Decimal(amount))


@pytest.mark.parametrize("amount", ["0", "-360"])
def test_nobody_is_sent_to_a_payment_page_to_pay_nothing(amount):
    with pytest.raises(Unsupported):
        _page(amount=Decimal(amount))


def test_the_timestamp_is_made_at_the_moment_of_asking():
    """PAYUNi checks it against its own clock, so a stale one is a refused payment."""
    before = int(time.time())
    fields = open_envelope(_page().fields["EncryptInfo"], key=KEY, iv=IV)
    assert before <= int(fields["Timestamp"]) <= int(time.time())


def test_a_paid_notification_reads_as_paid():
    notification = _notify()
    assert notification.paid is True
    assert notification.mer_trade_no == "AU0001"
    assert notification.trade_no == "UNI999"
    assert notification.trade_amt == Decimal("360")


@pytest.mark.parametrize(
    ("status", "trade_status"),
    [(SUCCEEDED, TRADE_AWAITING), ("FAIL", TRADE_PAID), ("FAIL", TRADE_AWAITING), (SUCCEEDED, "")],
)
def test_only_a_success_that_says_paid_is_paid(status, trade_status):
    assert _notify(Status=status, TradeStatus=trade_status).paid is False


def test_what_was_posted_beside_the_envelope_does_not_decide_anything():
    """A stranger can post Status=SUCCESS all day; only what was sealed counts."""
    sealed = seal({"Status": "FAIL", "TradeStatus": TRADE_PAID, "TradeAmt": "360"}, key=KEY, iv=IV)
    form = sealed.as_form("SHOP123") | {"Status": SUCCEEDED}
    assert read_notification(form, key=KEY, iv=IV).paid is False


def test_a_notification_with_no_envelope_is_not_a_notification():
    with pytest.raises(EnvelopeError, match="neither"):
        read_notification({"Status": SUCCEEDED, "MerID": "SHOP123"}, key=KEY, iv=IV)


def test_an_amount_that_is_not_a_number_is_refused_rather_than_crashing():
    sealed = seal({"TradeAmt": "three hundred"}, key=KEY, iv=IV)
    with pytest.raises(EnvelopeError, match="not a number"):
        read_notification(sealed.as_form("SHOP123"), key=KEY, iv=IV)


def test_what_payuni_wants_to_hear_back():
    assert acknowledge() == "1|OK"


def _page(*, amount=Decimal("360"), email="reader@example.com", now=None):
    return payment_page(
        base_url="https://sandbox-api.payuni.com.tw/api",
        mer_id="SHOP123",
        key=KEY,
        iv=IV,
        mer_trade_no="AU0001",
        amount=amount,
        description="一年",
        return_url="https://example.com/done",
        notify_url="https://example.com/notify",
        email=email,
        now=now,
    )


def _notify(**overrides):
    fields = {
        "Status": SUCCEEDED,
        "Message": "交易成功",
        "MerID": "SHOP123",
        "MerTradeNo": "AU0001",
        "TradeNo": "UNI999",
        "TradeAmt": "360",
        "TradeStatus": TRADE_PAID,
        "PaymentType": "1",
    } | overrides
    return read_notification(seal(fields, key=KEY, iv=IV).as_form("SHOP123"), key=KEY, iv=IV)
