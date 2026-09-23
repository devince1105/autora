"""What the public site reads and records (T-515; platform/05 §4, §8; 3d-office/06 §2).

The site shows only what was published: the draft group the publisher marked as published
(``Article.published_group_id``), in the languages it was published in. Readers see the text and
the sources behind it (the evidence the cited claims quote: title, site and link), never the
claims' internals, drafts or anything unpublished.

Some articles are for members (D-025). The paywall lives here, in what is returned: a member's
request gets the whole article, anybody else gets the opening (``PREVIEW_BLOCKS``) and
``locked``. The site never receives the rest of the text and then hides it — a reader with the
developer tools open would find it there. Who counts as a member is decided above this module:
this one is handed a yes or a no.

Beacons (``record_beacon``) count readers without knowing who they are: the browser sends a
random id it makes each day (``session_hash``); no IP address or anything else about the reader is
stored. One count per session, article, language, kind and day: repeats are dropped.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Company
from autora.domains.newsroom.models import (
    AnalyticsEvent,
    AnalyticsEventType,
    Article,
    ArticleAccess,
    ArticleState,
    ArticleVersion,
    ClaimEvidence,
    Evidence,
    SupportType,
)
from autora.domains.newsroom.publisher import article_path
from autora.infra.ids import uuid7

MAX_LIST = 50
PREVIEW_BLOCKS = 2
"""At most how many blocks of a members-only article anybody may read."""


def preview(body: list[dict]) -> list[dict]:
    """The opening of a locked article — always strictly less than all of it.

    A short article would otherwise be given away whole: two blocks of a two-block piece is
    the piece. What stays behind is at least the last block, whatever the length."""
    return body[: min(PREVIEW_BLOCKS, max(0, len(body) - 1))]


class PublicBlock(BaseModel):
    type: str
    text: str


class PublicSource(BaseModel):
    title: str
    site: str
    url: str


class PublicArticleSummary(BaseModel):
    article_id: uuid.UUID
    lang: str
    slug: str
    path: str
    title: str
    summary: str | None
    published_at: datetime
    access: str = ArticleAccess.FREE.value
    """``free`` or ``members`` (D-025). On a list, this is what draws the badge."""


class PublicArticle(PublicArticleSummary):
    locked: bool = False
    """True when ``blocks`` is only the opening, because this one is for members."""
    blocks: list[PublicBlock]
    sources: list[PublicSource]
    """The evidence the article's claims quote, once per page, in order of first use."""
    langs: dict[str, str]
    """Every published language -> its page, for the language switch and hreflang."""
    company: str
    company_id: uuid.UUID
    """Whose article it is — the site asks that company whether this reader is a member."""
    company_slug: str
    """The same company, as the public API names one. The paywall asks what a year costs here."""


def _summary(article: Article, version: ArticleVersion) -> PublicArticleSummary:
    assert article.published_at is not None
    return PublicArticleSummary(
        access=article.access,
        article_id=article.id,
        lang=version.lang,
        slug=article.slug,
        path=article_path(version.lang, article.slug),
        title=version.title,
        summary=version.summary,
        published_at=article.published_at,
    )


def _published(lang: str):
    """Published articles with their version in ``lang`` (only if published in that language)."""
    return (
        select(Article, ArticleVersion)
        .join(ArticleVersion, ArticleVersion.draft_group_id == Article.published_group_id)
        .where(
            Article.state == ArticleState.PUBLISHED,
            ArticleVersion.lang == lang,
            Article.published_langs.any(lang),
        )
    )


async def published_article(
    session: AsyncSession, lang: str, slug: str, *, unlocked: bool = False
) -> PublicArticle | None:
    """One published article. ``unlocked`` says the reader is a member; without it, a
    members-only article comes back as its opening and ``locked``."""
    row = (await session.execute(_published(lang).where(Article.slug == slug))).first()
    if row is None:
        return None
    article, version = row
    company = await session.get(Company, article.company_id)
    cited: list[uuid.UUID] = []
    for block in version.body:
        for claim_id in block.get("claim_ids", []):
            if uuid.UUID(claim_id) not in cited:
                cited.append(uuid.UUID(claim_id))
    evidence = (
        await session.execute(
            select(ClaimEvidence.claim_id, Evidence.url, Evidence.title)
            .join(Evidence, Evidence.id == ClaimEvidence.evidence_id)
            .where(
                ClaimEvidence.claim_id.in_(cited),
                ClaimEvidence.support_type == SupportType.SUPPORTS,
            )
        )
    ).all()
    by_claim: dict[uuid.UUID, list[tuple[str, str | None]]] = {}
    for claim_id, url, title in evidence:
        by_claim.setdefault(claim_id, []).append((url, title))
    sources: dict[str, PublicSource] = {}
    for claim_id in cited:
        for url, title in sorted(by_claim.get(claim_id, [])):
            if url not in sources:
                site = urlsplit(url).hostname or url
                sources[url] = PublicSource(title=title or site, site=site, url=url)
    locked = article.access == ArticleAccess.MEMBERS.value and not unlocked
    body = preview(version.body) if locked else version.body
    return PublicArticle(
        **_summary(article, version).model_dump(),
        locked=locked,
        blocks=[PublicBlock(type=b["type"], text=b["text"]) for b in body],
        sources=[] if locked else list(sources.values()),
        langs={lang_: article_path(lang_, article.slug) for lang_ in article.published_langs},
        company=company.name if company else "",
        company_id=article.company_id,
        company_slug=company.slug if company else "",
    )


async def published_articles(
    session: AsyncSession, lang: str, *, company_slug: str | None = None, limit: int = 20
) -> list[PublicArticleSummary]:
    query = _published(lang).order_by(Article.published_at.desc(), Article.id.desc())
    if company_slug is not None:
        query = query.join(Company, Company.id == Article.company_id).where(
            Company.slug == company_slug
        )
    rows = (await session.execute(query.limit(min(max(limit, 1), MAX_LIST)))).all()
    return [_summary(article, version) for article, version in rows]


class BeaconRejected(Exception):
    """The beacon is not about a published article in that language."""


@dataclass(frozen=True)
class BeaconOutcome:
    recorded: bool
    """False when this session had already counted (a repeat is dropped)."""


async def record_beacon(
    session: AsyncSession,
    *,
    article_id: uuid.UUID,
    lang: str,
    event_type: AnalyticsEventType,
    session_hash: str,
    now: datetime | None = None,
) -> BeaconOutcome:
    article = await session.get(Article, article_id)
    if (
        article is None
        or article.state != ArticleState.PUBLISHED
        or lang not in article.published_langs
    ):
        raise BeaconRejected(f"no published article {article_id} in {lang}")
    day: date = (now or datetime.now(UTC)).astimezone(UTC).date()
    result = await session.execute(
        insert(AnalyticsEvent)
        .values(
            id=uuid7(),
            company_id=article.company_id,
            article_id=article.id,
            lang=lang,
            event_type=event_type.value,
            session_hash=session_hash,
            day=day,
        )
        .on_conflict_do_nothing()
        .returning(AnalyticsEvent.id)
    )
    return BeaconOutcome(recorded=result.scalar_one_or_none() is not None)
