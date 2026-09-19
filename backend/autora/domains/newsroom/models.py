"""Newsroom tables (logs/platform/05_NEWSROOM_DOMAIN.md §1, 10_DATABASE_SCHEMA.md).

Phase 5 adds them task by task: sources and polled items (T-501), evidence (T-502) and its
chunks (T-503), stories (T-504), claims and their evidence (T-505), articles and versions (T-508),
fact-check reports (T-510), distributions (T-512).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Text

from autora.db.base import Base, CreatedAtMixin, IdMixin, TimestampMixin, check_in, check_regex
from autora.db.vector import HalfVector

EMBED_DIM = 2048
"""Dimension of stored embeddings (the configured EMBED_MODEL_ID must produce it; T-503)."""


class SourceKind(StrEnum):
    RSS = "rss"
    """``url`` is an RSS or Atom feed."""
    URL_LIST = "url_list"
    """``config.urls``: pages to watch; each becomes an item once."""
    SEARCH_QUERY = "search_query"
    """``config.query`` (and optional ``k``, ``recency_days``) run through web_search's provider."""


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    """Not polled: by a person, or automatically after repeated failures."""


class Source(IdMixin, TimestampMixin, Base):
    """Somewhere stories come from, polled on its own interval."""

    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("company_id", "name"),
        check_in("kind", SourceKind),
        check_in("status", SourceStatus),
        CheckConstraint("trust_level >= 0 AND trust_level <= 1", name="trust_level_range"),
        CheckConstraint("poll_interval_seconds >= 60", name="poll_interval_min"),
        CheckConstraint("kind <> 'rss' OR url IS NOT NULL", name="rss_has_url"),
        Index("ix_sources_due", "next_poll_at", postgresql_where=text("status = 'active'")),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str]
    kind: Mapped[str]
    url: Mapped[str | None]
    config: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    trust_level: Mapped[Decimal] = mapped_column(Numeric(3, 2), server_default="0.5")
    """0-1. Fact-check (T-510): a source below the threshold cannot support a claim alone."""
    language: Mapped[str | None]
    poll_interval_seconds: Mapped[int] = mapped_column(server_default="3600")
    status: Mapped[str] = mapped_column(server_default=SourceStatus.ACTIVE.value)
    next_poll_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    last_polled_at: Mapped[datetime | None]
    consecutive_failures: Mapped[int] = mapped_column(server_default="0")
    last_error: Mapped[str | None]


class SourceItem(IdMixin, CreatedAtMixin, Base):
    """One entry a poll found. The same entry polled again is not a new item: each source keeps
    one row per ``external_id`` and one per ``content_hash`` (canonical URL + title)."""

    __tablename__ = "source_items"
    __table_args__ = (
        UniqueConstraint("source_id", "content_hash"),
        UniqueConstraint("source_id", "external_id"),
        Index("ix_source_items_company_created", "company_id", "created_at"),
        Index("ix_source_items_url", "company_id", "url"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str]
    url: Mapped[str]
    """Canonical (tracking parameters and fragments removed)."""
    title: Mapped[str]
    summary: Mapped[str | None]
    published_at: Mapped[datetime | None]
    content_hash: Mapped[str]


class Evidence(IdMixin, CreatedAtMixin, Base):
    """A page as it was when fetched (T-502): the raw snapshot in the blob store (private, never
    published) and its readable text. Claims are supported by quotes from ``extracted_text``.

    The same URL with the same text on the same day is one evidence row: fetching it again
    returns the existing one (unique on company, URL, text hash and day)."""

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("company_id", "url", "text_hash", "retrieved_on"),
        Index("ix_evidence_company_retrieved", "company_id", "retrieved_at"),
        Index("ix_evidence_task", "task_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    url: Mapped[str]
    """Canonical URL (as asked for); ``final_url`` is where redirects ended."""
    final_url: Mapped[str]
    title: Mapped[str | None]
    content_type: Mapped[str]
    language: Mapped[str | None]
    retrieved_at: Mapped[datetime]
    retrieved_on: Mapped[date] = mapped_column(Date)
    blob_key: Mapped[str]
    extracted_text: Mapped[str]
    text_hash: Mapped[str]
    truncated: Mapped[bool] = mapped_column(server_default="false")
    """The text was cut at the length limit."""
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id"))
    """The source that listed this URL, when one did (its trust level applies)."""
    source_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_items.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    """The run that first captured it."""


class EvidenceChunk(IdMixin, CreatedAtMixin, Base):
    """A retrieval unit of an evidence text (T-503): ``start``/``end`` locate it in
    ``evidence.extracted_text``. ``embedding`` is NULL when it could not be computed (it is then
    only found by keyword); ``embedding_model`` says which model made it, because vectors from
    different models are not comparable."""

    __tablename__ = "evidence_chunks"
    __table_args__ = (
        UniqueConstraint("evidence_id", "seq"),
        Index("ix_evidence_chunks_company", "company_id"),
        Index(
            "ix_evidence_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id"))
    seq: Mapped[int]
    start: Mapped[int]
    end: Mapped[int]
    text: Mapped[str]
    embedding: Mapped[list[float] | None] = mapped_column(HalfVector(EMBED_DIM))
    embedding_model: Mapped[str | None]


class StoryState(StrEnum):
    """platform/02 §6: DISCOVERED -> SELECTED -> IN_PRODUCTION -> PUBLISHED | DROPPED;
    DISCOVERED -> IGNORED."""

    DISCOVERED = "DISCOVERED"
    SELECTED = "SELECTED"
    IN_PRODUCTION = "IN_PRODUCTION"
    PUBLISHED = "PUBLISHED"
    DROPPED = "DROPPED"
    IGNORED = "IGNORED"


class Story(IdMixin, TimestampMixin, Base):
    """A topic worth writing about (T-504): source items about the same thing, from any number
    of sources. Found by clustering, not by a model; ``score`` ranks candidates."""

    __tablename__ = "stories"
    __table_args__ = (
        check_in("state", StoryState),
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        Index("ix_stories_company_state", "company_id", "state"),
        Index(
            "ix_stories_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    """Set when the story is selected for production."""
    title: Mapped[str]
    summary: Mapped[str | None]
    angle: Mapped[str | None]
    state: Mapped[str] = mapped_column(server_default=StoryState.DISCOVERED.value)
    score: Mapped[Decimal] = mapped_column(Numeric(4, 3), server_default="0")
    items_count: Mapped[int] = mapped_column(server_default="0")
    sources_count: Mapped[int] = mapped_column(server_default="0")
    """Distinct sources reporting it: corroboration."""
    first_seen_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    last_item_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    embedding: Mapped[list[float] | None] = mapped_column(HalfVector(EMBED_DIM))
    """Centroid of its items' embeddings (what new items are compared with)."""
    embedding_model: Mapped[str | None]
    seed: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    """For a story someone started by hand: its urls / query (the researcher's starting point)."""


class StoryItem(Base):
    """Which story a source item belongs to (each item to at most one)."""

    __tablename__ = "story_items"

    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id"), primary_key=True)
    source_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_items.id"), primary_key=True, unique=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    similarity: Mapped[float | None]
    """How close the item was to the story when it joined (None: it started the story)."""
    joined_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class ClaimType(StrEnum):
    FACT = "fact"
    NUMBER = "number"
    QUOTE = "quote"
    """Someone's words, quoted."""
    ATTRIBUTION = "attribution"
    """Who said or did something."""
    OPINION = "opinion"
    """An assessment; the only type that needs no supporting evidence (T-510)."""


class ClaimStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class SupportType(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class Claim(IdMixin, TimestampMixin, Base):
    """A checkable statement about a story (T-505). Articles cite claims, claims cite evidence:
    every fact, number and quote in an article traces back to a quote in a captured page."""

    __tablename__ = "claims"
    __table_args__ = (
        check_in("claim_type", ClaimType),
        check_in("status", ClaimStatus),
        Index("ix_claims_story", "story_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id"))
    text: Mapped[str]
    claim_type: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=ClaimStatus.UNVERIFIED.value)
    idempotency_key: Mapped[str | None] = mapped_column(unique=True)
    """The creating tool call's key: a retried call returns the same claim."""
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))


class ClaimEvidence(IdMixin, CreatedAtMixin, Base):
    """A quote from an evidence text, and how it bears on a claim. ``quote`` is always the
    evidence's own text between ``quote_start`` and ``quote_end``."""

    __tablename__ = "claim_evidence"
    __table_args__ = (
        check_in("support_type", SupportType),
        CheckConstraint("quote_end > quote_start AND quote_start >= 0", name="quote_span"),
        UniqueConstraint("claim_id", "evidence_id", "quote_start", "support_type"),
        Index("ix_claim_evidence_evidence", "evidence_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id"))
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id"))
    quote: Mapped[str]
    quote_start: Mapped[int]
    quote_end: Mapped[int]
    support_type: Mapped[str]
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))


class ArticleState(StrEnum):
    """platform/02 §6: DRAFT -> IN_REVIEW -> APPROVED -> PUBLISHED -> ARCHIVED;
    IN_REVIEW -> DRAFT (revise, at most twice) | REJECTED."""

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"


class Article(IdMixin, TimestampMixin, Base):
    """What gets published for a story (T-508): one per story, in several languages. Its text
    lives in versions; ``current_draft_group_id`` is the latest draft (one version per language)."""

    __tablename__ = "articles"
    __table_args__ = (
        check_in("state", ArticleState),
        UniqueConstraint("story_id"),
        UniqueConstraint("slug"),
        CheckConstraint("revision_count >= 0", name="revision_count_non_negative"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id"))
    slug: Mapped[str]
    """The public URL's name: /{lang}/articles/{slug}."""
    title: Mapped[str]
    """In the primary language (each version has its own title)."""
    state: Mapped[str] = mapped_column(server_default=ArticleState.DRAFT.value)
    primary_lang: Mapped[str]
    published_langs: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    current_draft_group_id: Mapped[uuid.UUID | None]
    revision_count: Mapped[int] = mapped_column(server_default="0")
    """Revisions the editor asked for (at most two, then the story is dropped)."""
    published_at: Mapped[datetime | None]
    published_group_id: Mapped[uuid.UUID | None]
    """The draft group that was published: what the public site shows (T-512)."""


class ArticleVersion(IdMixin, CreatedAtMixin, Base):
    """One language of one draft. The versions written together share ``draft_group_id`` and
    ``version`` and cite the same claims (checked when written). ``body`` is a list of blocks:
    ``{"type": "heading" | "paragraph" | "quote", "text": ..., "claim_ids": [...]}``."""

    __tablename__ = "article_versions"
    __table_args__ = (
        UniqueConstraint("article_id", "version", "lang"),
        Index("ix_article_versions_group", "draft_group_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id"))
    version: Mapped[int]
    lang: Mapped[str]
    draft_group_id: Mapped[uuid.UUID]
    title: Mapped[str]
    summary: Mapped[str | None]
    body: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    claim_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    """Every claim the version cites (the same set in every language of the group)."""
    translation_of_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("article_versions.id")
    )
    """For a secondary language: the primary-language version of the same draft."""
    change_summary: Mapped[str | None]
    idempotency_key: Mapped[str | None] = mapped_column(unique=True)
    """On the primary-language row: the writing call's key (a retry returns the same draft)."""
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    author_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))


class FactCheckReport(IdMixin, CreatedAtMixin, Base):
    """One deterministic fact-check of a draft (T-510): a verdict per cited claim, and what is left
    for the editor's semantic check (layer 3). A new report per run; the latest one counts."""

    __tablename__ = "fact_check_reports"
    __table_args__ = (Index("ix_fact_check_reports_article", "article_id", "created_at"),)

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id"))
    article_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("article_versions.id"))
    """The primary-language version of the draft checked (its group shares the claims)."""
    draft_group_id: Mapped[uuid.UUID]
    passed: Mapped[bool]
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    """[{claim_id, claim_type, verdict: pass|fail, problems[], notes[], related[]}]"""
    semantic_review: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    """For the editor (layer 3): each passing claim with its supporting quotes."""
    idempotency_key: Mapped[str | None] = mapped_column(unique=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))


class DistributionStatus(StrEnum):
    DRAFT = "draft"
    """Prepared, not sent (MVP social copy: only written to the database)."""
    PUBLISHED = "published"
    FAILED = "failed"


class Distribution(IdMixin, CreatedAtMixin, Base):
    """Where a published article went (T-512): the company's own site, and later the channels
    marketing prepares copy for (T-513). An article is on the site once."""

    __tablename__ = "distributions"
    __table_args__ = (
        check_in("status", DistributionStatus),
        check_regex("channel", "^[a-z][a-z0-9_]*$"),
        Index("ix_distributions_article", "article_id"),
        Index(
            "uq_distributions_site_once",
            "article_id",
            unique=True,
            postgresql_where=text("channel = 'site'"),
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id"))
    channel: Mapped[str]
    """site | social_draft | ..."""
    status: Mapped[str]
    external_ref: Mapped[str | None]
    """For the site: the primary language's path."""
    copy: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    """Per language: the site's {url, title}; for social drafts, the text."""
    created_by: Mapped[dict[str, Any]]
    """Actor JSON."""
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))


class AnalyticsEventType(StrEnum):
    VIEW = "view"
    """The article's page was opened."""
    READ_COMPLETE = "read_complete"
    """The reader reached the end of the article."""


class AnalyticsEvent(IdMixin, CreatedAtMixin, Base):
    """One reader beacon from the public site (T-515; platform/05 §8: no IP, no personal data).

    ``session_hash`` is a random id the reader's browser makes each day (it cannot be tied to a
    person or followed across days). A session counts once per article, language, kind and day:
    a repeat is dropped (unique key). Aggregated into ``analytics_daily`` (T-516); raw rows are
    deleted 30 days after aggregation (platform/08)."""

    __tablename__ = "analytics_events"
    __table_args__ = (
        check_in("event_type", AnalyticsEventType),
        check_regex("session_hash", "^[0-9a-f]{16,64}$"),
        UniqueConstraint("article_id", "lang", "event_type", "session_hash", "day"),
        Index("ix_analytics_events_company_created", "company_id", "created_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id"))
    lang: Mapped[str]
    event_type: Mapped[str]
    session_hash: Mapped[str]
    day: Mapped[date] = mapped_column(Date)
    """The UTC day it was received (the dedup window; the session id rotates daily too)."""


class AnalyticsDaily(IdMixin, TimestampMixin, Base):
    """Readers per article, language and UTC day (T-516), recounted from ``analytics_events`` by
    the hourly collector: it can always be rebuilt from the raw beacons while they are kept.

    ``views``: sessions that opened the article; ``read_complete``: sessions that reached its end;
    ``uniques``: sessions that did either. (A session counts once per kind and day already, so
    views are unique views.)"""

    __tablename__ = "analytics_daily"
    __table_args__ = (
        UniqueConstraint("article_id", "lang", "day"),
        Index("ix_analytics_daily_company_day", "company_id", "day"),
        CheckConstraint(
            "views >= 0 AND uniques >= 0 AND read_complete >= 0", name="counts_non_negative"
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id"))
    lang: Mapped[str]
    day: Mapped[date] = mapped_column(Date)
    views: Mapped[int] = mapped_column(server_default="0")
    uniques: Mapped[int] = mapped_column(server_default="0")
    read_complete: Mapped[int] = mapped_column(server_default="0")
