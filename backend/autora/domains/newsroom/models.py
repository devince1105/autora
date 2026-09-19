"""Newsroom tables (logs/platform/05_NEWSROOM_DOMAIN.md §1, 10_DATABASE_SCHEMA.md).

Phase 5 adds them task by task; T-501: sources and the items polled from them; T-502: evidence;
T-503: evidence chunks with embeddings.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import Base, CreatedAtMixin, IdMixin, TimestampMixin, check_in
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
