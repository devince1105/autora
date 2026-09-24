"""T-702: buying a year over HTTP, from the checkout form to the notification that pays.

PAYUNi's own servers are never contacted here. The notifications are sealed with the same shop
secrets the app is configured with, which is exactly what PAYUNi does — so what these tests
exercise is every check the handler makes, including the ones that decide a message is not
PAYUNi's. What they cannot show is that PAYUNi agrees with our reading of its documentation;
only a sandbox store can, and the crypto is cross-checked against an independent implementation
in ``tests/infra/test_payuni.py``.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from autora.company import memberships
from autora.company.organization import add_business_unit, add_product
from autora.db.models import (
    BusinessUnitState,
    Company,
    Order,
    OrderState,
    Payment,
    PriceInterval,
    ProductState,
)
from autora.infra.payments import payuni
from autora.infra.settings import load_settings
from autora.runtime.actor import Actor
from autora_api.deps import settings_dep

OPERATOR = Actor.human("operator")
ADDRESS = "buyer@example.com"

MER_ID = "TESTSHOP"
KEY = "0123456789abcdef0123456789abcdef"
IV = "0123456789abcdef"


@pytest.fixture
async def shop(api, db_settings):
    """The app, configured as a PAYUNi store. The secrets are this test's, not anybody's."""
    app = api._transport.app
    app.dependency_overrides[settings_dep] = lambda: load_settings(
        database_url=db_settings.database_url,
        api_bearer_token="test-operator-token",
        payuni_mer_id=MER_ID,
        payuni_hash_key=KEY,
        payuni_hash_iv=IV,
    )
    async with httpx.AsyncClient(transport=api._transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def unconfigured(api, db_settings):
    """The same app with no store: a site that has not applied for one yet.

    The three ids are cleared explicitly rather than left out, because a developer running the
    suite has a real ``.env`` and this test would otherwise pass or fail depending on whether
    they happen to have a PAYUNi store.
    """
    app = api._transport.app
    app.dependency_overrides[settings_dep] = lambda: load_settings(
        database_url=db_settings.database_url,
        api_bearer_token="test-operator-token",
        payuni_mer_id=None,
        payuni_hash_key=None,
        payuni_hash_iv=None,
    )
    async with httpx.AsyncClient(transport=api._transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def company(db_session):
    """The company, as plain values.

    Deliberately not the ORM row: the handlers commit, a commit expires every loaded object, and
    reading ``company.slug`` afterwards would quietly go back to the database from a place that
    cannot await. What a test needs from a company is an id and a slug.
    """
    row = Company(name="Autora Test", slug=f"t{uuid.uuid4().hex[:8]}")
    db_session.add(row)
    await db_session.flush()
    return SimpleNamespace(id=row.id, slug=row.slug)


async def _for_sale(db_session, company, amount="360", month=None):
    unit = await add_business_unit(
        db_session, company_id=company.id, key="ai_media", name="AI Media",
        actor=OPERATOR, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    product = await add_product(
        db_session, company_id=company.id, key=memberships.PRODUCT_KEY, name="Membership",
        business_unit_id=unit.id, actor=OPERATOR, state=ProductState.LIVE,
    )  # fmt: skip
    price = await memberships.add_price(db_session, product, amount=Decimal(amount))
    if month is not None:
        await memberships.add_price(
            db_session, product, amount=Decimal(month), interval=PriceInterval.MONTH
        )
    await db_session.commit()
    return price


async def _sign_in(client, mailbox, address=ADDRESS):
    await client.post("/api/auth/link", json={"email": address})
    token = re.search(r"token=([A-Za-z0-9_\-]+)", mailbox.sent[-1].text).group(1)
    verified = await client.post("/api/auth/verify", json={"token": token})
    return uuid.UUID(verified.json()["reader_id"])


def _notification(mer_trade_no, *, amount="360", trade_status=payuni.TRADE_PAID, trade_no=None):
    """What PAYUNi posts, sealed the way PAYUNi seals it."""
    fields = {
        "Status": payuni.SUCCEEDED,
        "Message": "交易成功",
        "MerID": MER_ID,
        "MerTradeNo": mer_trade_no,
        "TradeNo": trade_no or f"UNI{uuid.uuid4().hex[:12]}",
        "TradeAmt": str(amount),
        "TradeStatus": trade_status,
        "PaymentType": "1",
    }
    return payuni.seal(fields, key=KEY, iv=IV).as_form(MER_ID) | {"Status": payuni.SUCCEEDED}


async def _member_until(client, company):
    """Always with the slug: without one the API answers for the oldest company it can find,
    which in a suite that has made several is somebody else's."""
    return (await client.get("/api/auth/me", params={"company": company.slug})).json()[
        "member_until"
    ]


# --- what a year costs --------------------------------------------------------------------------


async def test_a_site_with_nothing_for_sale_says_so_rather_than_failing(shop, company):
    offer = (await shop.get("/api/checkout/offer", params={"company": company.slug})).json()
    assert offer["available"] is False


async def test_the_offer_is_the_price_that_was_set(shop, db_session, company):
    await _for_sale(db_session, company, amount="360")
    offer = (await shop.get("/api/checkout/offer", params={"company": company.slug})).json()
    assert Decimal(offer.pop("amount")) == Decimal("360")
    assert offer == {"currency": "TWD", "interval": "year", "available": True}


async def test_a_month_and_a_year_are_each_their_own_price(shop, db_session, company):
    """D-034: both on sale at once, and asking for one never answers with the other."""
    await _for_sale(db_session, company, amount="330", month="30")
    ask = {"company": company.slug}
    year = (await shop.get("/api/checkout/offer", params=ask | {"interval": "year"})).json()
    month = (await shop.get("/api/checkout/offer", params=ask | {"interval": "month"})).json()
    assert (Decimal(year["amount"]), year["interval"]) == (Decimal("330"), "year")
    assert (Decimal(month["amount"]), month["interval"]) == (Decimal("30"), "month")
    assert Decimal((await shop.get("/api/checkout/offer", params=ask)).json()["amount"]) == 330


async def test_a_month_that_is_not_for_sale_is_not_the_year_instead(shop, db_session, company):
    await _for_sale(db_session, company)
    offer = (
        await shop.get("/api/checkout/offer", params={"company": company.slug, "interval": "month"})
    ).json()
    assert offer["available"] is False
    assert offer["interval"] == "month"


# --- starting a checkout ------------------------------------------------------------------------


async def test_nobody_buys_a_membership_without_signing_in(shop, db_session, company):
    await _for_sale(db_session, company)
    response = await shop.post("/api/checkout", json={"company": company.slug})
    assert response.status_code == 401


async def test_a_site_without_a_store_says_payments_are_not_configured(
    unconfigured, db_session, company, mailbox
):
    await _for_sale(db_session, company)
    await _sign_in(unconfigured, mailbox)
    response = await unconfigured.post("/api/checkout", json={"company": company.slug})
    assert response.status_code == 503


async def test_checkout_hands_back_a_form_and_writes_a_pending_order(
    shop, db_session, company, mailbox
):
    await _for_sale(db_session, company)
    await _sign_in(shop, mailbox)

    checkout = (await shop.post("/api/checkout", json={"company": company.slug})).json()
    assert checkout["url"].endswith("/upp")
    assert Decimal(checkout["amount"]) == Decimal("360")
    assert set(checkout["fields"]) == {"MerID", "Version", "EncryptInfo", "HashInfo"}

    order = await db_session.get(Order, uuid.UUID(checkout["order_id"]))
    assert order.state == OrderState.PENDING.value
    assert order.mer_trade_no == checkout["mer_trade_no"]
    assert await _member_until(shop, company) is None, "an order is not a payment"


async def test_the_form_carries_the_order_and_can_be_opened_with_the_shop_s_key(
    shop, db_session, company, mailbox
):
    """The envelope is not decoration: PAYUNi will read these fields out of it."""
    await _for_sale(db_session, company)
    await _sign_in(shop, mailbox)
    checkout = (await shop.post("/api/checkout", json={"company": company.slug})).json()

    sent = payuni.unseal(
        checkout["fields"]["EncryptInfo"], checkout["fields"]["HashInfo"], key=KEY, iv=IV
    )
    assert sent["MerTradeNo"] == checkout["mer_trade_no"]
    assert sent["TradeAmt"] == "360"
    assert sent["UsrMail"] == ADDRESS
    assert sent["NotifyURL"].endswith("/api/payments/payuni/notify")


async def test_a_month_is_ordered_at_the_month_s_price_and_buys_a_month(
    shop, db_session, company, mailbox
):
    await _for_sale(db_session, company, amount="330", month="30")
    await _sign_in(shop, mailbox)
    checkout = (
        await shop.post("/api/checkout", json={"company": company.slug, "interval": "month"})
    ).json()
    sent = payuni.unseal(
        checkout["fields"]["EncryptInfo"], checkout["fields"]["HashInfo"], key=KEY, iv=IV
    )
    assert (sent["TradeAmt"], sent["ProdDesc"]) == ("30", "Autora 會員一個月")

    await shop.post(
        "/api/payments/payuni/notify", data=_notification(checkout["mer_trade_no"], amount="30")
    )
    until = datetime.fromisoformat(await _member_until(shop, company))
    now = datetime.now(UTC)
    assert now + timedelta(days=27) < until < now + timedelta(days=32), "a month, not a year"


async def test_an_interval_nobody_sells_is_refused(shop, db_session, company, mailbox):
    await _for_sale(db_session, company)
    await _sign_in(shop, mailbox)
    week = await shop.post("/api/checkout", json={"company": company.slug, "interval": "week"})
    month = await shop.post("/api/checkout", json={"company": company.slug, "interval": "month"})
    assert week.status_code == 422
    assert month.status_code == 404


async def test_nothing_is_for_sale_is_a_404_not_an_order(shop, company, mailbox):
    await _sign_in(shop, mailbox)
    response = await shop.post("/api/checkout", json={"company": company.slug})
    assert response.status_code == 404


# --- the notification ---------------------------------------------------------------------------


async def test_a_paid_notification_buys_the_year(shop, db_session, company, mailbox):
    await _for_sale(db_session, company)
    await _sign_in(shop, mailbox)
    checkout = (await shop.post("/api/checkout", json={"company": company.slug})).json()
    assert await _member_until(shop, company) is None

    response = await shop.post(
        "/api/payments/payuni/notify", data=_notification(checkout["mer_trade_no"])
    )
    assert response.status_code == 200
    assert response.text == "1|OK"
    assert await _member_until(shop, company) is not None

    order = await db_session.get(Order, uuid.UUID(checkout["order_id"]))
    await db_session.refresh(order)
    assert order.state == OrderState.PAID.value
    assert order.payment_id is not None


async def test_the_same_notification_again_changes_nothing(shop, db_session, company, mailbox):
    await _for_sale(db_session, company)
    await _sign_in(shop, mailbox)
    checkout = (await shop.post("/api/checkout", json={"company": company.slug})).json()
    form = _notification(checkout["mer_trade_no"])

    first = await shop.post("/api/payments/payuni/notify", data=form)
    until = await _member_until(shop, company)
    again = await shop.post("/api/payments/payuni/notify", data=form)

    assert (first.text, again.text) == ("1|OK", "1|OK")
    assert await _member_until(shop, company) == until
    assert await _count(db_session, Payment, Payment.company_id == company.id) == 1


async def test_a_notification_nobody_sealed_is_refused(shop, db_session, company, mailbox):
    await _for_sale(db_session, company)
    await _sign_in(shop, mailbox)
    checkout = (await shop.post("/api/checkout", json={"company": company.slug})).json()

    forged = _notification(checkout["mer_trade_no"]) | {"HashInfo": "0" * 64}
    response = await shop.post("/api/payments/payuni/notify", data=forged)

    assert response.status_code == 400
    assert await _member_until(shop, company) is None


async def test_a_notification_that_says_a_different_amount_is_refused(
    shop, db_session, company, mailbox
):
    await _for_sale(db_session, company, amount="360")
    await _sign_in(shop, mailbox)
    checkout = (await shop.post("/api/checkout", json={"company": company.slug})).json()

    response = await shop.post(
        "/api/payments/payuni/notify", data=_notification(checkout["mer_trade_no"], amount="1")
    )
    assert response.status_code == 400
    assert await _member_until(shop, company) is None
    assert await _count(db_session, Payment, Payment.company_id == company.id) == 0


async def test_a_trade_number_we_never_issued_is_refused(shop, db_session, company, mailbox):
    await _for_sale(db_session, company)
    await _sign_in(shop, mailbox)
    response = await shop.post(
        "/api/payments/payuni/notify", data=_notification("AU000000000000000000")
    )
    assert response.status_code == 400
    assert await _member_until(shop, company) is None


async def test_a_refusal_says_nothing_about_which_check_failed(shop, db_session, company, mailbox):
    """The only reader of this message is somebody guessing at trade numbers."""
    await _for_sale(db_session, company, amount="360")
    await _sign_in(shop, mailbox)
    checkout = (await shop.post("/api/checkout", json={"company": company.slug})).json()

    wrong_amount = await shop.post(
        "/api/payments/payuni/notify", data=_notification(checkout["mer_trade_no"], amount="1")
    )
    no_such_order = await shop.post(
        "/api/payments/payuni/notify", data=_notification("AU000000000000000000")
    )
    assert wrong_amount.text == no_such_order.text
    for body in (wrong_amount.text, no_such_order.text):
        assert checkout["mer_trade_no"] not in body
        assert "360" not in body


async def test_an_unpaid_notification_is_heard_but_buys_nothing(shop, db_session, company, mailbox):
    """An ATM code was issued. PAYUNi has said something true; it is just not a payment."""
    await _for_sale(db_session, company)
    await _sign_in(shop, mailbox)
    checkout = (await shop.post("/api/checkout", json={"company": company.slug})).json()

    response = await shop.post(
        "/api/payments/payuni/notify",
        data=_notification(checkout["mer_trade_no"], trade_status=payuni.TRADE_AWAITING),
    )
    assert response.text == "1|OK"
    assert await _member_until(shop, company) is None

    order = await db_session.get(Order, uuid.UUID(checkout["order_id"]))
    await db_session.refresh(order)
    assert order.state == OrderState.PENDING.value, "still waiting, not failed"


async def test_a_second_year_extends_the_first(shop, db_session, company, mailbox):
    await _for_sale(db_session, company)
    await _sign_in(shop, mailbox)

    first = (await shop.post("/api/checkout", json={"company": company.slug})).json()
    await shop.post("/api/payments/payuni/notify", data=_notification(first["mer_trade_no"]))
    after_one = await _member_until(shop, company)

    second = (await shop.post("/api/checkout", json={"company": company.slug})).json()
    await shop.post("/api/payments/payuni/notify", data=_notification(second["mer_trade_no"]))
    after_two = await _member_until(shop, company)

    assert after_two > after_one
    assert await _count(db_session, Payment, Payment.company_id == company.id) == 2


async def _count(session, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where))
