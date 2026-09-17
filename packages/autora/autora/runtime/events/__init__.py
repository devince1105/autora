"""Event envelope, payload registry and the runtime-owned event catalog.

Importing this package registers the runtime catalog. Other layers register their own events
by importing their module (``autora.company.events``; domains in their ``register()``).
"""

from autora.runtime.events import catalog as _catalog  # noqa: F401  (registers core events)
from autora.runtime.events.schema import (
    DuplicateEventType,
    EventEnvelope,
    EventPayload,
    Persistence,
    UnknownEventType,
    event,
    new_event,
    parse_event,
    payload_class,
    registered_event_types,
)

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
