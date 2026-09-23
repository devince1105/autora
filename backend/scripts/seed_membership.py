"""Put a membership on sale, so the paywall has something to sell (T-702, D-024/D-025).

    python backend/scripts/seed_membership.py                       # NT$360 a year, demo company
    python backend/scripts/seed_membership.py --amount 480          # a different price
    python backend/scripts/seed_membership.py --company other-slug

The site's paywall asks ``/api/checkout/offer`` for what a year costs, and a company with no
membership product has nothing to answer — the button then says "not open yet", which is true
but not much use for trying the flow. This makes the product and the price.

Idempotent in the way that matters: a company that already has a live membership product keeps
it, and asking for a price it is already on sale at changes nothing. Asking for a *different*
price retires the old one and adds the new, which is how a price is raised without touching what
anybody already bought (memberships.offer).
"""

import argparse
import asyncio
import json
from decimal import Decimal

from sqlalchemy import select

from autora.company import memberships
from autora.company.organization import add_business_unit, add_product, business_unit_by_key
from autora.db.models import BusinessUnitState, Company, PriceInterval, Product, ProductState
from autora.db.session import dispose_engine, get_sessionmaker
from autora.domains.newsroom import organization as newsroom_org
from autora.runtime.actor import Actor

ACTOR = Actor.human("seed_membership")
DEFAULT_SLUG = "newsroom-demo"
PRODUCT_NAME = "會員"


async def seed(slug: str, amount: Decimal) -> dict[str, object]:
    async with get_sessionmaker()() as session:
        company = await session.scalar(select(Company).where(Company.slug == slug))
        if company is None:
            raise SystemExit(f"no company {slug!r}; run seed_newsroom.py first")

        unit = await business_unit_by_key(session, company.id, newsroom_org.BUSINESS_UNIT)
        if unit is None:
            unit = await add_business_unit(
                session, company_id=company.id, key=newsroom_org.BUSINESS_UNIT,
                name="新聞室", actor=ACTOR, state=BusinessUnitState.ACTIVE,
            )  # fmt: skip

        product = await session.scalar(
            select(Product).where(
                Product.company_id == company.id, Product.key == memberships.PRODUCT_KEY
            )
        )
        if product is None:
            product = await add_product(
                session, company_id=company.id, key=memberships.PRODUCT_KEY, name=PRODUCT_NAME,
                business_unit_id=unit.id, actor=ACTOR, state=ProductState.LIVE,
                description="一次付款，閱讀全部會員專屬報導一年。",
            )  # fmt: skip

        current = await memberships.offer(session, company.id)
        if current is not None and current.amount == amount:
            price, changed = current, False
        else:
            if current is not None:
                # what was bought at the old price stays bought; only the shop window changes
                await memberships.retire_price(session, current)
            price = await memberships.add_price(
                session, product, amount=amount, interval=PriceInterval.YEAR
            )
            changed = True
        await session.commit()
        return {
            "company": slug,
            "product": product.key,
            "price_id": str(price.id),
            "amount": str(price.amount),
            "currency": price.currency,
            "interval": price.interval,
            "changed": changed,
        }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--company", default=DEFAULT_SLUG, help="the company's slug")
    parser.add_argument("--amount", default="360", help="what a year costs, in the base currency")
    args = parser.parse_args()
    try:
        print(
            json.dumps(await seed(args.company, Decimal(args.amount)), indent=2, ensure_ascii=False)
        )
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
