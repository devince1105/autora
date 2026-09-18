"""Declarative base, shared column types and mixins for every SQLAlchemy model.

Conventions (see logs/platform/10_DATABASE_SCHEMA.md):
- Primary keys are UUIDv7 generated in Python (time-ordered).
- Every table carries ``company_id`` except ``companies`` itself.
- Enumerations are TEXT + CHECK constraints (not native PG enums) so adding a value is a
  one-line migration. FSM states are UPPER_SNAKE; plain categorical fields are lowercase.
- Money is NUMERIC(18, 6): model costs are fractions of a cent.
- The naming convention keeps constraint names deterministic for Alembic autogenerate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, MetaData, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from autora.infra.ids import uuid7

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Money = Numeric(18, 6)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
        Decimal: Money,
        dict[str, Any]: JSONB,
        list[str]: ARRAY(Text),
        str: Text,
    }


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


def check_in(column: str, enum: type[StrEnum], *, nullable: bool = False) -> CheckConstraint:
    """CHECK constraint restricting a TEXT column to the values of a StrEnum."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    expr = f"{column} IN ({values})"
    if nullable:
        expr = f"{column} IS NULL OR {expr}"
    return CheckConstraint(expr, name=f"{column}_valid")


def check_regex(column: str, pattern: str, name: str | None = None) -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '{pattern}'", name=name or f"{column}_format")
