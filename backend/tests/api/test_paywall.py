"""D-025: a members-only article is not sent to a browser that may not read it.

The point of these is the negative one: without a membership the rest of the text never leaves
the server, so "hidden" cannot mean "hidden with CSS".
"""

import re
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from autora.accounts import customer_ref
from autora.company import memberships
from autora.company.organization import add_business_unit, add_product
from autora.db.models import BusinessUnitState, Company, Customer, ProductState
from autora.domains.newsroom.models import Article, ArticleAccess
from autora.runtime.actor import Actor

OPERATOR = Actor.human("operator")
PAYUNI = Actor.system("payments:payuni")
ADDRESS = "member@example.com"


@pytest.fixture
async def site(api):
    async with httpx.AsyncClient(transport=api._transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def article(newsroom_room):
    article_id = await newsroom_room.publish()
    async with newsroom_room.committed() as session:
        return await session.get(Article, uuid.UUID(article_id))


async def _members_only(newsroom_room, article):
    async with newsroom_room.committed() as session:
        row = await session.get(Article, article.id)
        row.access = ArticleAccess.MEMBERS.value
        await session.commit()


async def _sign_in(site, mailbox, address=ADDRESS):
    await site.post("/api/auth/link", json={"email": address})
    token = re.search(r"token=([A-Za-z0-9_\-]+)", mailbox.sent[-1].text).group(1)
    verified = await site.post("/api/auth/verify", json={"token": token})
    return uuid.UUID(verified.json()["reader_id"])


async def _buy(session, company_id, reader_id):
    """A year, bought the way a payment notification will buy it (D-024)."""
    unit = await add_business_unit(
        session, company_id=company_id, key="ai_media", name="AI Media",
        actor=OPERATOR, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    product = await add_product(
        session, company_id=company_id, key="daily", name="Daily",
        business_unit_id=unit.id, actor=OPERATOR, state=ProductState.LIVE,
    )  # fmt: skip
    price = await memberships.add_price(session, product, amount=Decimal("360"))
    await memberships.purchase(
        session, price=price, customer_ref=customer_ref(reader_id), provider="payuni",
        external_ref=f"T{uuid.uuid4().hex[:10]}", actor=PAYUNI,
    )  # fmt: skip


async def test_a_free_article_is_whole_for_anybody(site, article):
    body = (await site.get(f"/api/public/articles/zh-TW/{article.slug}")).json()
    assert body["access"] == "free" and body["locked"] is False
    assert len(body["blocks"]) == 2, "the whole of it, short as it is"


async def test_a_members_only_article_is_an_opening_for_a_stranger(site, article, newsroom_room):
    whole = (await site.get(f"/api/public/articles/zh-TW/{article.slug}")).json()
    await _members_only(newsroom_room, article)

    body = (await site.get(f"/api/public/articles/zh-TW/{article.slug}")).json()

    assert body["access"] == "members" and body["locked"] is True
    assert len(body["blocks"]) < len(whole["blocks"]), "a preview is never the whole thing"
    assert body["sources"] == [], "the sources are part of the article"
    rest = whole["blocks"][-1]["text"]
    assert rest not in (await site.get(f"/api/public/articles/zh-TW/{article.slug}")).text


async def test_signing_in_is_not_paying(site, mailbox, article, newsroom_room):
    await _members_only(newsroom_room, article)
    await _sign_in(site, mailbox)

    body = (await site.get(f"/api/public/articles/zh-TW/{article.slug}")).json()
    assert body["locked"] is True


async def test_a_member_reads_the_whole_thing(site, mailbox, article, newsroom_room, db_session):
    whole = (await site.get(f"/api/public/articles/zh-TW/{article.slug}")).json()
    await _members_only(newsroom_room, article)
    reader_id = await _sign_in(site, mailbox)
    await _buy(db_session, article.company_id, reader_id)
    await db_session.commit()

    body = (await site.get(f"/api/public/articles/zh-TW/{article.slug}")).json()

    assert body["locked"] is False
    assert [b["text"] for b in body["blocks"]] == [b["text"] for b in whole["blocks"]]
    company = await db_session.get(Company, article.company_id)
    me = (await site.get("/api/auth/me", params={"company": company.slug})).json()
    assert me["member_until"] is not None


async def test_the_list_says_which_ones_need_a_membership(site, article, newsroom_room):
    await _members_only(newsroom_room, article)
    listed = (await site.get("/api/public/articles", params={"lang": "zh-TW"})).json()
    mine = [row for row in listed if row["slug"] == article.slug]
    assert mine and mine[0]["access"] == "members"


async def test_the_company_stores_a_reference_not_an_address(db_session, article, site, mailbox):
    """D-018's line, with readers on the other side of it (D-024)."""
    reader_id = await _sign_in(site, mailbox)
    await _buy(db_session, article.company_id, reader_id)

    customer = await db_session.scalar(
        select(Customer).where(Customer.company_id == article.company_id)
    )
    assert customer.external_ref == f"reader:{reader_id}"
    assert "@" not in customer.external_ref
    assert not any("@" in str(value) for value in vars(customer).values() if value is not None)


async def test_an_operator_decides_which_articles_are_for_members(api, site, article):
    """No rule picks this: what is worth paying for is a judgement about readers (D-025)."""
    answer = await api.post(f"/api/articles/{article.id}/access", json={"access": "members"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["access"] == "members"

    body = (await site.get(f"/api/public/articles/zh-TW/{article.slug}")).json()
    assert body["locked"] is True

    await api.post(f"/api/articles/{article.id}/access", json={"access": "free"})
    body = (await site.get(f"/api/public/articles/zh-TW/{article.slug}")).json()
    assert body["locked"] is False


async def test_only_an_operator_may_move_the_paywall(site, article):
    answer = await site.post(f"/api/articles/{article.id}/access", json={"access": "members"})
    assert answer.status_code == 401
