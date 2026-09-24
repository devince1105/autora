"""The newsroom's admin API (T-517): stories, articles and sources for the operator's pages.

- GET  /api/companies/{id}/stories[?state=]      stories, newest activity first
- GET  /api/stories/{id}                          a story: leads, evidence, claims (with quotes)
- POST /api/stories/{id}/start                    select it (if new) and start its workflow
- GET  /api/companies/{id}/articles               articles, last changed first
- GET  /api/articles/{id}[?version=]              an article: versions, a version's text in every
                                                  language, its claims, fact-checks,
                                                  distributions, readers
- POST /api/articles/{id}/unpublish               take a published article off the site (D-044)
- POST /api/articles/{id}/republish               put it back
- GET  /api/companies/{id}/sources                sources with how many items each brought
- POST /api/companies/{id}/sources                add a source (starts the newsroom schedules)
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from autora.app import build_embedder
from autora.company.workflows import StartWorkflowError, WorkflowNotAllowed
from autora.db.models import Company, Project, ProjectState
from autora.domains.newsroom import admin
from autora.domains.newsroom.models import Article, ArticleAccess, SourceKind, Story, StoryState
from autora.domains.newsroom.publisher import (
    NotAllowed,
    PublishError,
    republish_article,
    unpublish_article,
)
from autora.domains.newsroom.sources import SourceConfigError, add_source
from autora.domains.newsroom.stories import StoryDesk, StoryError
from autora.domains.newsroom.workflow import start_story
from autora.runtime.fsm import IllegalTransition
from autora_api.deps import Operator, RuntimeDep, Session

router = APIRouter(tags=["newsroom"])


async def _company(session: Session, company_id: uuid.UUID) -> Company:
    company = await session.get(Company, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"company {company_id} not found")
    return company


@router.get("/api/companies/{company_id}/stories")
async def list_stories(
    company_id: uuid.UUID,
    session: Session,
    _: Operator,
    state: Annotated[StoryState | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=admin.MAX_LIST)] = 50,
) -> list[admin.StorySummary]:
    await _company(session, company_id)
    return await admin.list_stories(
        session, company_id, state=state.value if state else None, limit=limit
    )


@router.get("/api/stories/{story_id}")
async def get_story(story_id: uuid.UUID, session: Session, _: Operator) -> admin.StoryDetail:
    story = await admin.story_detail(session, story_id)
    if story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"story {story_id} not found")
    return story


class StartStory(BaseModel):
    project_id: uuid.UUID | None = Field(
        default=None, description="Default: the story's project, else the first active one."
    )


class StartedStory(BaseModel):
    story_id: uuid.UUID
    workflow_run_id: uuid.UUID


@router.post("/api/stories/{story_id}/start", status_code=status.HTTP_201_CREATED)
async def start(
    story_id: uuid.UUID,
    body: StartStory,
    session: Session,
    operator: Operator,
    runtime: RuntimeDep,
) -> StartedStory:
    """Select the story (if it was only discovered) and start its workflow."""
    story = await session.get(Story, story_id, with_for_update=True)
    if story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"story {story_id} not found")
    project_id = (
        body.project_id
        or story.project_id
        or await session.scalar(
            select(Project.id)
            .where(Project.company_id == story.company_id, Project.state == ProjectState.ACTIVE)
            .order_by(Project.created_at)
            .limit(1)
        )
    )
    if project_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "the company has no active project")
    try:
        if story.state == StoryState.DISCOVERED:
            desk = StoryDesk(build_embedder(None))
            await desk.select(session, story, project_id=project_id, actor=operator)
        run = await start_story(
            session,
            policy=runtime.policy,
            workflows=runtime.workflows,
            story=story,
            project_id=project_id,
            actor=operator,
        )
    except WorkflowNotAllowed as refused:
        raise HTTPException(status.HTTP_403_FORBIDDEN, refused.reason) from None
    except (StartWorkflowError, StoryError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    await session.commit()
    return StartedStory(story_id=story.id, workflow_run_id=run.id)


@router.get("/api/companies/{company_id}/articles")
async def list_articles(
    company_id: uuid.UUID,
    session: Session,
    _: Operator,
    limit: Annotated[int, Query(ge=1, le=admin.MAX_LIST)] = 50,
) -> list[admin.ArticleSummary]:
    await _company(session, company_id)
    return await admin.list_articles(session, company_id, limit=limit)


@router.get("/api/articles/{article_id}")
async def get_article(
    article_id: uuid.UUID,
    session: Session,
    _: Operator,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> admin.ArticleDetail:
    article = await admin.article_detail(session, article_id, version=version)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"article {article_id} not found")
    return article


class ArticleAccessBody(BaseModel):
    access: ArticleAccess
    """``free`` or ``members``: who may read the whole thing (D-025)."""


@router.post("/api/articles/{article_id}/access")
async def set_article_access(
    article_id: uuid.UUID, body: ArticleAccessBody, session: Session, _: Operator
) -> dict[str, str]:
    """Put an article behind the paywall, or take it out. A person's decision for now: what is
    worth paying for is a judgement about the reader, and no rule here would be honest."""
    article = await session.get(Article, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"article {article_id} not found")
    article.access = body.access.value
    await session.commit()
    return {"article_id": str(article.id), "access": article.access}


class UnpublishBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    """Why it comes down: kept with the article's history."""


async def _article_of(session: Session, article_id: uuid.UUID) -> Article:
    article = await session.get(Article, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"article {article_id} not found")
    return article


@router.post("/api/articles/{article_id}/unpublish")
async def post_unpublish(
    article_id: uuid.UUID, body: UnpublishBody, session: Session, operator: Operator
) -> dict[str, str]:
    """Take a published article off the site (D-044). It stays, with its history, as ARCHIVED."""
    article = await _article_of(session, article_id)
    try:
        await unpublish_article(
            session, company_id=article.company_id, article_id=article.id,
            actor=operator, reason=body.reason,
        )  # fmt: skip
    except (IllegalTransition, PublishError, NotAllowed) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return {"article_id": str(article.id), "state": article.state}


@router.post("/api/articles/{article_id}/republish")
async def post_republish(
    article_id: uuid.UUID, session: Session, operator: Operator
) -> dict[str, str]:
    """Put an article that was taken down back on the site, as it was (D-044)."""
    article = await _article_of(session, article_id)
    try:
        await republish_article(
            session, company_id=article.company_id, article_id=article.id, actor=operator
        )
    except (IllegalTransition, PublishError, NotAllowed) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return {"article_id": str(article.id), "state": article.state}


@router.get("/api/companies/{company_id}/sources")
async def list_sources(
    company_id: uuid.UUID, session: Session, _: Operator
) -> list[admin.SourceView]:
    await _company(session, company_id)
    return await admin.list_sources(session, company_id)


class NewSource(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: SourceKind
    url: str | None = Field(default=None, max_length=2000)
    config: dict[str, Any] = {}
    trust_level: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    language: str | None = Field(default=None, max_length=10)
    poll_interval_seconds: int = Field(default=3600, ge=300, le=7 * 86400)


@router.post("/api/companies/{company_id}/sources", status_code=status.HTTP_201_CREATED)
async def create_source(
    company_id: uuid.UUID, body: NewSource, session: Session, _: Operator
) -> admin.SourceView:
    await _company(session, company_id)
    try:
        source = await add_source(
            session,
            company_id=company_id,
            name=body.name,
            kind=body.kind,
            url=body.url,
            config=body.config,
            trust_level=body.trust_level,
            language=body.language,
            poll_interval_seconds=body.poll_interval_seconds,
        )
    except SourceConfigError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    await session.commit()
    return admin.source_view(source)
