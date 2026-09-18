"""Echo domain tables. One: the note each agent writes (the "artifact" of the workflow)."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import Base, CreatedAtMixin, IdMixin


class EchoNote(IdMixin, CreatedAtMixin, Base):
    """Written by the ``echo_note`` tool. ``idempotency_key`` is unique, so a retried or re-run
    tool call returns the existing note instead of writing a second one (T-215)."""

    __tablename__ = "echo_notes"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    """The run that first wrote it (a later attempt reusing it does not change this)."""
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    role: Mapped[str]
    text: Mapped[str]
