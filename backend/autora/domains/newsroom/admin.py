"""What the newsroom's admin pages read (T-517, 3d-office/06 §5): stories with their sources,
evidence and claims; articles with every version, fact-check, distribution and reader numbers;
sources. Read-only views over the domain's tables, for operators (the public site reads
``site.py``, which shows only what was published).

Every claim comes with its quotes and, for each quote, a little of the evidence text around it,
so a reader of the admin page can see that the quote is really there (AC-7).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import AgentRun, Task, WorkflowRun
from autora.domains.newsroom.models import (
    AnalyticsDaily,
    Article,
    ArticleVersion,
    Claim,
    ClaimEvidence,
    Distribution,
    Evidence,
    FactCheckReport,
    Source,
    SourceItem,
    Story,
    StoryItem,
)
from autora.domains.newsroom.publisher import article_path

CONTEXT = 80
"""Characters of evidence text shown before and after a quote."""
MAX_LIST = 100


class ArticleRef(BaseModel):
    id: uuid.UUID
    state: str
    slug: str
    title: str


class StorySummary(BaseModel):
    id: uuid.UUID
    title: str
    state: str
    score: Decimal
    items: int
    sources: int
    evidence: int
    claims: int
    first_seen_at: datetime
    article: ArticleRef | None


class Lead(BaseModel):
    title: str
    url: str
    source: str
    published_at: datetime | None


class EvidenceView(BaseModel):
    id: uuid.UUID
    title: str | None
    url: str
    site: str
    retrieved_at: datetime
    chars: int
    truncated: bool
    trust_level: Decimal | None
    run_id: uuid.UUID | None


class QuoteView(BaseModel):
    evidence_id: uuid.UUID
    evidence_title: str | None
    url: str
    support_type: str
    quote: str
    before: str
    after: str
    """A little of the evidence text around the quote, to see it in place."""


class ClaimView(BaseModel):
    id: uuid.UUID
    text: str
    claim_type: str
    status: str
    quotes: list[QuoteView]


class StoryDetail(StorySummary):
    company_id: uuid.UUID
    summary: str | None
    angle: str | None
    seed: dict[str, Any]
    leads: list[Lead]
    evidence_list: list[EvidenceView]
    claim_list: list[ClaimView]
    workflow_run_ids: list[uuid.UUID]


class ArticleSummary(BaseModel):
    id: uuid.UUID
    story_id: uuid.UUID
    title: str
    state: str
    slug: str
    version: int | None
    langs: list[str]
    revision_count: int
    published_at: datetime | None
    updated_at: datetime
    views: int
    listed: bool = True
    """On the site (D-045): a published version shows unless it was taken down."""
    revised_at: datetime | None = None
    """When a changed version of the published article went up (D-045)."""


class BlockView(BaseModel):
    type: str
    text: str
    claim_ids: list[uuid.UUID]


class LanguageView(BaseModel):
    version_id: uuid.UUID
    title: str
    summary: str | None
    blocks: list[BlockView]


class VersionView(BaseModel):
    version: int
    draft_group_id: uuid.UUID
    langs: list[str]
    created_at: datetime
    change_summary: str | None
    current: bool
    published: bool


class FactCheckView(BaseModel):
    id: uuid.UUID
    version: int | None
    passed: bool
    created_at: datetime
    checked: int
    failed: int
    results: list[dict[str, Any]]


class DistributionView(BaseModel):
    id: uuid.UUID
    channel: str
    status: str
    external_ref: str | None
    content: dict[str, Any]
    """The distribution's copy: per language, the site's {url, title} or a post's text."""
    created_at: datetime


class DailyView(BaseModel):
    day: date
    lang: str
    views: int
    uniques: int
    read_complete: int


class ArticleDetail(ArticleSummary):
    story_title: str
    company_id: uuid.UUID
    primary_lang: str
    published_langs: list[str]
    public_urls: dict[str, str]
    versions: list[VersionView]
    shown: int | None
    """The version shown in ``languages`` (the latest unless another was asked for)."""
    languages: dict[str, LanguageView]
    claims: list[ClaimView]
    fact_checks: list[FactCheckView]
    distributions: list[DistributionView]
    analytics: list[DailyView]
    workflow_run_ids: list[uuid.UUID]


class SourceView(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    url: str | None
    config: dict[str, Any]
    trust_level: Decimal
    language: str | None
    status: str
    poll_interval_seconds: int
    last_polled_at: datetime | None
    next_poll_at: datetime
    items: int


def _site(url: str) -> str:
    return urlsplit(url).hostname or url


# --- stories ----------------------------------------------------------------------------------


async def _story_counts(
    session: AsyncSession, story_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    claims = dict(
        (
            await session.execute(
                select(Claim.story_id, func.count())
                .where(Claim.story_id.in_(story_ids))
                .group_by(Claim.story_id)
            )
        ).all()
    )
    evidence = dict(
        (
            await session.execute(
                select(Claim.story_id, func.count(func.distinct(ClaimEvidence.evidence_id)))
                .join(ClaimEvidence, ClaimEvidence.claim_id == Claim.id)
                .where(Claim.story_id.in_(story_ids))
                .group_by(Claim.story_id)
            )
        ).all()
    )
    return claims, evidence


def _story_summary(
    story: Story, article: Article | None, claims: int, evidence: int
) -> dict[str, Any]:
    return {
        "id": story.id,
        "title": story.title,
        "state": story.state,
        "score": story.score,
        "items": story.items_count,
        "sources": story.sources_count,
        "evidence": evidence,
        "claims": claims,
        "first_seen_at": story.first_seen_at,
        "article": ArticleRef(id=article.id, state=article.state, slug=article.slug,
                              title=article.title) if article else None,
    }  # fmt: skip


async def list_stories(
    session: AsyncSession, company_id: uuid.UUID, *, state: str | None = None, limit: int = 50
) -> list[StorySummary]:
    query = (
        select(Story, Article)
        .outerjoin(Article, Article.story_id == Story.id)
        .where(Story.company_id == company_id)
    )
    if state:
        query = query.where(Story.state == state)
    rows = (
        await session.execute(
            query.order_by(Story.last_item_at.desc(), Story.id.desc()).limit(min(limit, MAX_LIST))
        )
    ).all()
    claims, evidence = await _story_counts(session, [s.id for s, _ in rows])
    return [
        StorySummary(**_story_summary(s, a, claims.get(s.id, 0), evidence.get(s.id, 0)))
        for s, a in rows
    ]


async def _claims(session: AsyncSession, claim_filter) -> list[ClaimView]:
    claims = (await session.scalars(select(Claim).where(claim_filter).order_by(Claim.id))).all()
    if not claims:
        return []
    rows = (
        await session.execute(
            select(ClaimEvidence, Evidence.title, Evidence.url, Evidence.extracted_text)
            .join(Evidence, Evidence.id == ClaimEvidence.evidence_id)
            .where(ClaimEvidence.claim_id.in_([c.id for c in claims]))
            .order_by(ClaimEvidence.id)
        )
    ).all()
    quotes: dict[uuid.UUID, list[QuoteView]] = {c.id: [] for c in claims}
    for link, title, url, text in rows:
        quotes[link.claim_id].append(
            QuoteView(
                evidence_id=link.evidence_id,
                evidence_title=title,
                url=url,
                support_type=link.support_type,
                quote=text[link.quote_start : link.quote_end],
                before=text[max(0, link.quote_start - CONTEXT) : link.quote_start],
                after=text[link.quote_end : link.quote_end + CONTEXT],
            )
        )
    return [
        ClaimView(
            id=c.id, text=c.text, claim_type=c.claim_type, status=c.status, quotes=quotes[c.id]
        )
        for c in claims
    ]


async def _workflow_runs(session: AsyncSession, story_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await session.scalars(
                select(WorkflowRun.id)
                .where(WorkflowRun.params["story_id"].astext == str(story_id))
                .order_by(WorkflowRun.created_at)
            )
        ).all()
    )


async def story_detail(session: AsyncSession, story_id: uuid.UUID) -> StoryDetail | None:
    story = await session.get(Story, story_id)
    if story is None:
        return None
    article = await session.scalar(select(Article).where(Article.story_id == story.id))
    leads = (
        await session.execute(
            select(SourceItem, Source.name)
            .join(StoryItem, StoryItem.source_item_id == SourceItem.id)
            .join(Source, Source.id == SourceItem.source_id)
            .where(StoryItem.story_id == story.id)
            .order_by(SourceItem.published_at.desc().nulls_last())
        )
    ).all()
    claim_list = await _claims(session, Claim.story_id == story.id)
    # evidence of the story: what its claims quote, and what its research tasks captured
    evidence_ids = {q.evidence_id for c in claim_list for q in c.quotes}
    runs = await _workflow_runs(session, story.id)
    run_ids = (
        list(
            (
                await session.scalars(
                    select(AgentRun.id)
                    .join(Task, Task.id == AgentRun.task_id)
                    .where(Task.workflow_run_id.in_(runs))
                )
            ).all()
        )
        if runs
        else []
    )
    evidence = (
        await session.execute(
            select(Evidence, Source.trust_level)
            .outerjoin(Source, Source.id == Evidence.source_id)
            .where(
                Evidence.company_id == story.company_id,
                or_(Evidence.id.in_(evidence_ids), Evidence.run_id.in_(run_ids)),
            )
            .order_by(Evidence.retrieved_at, Evidence.id)
        )
    ).all()
    return StoryDetail(
        **_story_summary(story, article, len(claim_list), len(evidence_ids)),
        company_id=story.company_id,
        summary=story.summary,
        angle=story.angle,
        seed=story.seed or {},
        leads=[
            Lead(title=item.title, url=item.url, source=name, published_at=item.published_at)
            for item, name in leads
        ],
        evidence_list=[
            EvidenceView(
                id=e.id,
                title=e.title,
                url=e.url,
                site=_site(e.url),
                retrieved_at=e.retrieved_at,
                chars=len(e.extracted_text),
                truncated=e.truncated,
                trust_level=trust,
                run_id=e.run_id,
            )
            for e, trust in evidence
        ],
        claim_list=claim_list,
        workflow_run_ids=runs,
    )


# --- articles ---------------------------------------------------------------------------------


async def list_articles(
    session: AsyncSession, company_id: uuid.UUID, *, limit: int = 50
) -> list[ArticleSummary]:
    articles = (
        await session.scalars(
            select(Article)
            .where(Article.company_id == company_id)
            .order_by(Article.updated_at.desc(), Article.id.desc())
            .limit(min(limit, MAX_LIST))
        )
    ).all()
    ids = [a.id for a in articles]
    current = {
        row.draft_group_id: row
        for row in (
            await session.execute(
                select(
                    ArticleVersion.draft_group_id,
                    func.max(ArticleVersion.version).label("version"),
                    func.array_agg(ArticleVersion.lang).label("langs"),
                )
                .where(ArticleVersion.article_id.in_(ids))
                .group_by(ArticleVersion.draft_group_id)
            )
        ).all()
    }
    views = dict(
        (
            await session.execute(
                select(AnalyticsDaily.article_id, func.sum(AnalyticsDaily.views))
                .where(AnalyticsDaily.article_id.in_(ids))
                .group_by(AnalyticsDaily.article_id)
            )
        ).all()
    )
    out = []
    for a in articles:
        group = current.get(a.current_draft_group_id)
        out.append(
            ArticleSummary(
                id=a.id,
                story_id=a.story_id,
                title=a.title,
                state=a.state,
                slug=a.slug,
                version=group.version if group else None,
                langs=sorted(group.langs) if group else [],
                revision_count=a.revision_count,
                published_at=a.published_at,
                listed=a.listed,
                revised_at=a.revised_at,
                updated_at=a.updated_at,
                views=int(views.get(a.id) or 0),
            )
        )
    return out


async def article_detail(
    session: AsyncSession, article_id: uuid.UUID, *, version: int | None = None
) -> ArticleDetail | None:
    article = await session.get(Article, article_id)
    if article is None:
        return None
    story = await session.get(Story, article.story_id)
    rows = (
        await session.scalars(
            select(ArticleVersion)
            .where(ArticleVersion.article_id == article.id)
            .order_by(ArticleVersion.version, ArticleVersion.id)
        )
    ).all()
    groups: dict[uuid.UUID, list[ArticleVersion]] = {}
    for row in rows:
        groups.setdefault(row.draft_group_id, []).append(row)
    versions = [
        VersionView(
            version=g[0].version,
            draft_group_id=group_id,
            langs=[r.lang for r in g],
            created_at=g[0].created_at,
            change_summary=g[0].change_summary,
            current=group_id == article.current_draft_group_id,
            published=group_id == article.published_group_id,
        )
        for group_id, g in groups.items()
    ]
    number_of = {v.draft_group_id: v.version for v in versions}
    shown = (
        version if version in number_of.values() else (versions[-1].version if versions else None)
    )
    shown_rows = [r for r in rows if r.version == shown]
    cited = {uuid.UUID(c) for r in shown_rows for b in r.body for c in b.get("claim_ids", [])}
    reports = (
        await session.scalars(
            select(FactCheckReport)
            .where(FactCheckReport.article_id == article.id)
            .order_by(FactCheckReport.created_at.desc(), FactCheckReport.id.desc())
        )
    ).all()
    distributions = (
        await session.scalars(
            select(Distribution)
            .where(Distribution.article_id == article.id)
            .order_by(Distribution.created_at)
        )
    ).all()
    daily = (
        await session.scalars(
            select(AnalyticsDaily)
            .where(AnalyticsDaily.article_id == article.id)
            .order_by(AnalyticsDaily.day.desc(), AnalyticsDaily.lang)
        )
    ).all()
    current = groups.get(article.current_draft_group_id) if article.current_draft_group_id else None
    return ArticleDetail(
        id=article.id,
        story_id=article.story_id,
        title=article.title,
        state=article.state,
        slug=article.slug,
        version=current[0].version if current else None,
        langs=[r.lang for r in current] if current else [],
        revision_count=article.revision_count,
        published_at=article.published_at,
        updated_at=article.updated_at,
        listed=article.listed,
        revised_at=article.revised_at,
        views=sum(d.views for d in daily),
        story_title=story.title if story else "",
        company_id=article.company_id,
        primary_lang=article.primary_lang,
        published_langs=list(article.published_langs),
        public_urls={lang: article_path(lang, article.slug) for lang in article.published_langs},
        versions=versions,
        shown=shown,
        languages={
            r.lang: LanguageView(
                version_id=r.id,
                title=r.title,
                summary=r.summary,
                blocks=[
                    BlockView(type=b["type"], text=b["text"], claim_ids=b.get("claim_ids", []))
                    for b in r.body
                ],
            )
            for r in shown_rows
        },
        claims=await _claims(session, Claim.id.in_(cited)) if cited else [],
        fact_checks=[
            FactCheckView(
                id=r.id,
                version=number_of.get(r.draft_group_id),
                passed=r.passed,
                created_at=r.created_at,
                checked=sum(1 for x in r.results if "claim_id" in x),
                failed=sum(1 for x in r.results if x.get("verdict") == "fail"),
                results=r.results,
            )
            for r in reports
        ],
        distributions=[
            DistributionView(
                id=d.id,
                channel=d.channel,
                status=d.status,
                external_ref=d.external_ref,
                content=d.copy,
                created_at=d.created_at,
            )
            for d in distributions
        ],
        analytics=[
            DailyView(
                day=d.day,
                lang=d.lang,
                views=d.views,
                uniques=d.uniques,
                read_complete=d.read_complete,
            )
            for d in daily
        ],
        workflow_run_ids=await _workflow_runs(session, article.story_id),
    )


# --- sources ----------------------------------------------------------------------------------


async def list_sources(session: AsyncSession, company_id: uuid.UUID) -> list[SourceView]:
    rows = (
        await session.execute(
            select(Source, func.count(SourceItem.id))
            .outerjoin(SourceItem, SourceItem.source_id == Source.id)
            .where(Source.company_id == company_id)
            .group_by(Source.id)
            .order_by(Source.created_at, Source.id)
        )
    ).all()
    return [source_view(s, items) for s, items in rows]


def source_view(source: Source, items: int = 0) -> SourceView:
    return SourceView(
        id=source.id,
        name=source.name,
        kind=source.kind,
        url=source.url,
        config=source.config,
        trust_level=source.trust_level,
        language=source.language,
        status=source.status,
        poll_interval_seconds=source.poll_interval_seconds,
        last_polled_at=source.last_polled_at,
        next_poll_at=source.next_poll_at,
        items=items,
    )
