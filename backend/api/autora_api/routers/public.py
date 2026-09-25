"""The public site's API (T-515, D-025): published articles and the reader beacon.

Most articles are free and need no sign-in. A members-only one comes back as its opening and
``locked`` unless the reader's cookie belongs to somebody whose membership is still running —
the rest of the text is never sent to a browser that may not read it. The beacon carries
nothing about the reader either way.

- GET  /api/public/articles?lang=zh-TW[&company=<slug>][&section=ai][&limit=20][&offset=0]:
  newest published first
- GET  /api/public/articles/{lang}/{slug}: one published article (404: not published in lang)
- GET  /api/public/markets: the market strip's figures, closing or delayed (D-048)
- POST /api/analytics/beacon: {article_id, lang, event_type, session_hash} -> 204
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from autora.accounts import SESSION_COOKIE, customer_ref, reader_for
from autora.company import memberships
from autora.domains.newsroom.market_strip import PublicQuote, QuoteBoard, build_board
from autora.domains.newsroom.models import AnalyticsEventType
from autora.domains.newsroom.site import (
    MAX_LIST,
    BeaconRejected,
    PublicArticle,
    PublicArticleSummary,
    published_article,
    published_articles,
    record_beacon,
)
from autora.infra.settings import get_settings
from autora_api.deps import Session

router = APIRouter(tags=["public"])

Section = Literal["holdings", "ai", "tw", "us", "crypto"]
"""The site's sections (``newsroom.sources.SECTIONS``), spelled out for the OpenAPI document."""

SessionCookie = Annotated[str | None, Cookie(alias=SESSION_COOKIE)]

Lang = Annotated[str, Field(pattern=r"^[a-z]{2}(-[A-Z][A-Za-z]{1,3})?$", max_length=10)]


@router.get("/api/public/articles")
async def list_articles(
    session: Session,
    lang: Annotated[str, Query(pattern=r"^[a-z]{2}(-[A-Z][A-Za-z]{1,3})?$", max_length=10)],
    company: Annotated[str | None, Query(max_length=100)] = None,
    section: Annotated[Section | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> list[PublicArticleSummary]:
    """Newest first; ``offset`` pages through them (D-047)."""
    return await published_articles(
        session, lang, company_slug=company, section=section, limit=limit, offset=offset
    )


@router.get("/api/public/articles/{lang}/{slug}")
async def get_article(
    lang: str, slug: str, session: Session, autora_reader: SessionCookie = None
) -> PublicArticle:
    article = await published_article(session, lang, slug)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no published article {slug} in {lang}")
    if not article.locked:
        return article
    reader = await reader_for(session, autora_reader)
    if reader is None:
        return article
    until = await memberships.access_until(
        session, company_id=article.company_id, customer_ref=customer_ref(reader.id)
    )
    if until is None:
        return article
    return await published_article(session, lang, slug, unlocked=True) or article


@lru_cache
def market_board() -> QuoteBoard:
    """One board per process: it is the cache, so every reader is served from the same one."""
    key = get_settings().fred_api_key
    return build_board(fred_api_key=key.get_secret_value() if key else None)


@router.get("/api/public/markets")
async def markets(board: Annotated[QuoteBoard, Depends(market_board)]) -> list[PublicQuote]:
    """The figures under the site's header, in the order shown; one a service never gave is
    left out rather than shown as zero."""
    return await board.quotes()


class Beacon(BaseModel):
    article_id: uuid.UUID
    lang: Lang
    event_type: AnalyticsEventType
    session_hash: str = Field(
        pattern=r"^[0-9a-f]{16,64}$",
        description="A random id the reader's browser makes each day; nothing about the reader.",
    )


@router.post("/api/analytics/beacon", status_code=status.HTTP_204_NO_CONTENT)
async def beacon(body: Beacon, session: Session) -> Response:
    """Count a view or a completed read. A repeat from the same session that day is dropped
    (still 204: the reader's page has nothing to do about it)."""
    try:
        await record_beacon(
            session,
            article_id=body.article_id,
            lang=body.lang,
            event_type=body.event_type,
            session_hash=body.session_hash,
        )
    except BeaconRejected as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from None
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
