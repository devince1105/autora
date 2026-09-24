"""Seed the investing newsroom: company, desks, project, real sources, no-advice policy (D-036).

    python backend/scripts/seed_markets.py                        # "Autora 財經", autora-finance
    python backend/scripts/seed_markets.py --name "新的名字"        # rename it (idempotent)

The sources are the real web: the worker only reads them with ``TOOLS_PROFILE=live`` (and SEC
only answers with ``FETCH_CONTACT_EMAIL`` set). Point the public site at it with
``SITE_COMPANY=autora-finance`` and ``NEXT_PUBLIC_SITE_COMPANY=autora-finance``.
"""

import argparse
import asyncio
import json

from autora.db.session import dispose_engine, get_sessionmaker
from autora.domains.newsroom import markets
from autora.runtime.actor import Actor

ACTOR = Actor.human("seed_markets")


async def seed(slug: str, name: str) -> dict[str, object]:
    async with get_sessionmaker()() as session:
        newsroom = await markets.seed_markets(session, actor=ACTOR, slug=slug, name=name)
        await session.commit()
        return {
            "company": newsroom.company.slug,
            "name": newsroom.company.name,
            "sources": len(newsroom.sources),
            "added": newsroom.added,
        }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--company", default=markets.SLUG, help="the company's slug")
    parser.add_argument("--name", default=markets.NAME, help="the name the site shows")
    args = parser.parse_args()
    try:
        print(json.dumps(await seed(args.company, args.name), indent=2, ensure_ascii=False))
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
