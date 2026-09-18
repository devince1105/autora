"""Unified event envelope and payload registry (logs/3d-office/03_EVENT_MODEL.md).

One schema serves audit, cross-module notification and realtime push. Pydantic here is the
single source of truth; ``frontend/event-schema`` (TS types + zod) is generated from it (T-108).

- Each payload class is registered with ``@event("EVENT_TYPE")`` and is keyed by
  ``(event_type, schema_version)`` so old stored events stay readable after a breaking change.
- ``new_event(payload, ...)`` builds an envelope for emission; ``seq`` stays ``None`` until the
  outbox insert assigns it (T-106).
- ``parse_event(data)`` validates an envelope and its payload against the registry.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import (
    BaseModel,
    ConfigDict,
    SerializeAsAny,
    ValidationInfo,
    field_validator,
    model_validator,
)

from autora.infra.ids import uuid7
from autora.runtime.actor import Actor

EVENT_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")


class Persistence(StrEnum):
    PERSISTED = "persisted"
    """Written to the `events` table in the same transaction as the state change."""
    EPHEMERAL = "ephemeral"
    """Pushed over WebSocket only (progress, heartbeat). Never has a seq."""


class EventRegistryError(Exception):
    pass


class DuplicateEventType(EventRegistryError):
    pass


class UnknownEventType(EventRegistryError):
    pass


class EventPayload(BaseModel):
    """Base class for typed payloads. Subclasses are registered with ``@event``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: ClassVar[str]
    schema_version: ClassVar[int]
    persistence: ClassVar[Persistence]


_REGISTRY: dict[tuple[str, int], type[EventPayload]] = {}


def event(
    event_type: str,
    *,
    version: int = 1,
    persistence: Persistence = Persistence.PERSISTED,
):
    """Register a payload class for ``event_type`` at ``version``."""
    if not EVENT_TYPE_PATTERN.match(event_type):
        raise EventRegistryError(f"event type must be UPPER_SNAKE_CASE: {event_type!r}")
    if version < 1:
        raise EventRegistryError(f"{event_type}: schema version must be >= 1")

    def decorator(cls: type[EventPayload]) -> type[EventPayload]:
        if not issubclass(cls, EventPayload):
            raise EventRegistryError(f"{cls.__name__} must subclass EventPayload")
        key = (event_type, version)
        existing = _REGISTRY.get(key)
        if existing is not None and existing is not cls:
            raise DuplicateEventType(
                f"{event_type} v{version} already registered by {existing.__module__}."
                f"{existing.__qualname__}"
            )
        cls.event_type = event_type
        cls.schema_version = version
        cls.persistence = persistence
        _REGISTRY[key] = cls
        return cls

    return decorator


def payload_class(event_type: str, version: int = 1) -> type[EventPayload]:
    try:
        return _REGISTRY[(event_type, version)]
    except KeyError:
        known_versions = sorted(v for (t, v) in _REGISTRY if t == event_type)
        if known_versions:
            raise UnknownEventType(
                f"{event_type} has no schema version {version} (known: {known_versions})"
            ) from None
        raise UnknownEventType(f"unknown event type: {event_type}") from None


def registered_event_types() -> dict[tuple[str, int], type[EventPayload]]:
    return dict(_REGISTRY)


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: uuid.UUID
    seq: int | None = None
    """Assigned by the database on insert; None for ephemeral or not-yet-persisted events."""
    event_type: str
    schema_version: int = 1
    company_id: uuid.UUID
    occurred_at: datetime

    aggregate_type: str
    aggregate_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
    cycle_id: uuid.UUID | None = None
    correlation_id: uuid.UUID | None = None
    causation_id: uuid.UUID | None = None

    actor: Actor
    payload: SerializeAsAny[EventPayload]

    @field_validator("event_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if not any(t == value for (t, _) in _REGISTRY):
            raise ValueError(f"unknown event type: {value}")
        return value

    @field_validator("occurred_at")
    @classmethod
    def _tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("payload", mode="before")
    @classmethod
    def _typed_payload(cls, value: Any, info: ValidationInfo) -> EventPayload:
        event_type = info.data.get("event_type")
        version = info.data.get("schema_version", 1)
        if event_type is None:
            raise ValueError("payload cannot be validated without a valid event_type")
        try:
            expected = payload_class(event_type, version)
        except UnknownEventType as exc:
            raise ValueError(str(exc)) from None
        if isinstance(value, EventPayload):
            if type(value) is not expected:
                raise ValueError(
                    f"payload {type(value).__name__} does not match {event_type} "
                    f"v{version} ({expected.__name__})"
                )
            return value
        return expected.model_validate(value)

    @model_validator(mode="after")
    def _ephemeral_has_no_seq(self) -> EventEnvelope:
        if self.persistence is Persistence.EPHEMERAL and self.seq is not None:
            raise ValueError(f"{self.event_type} is ephemeral and cannot carry a seq")
        return self

    @property
    def persistence(self) -> Persistence:
        return type(self.payload).persistence


def new_event(
    payload: EventPayload,
    *,
    company_id: uuid.UUID,
    actor: Actor,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    agent_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    workflow_run_id: uuid.UUID | None = None,
    cycle_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    causation_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
) -> EventEnvelope:
    cls = type(payload)
    if not hasattr(cls, "event_type"):
        raise UnknownEventType(f"{cls.__name__} is not registered with @event")
    return EventEnvelope(
        event_id=uuid7(),
        event_type=cls.event_type,
        schema_version=cls.schema_version,
        company_id=company_id,
        occurred_at=occurred_at or datetime.now(UTC),
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        agent_id=agent_id,
        task_id=task_id,
        run_id=run_id,
        workflow_run_id=workflow_run_id,
        cycle_id=cycle_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        actor=actor,
        payload=payload,
    )


def parse_event(data: dict[str, Any] | str | bytes) -> EventEnvelope:
    if isinstance(data, str | bytes):
        return EventEnvelope.model_validate_json(data)
    return EventEnvelope.model_validate(data)


__all__ = [
    "DuplicateEventType",
    "EventEnvelope",
    "EventPayload",
    "Persistence",
    "UnknownEventType",
    "event",
    "new_event",
    "parse_event",
    "payload_class",
    "registered_event_types",
]
