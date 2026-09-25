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

The site is in sections (D-047): big investors' filings, public figures' holdings (D-050), AI
and tech, Taiwan stocks, US stocks, crypto. An article's section is not stored — it is its
story's, which is the section most of the story's items' sources name (``config.section``).
Retagging a source moves what is already written.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from pydantic import BaseModel
from sqlalchemy import Text, cast, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Company
from autora.domains.newsroom.models import (
    AnalyticsEvent,
    AnalyticsEventType,
    Article,
    ArticleAccess,
    ArticleVersion,
    ClaimEvidence,
    Evidence,
    Source,
    SourceItem,
    StoryItem,
    SupportType,
)
from autora.domains.newsroom.publisher import article_path
from autora.domains.newsroom.sources import SECTION, SECTIONS
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
    revised_at: datetime | None = None
    """When a changed version went up (D-045); the page says so, as a correction should."""
    access: str = ArticleAccess.FREE.value
    """``free`` or ``members`` (D-025). On a list, this is what draws the badge."""
    section: str | None = None
    """One of ``SECTIONS`` (D-047), or None when none of its story's sources names one."""


class PublicNeighbour(BaseModel):
    """The next article along, newer or older, in the same language and company."""

    title: str
    path: str


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
    newer: PublicNeighbour | None = None
    older: PublicNeighbour | None = None


def _summary(
    article: Article, version: ArticleVersion, section: str | None
) -> PublicArticleSummary:
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
        revised_at=article.revised_at,
        section=section,
    )


def _section():
    """The section most of the article's story's sources name; ties go to the first in
    alphabetical order, so an article does not change section from one read to the next."""
    named = Source.config[SECTION].astext
    return (
        select(named)
        .select_from(StoryItem)
        .join(SourceItem, SourceItem.id == StoryItem.source_item_id)
        .join(Source, Source.id == SourceItem.source_id)
        .where(StoryItem.story_id == Article.story_id, named.in_(SECTIONS))
        .group_by(named)
        .order_by(func.count().desc(), named)
        .limit(1)
        .correlate(Article)
        .scalar_subquery()
    )


def _published(lang: str):
    """Published articles with their version in ``lang`` (only if published in that language).

    On the site is "has a published version and is listed" (D-045), not "is PUBLISHED": an
    article being revised keeps showing what was published until the new version is."""
    return (
        select(Article, ArticleVersion, _section())
        .join(ArticleVersion, ArticleVersion.draft_group_id == Article.published_group_id)
        .where(
            Article.published_group_id.is_not(None),
            Article.listed.is_(True),
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
    article, version, section = row
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
    newer, older = await _neighbours(session, lang, article)
    return PublicArticle(
        **_summary(article, version, section).model_dump(),
        locked=locked,
        blocks=[PublicBlock(type=b["type"], text=b["text"]) for b in body],
        sources=[] if locked else list(sources.values()),
        langs={lang_: article_path(lang_, article.slug) for lang_ in article.published_langs},
        company=company.name if company else "",
        company_id=article.company_id,
        company_slug=company.slug if company else "",
        newer=newer,
        older=older,
    )


async def _neighbours(
    session: AsyncSession, lang: str, article: Article
) -> tuple[PublicNeighbour | None, PublicNeighbour | None]:
    """The article published just after this one and the one just before, as the list orders
    them (newest first), among the same company's articles on the site in this language."""
    here = tuple_(Article.published_at, Article.id)
    at = tuple_(article.published_at, article.id)
    mine = _published(lang).where(Article.company_id == article.company_id)
    out: list[PublicNeighbour | None] = []
    for query in (
        mine.where(here > at).order_by(Article.published_at.asc(), Article.id.asc()),
        mine.where(here < at).order_by(Article.published_at.desc(), Article.id.desc()),
    ):
        row = (await session.execute(query.limit(1))).first()
        out.append(
            None
            if row is None
            else PublicNeighbour(title=row[1].title, path=article_path(lang, row[0].slug))
        )
    return out[0], out[1]


async def published_articles(
    session: AsyncSession,
    lang: str,
    *,
    company_slug: str | None = None,
    section: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[PublicArticleSummary]:
    """Newest first. ``offset`` pages through them; a page that comes back shorter than
    ``limit`` is the last."""
    query = _published(lang).order_by(Article.published_at.desc(), Article.id.desc())
    if company_slug is not None:
        query = query.join(Company, Company.id == Article.company_id).where(
            Company.slug == company_slug
        )
    if section is not None:
        query = query.where(_section() == section)
    query = query.limit(min(max(limit, 1), MAX_LIST)).offset(max(offset, 0))
    rows = (await session.execute(query)).all()
    return [_summary(article, version, named) for article, version, named in rows]


async def published_articles_mentioning(
    session: AsyncSession,
    lang: str,
    terms: tuple[str, ...],
    *,
    company_slug: str | None = None,
    limit: int = 10,
) -> list[PublicArticleSummary]:
    """The newest published articles in ``lang`` whose title, summary or text names any of
    ``terms`` (D-049, a stock page's "our coverage"). A Latin term matches as a whole word and
    in its own case — ``MU`` is not in "MUST", ``Meta`` is not "metadata" — others anywhere."""
    text_of = func.concat_ws(
        " ", ArticleVersion.title, ArticleVersion.summary, cast(ArticleVersion.body, Text)
    )
    matches = []
    for term in terms:
        if term.isascii():
            matches.append(text_of.op("~")(rf"\m{re.escape(term)}\M"))
        else:
            matches.append(text_of.contains(term, autoescape=True))
    if not matches:
        return []
    query = (
        _published(lang)
        .where(or_(*matches))
        .order_by(Article.published_at.desc(), Article.id.desc())
    )
    if company_slug is not None:
        query = query.join(Company, Company.id == Article.company_id).where(
            Company.slug == company_slug
        )
    rows = (await session.execute(query.limit(min(max(limit, 1), MAX_LIST)))).all()
    return [_summary(article, version, named) for article, version, named in rows]


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
        or article.published_group_id is None
        or not article.listed
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
