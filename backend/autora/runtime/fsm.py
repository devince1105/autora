"""Generic finite state machine for entity lifecycles (logs/platform/03_AGENT_RUNTIME.md §4).

Used for Task, AgentRun, Project, Article, Story, Cycle, Approval. Each lifecycle declares a
``StateMachine`` once (in the layer that owns the entity); ``transition()`` validates the move,
runs guards, updates the entity and appends a ``state_transitions`` audit row in the caller's
transaction.

What this module does NOT do:
- emit events (callers combine a transition with ``events.emit`` in the same transaction);
- concurrency control (TaskManager claims rows with ``FOR UPDATE SKIP LOCKED`` before
  transitioning; entities without a claim step must lock the row themselves).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import StateTransition
from autora.runtime.actor import Actor


class FSMError(Exception):
    pass


class IllegalTransition(FSMError):
    def __init__(self, entity_type: str, from_state: str, to_state: str, allowed: Iterable[str]):
        self.entity_type = entity_type
        self.from_state = from_state
        self.to_state = to_state
        self.allowed = sorted(allowed)
        super().__init__(
            f"{entity_type}: illegal transition {from_state} -> {to_state} "
            f"(allowed from {from_state}: {', '.join(self.allowed) or 'none, terminal state'})"
        )


class GuardRejected(FSMError):
    def __init__(self, entity_type: str, from_state: str, to_state: str, reason: str):
        self.reason = reason
        super().__init__(f"{entity_type}: {from_state} -> {to_state} rejected by guard: {reason}")


class StatefulEntity(Protocol):
    id: Any
    company_id: Any


Guard = Callable[[Any], str | None]
"""Returns None to allow the transition, or a human-readable reason to reject it."""


@dataclass(frozen=True)
class StateMachine[S: StrEnum]:
    entity_type: str
    states: type[S]
    transitions: Mapping[S, frozenset[S]]
    initial: S
    state_attr: str = "state"
    guards: Mapping[tuple[S, S], Guard] = field(default_factory=dict)

    def __post_init__(self) -> None:
        members = set(self.states)
        if self.initial not in members:
            raise ValueError(f"{self.entity_type}: initial state {self.initial!r} not in states")
        for source, targets in self.transitions.items():
            unknown = ({source} | set(targets)) - members
            if unknown:
                raise ValueError(f"{self.entity_type}: unknown states in transitions: {unknown}")
            if source in targets:
                raise ValueError(f"{self.entity_type}: self-transition {source} is not allowed")
        for source, target in self.guards:
            if target not in self.transitions.get(source, frozenset()):
                raise ValueError(
                    f"{self.entity_type}: guard on undeclared transition {source} -> {target}"
                )

    # --- queries ---------------------------------------------------------------------------

    def allowed_from(self, state: S | str) -> frozenset[S]:
        return self.transitions.get(self.states(state), frozenset())

    def can(self, from_state: S | str, to_state: S | str) -> bool:
        return self.states(to_state) in self.allowed_from(from_state)

    def is_terminal(self, state: S | str) -> bool:
        return not self.allowed_from(state)

    def terminal_states(self) -> frozenset[S]:
        return frozenset(s for s in self.states if self.is_terminal(s))

    def shortest_path(self, from_state: S | str, to_state: S | str) -> list[S]:
        """States to pass through (excluding ``from_state``) to reach ``to_state``.

        Empty when already there; raises ``IllegalTransition`` when unreachable.
        """
        start, goal = self.states(from_state), self.states(to_state)
        if start == goal:
            return []
        previous: dict[S, S] = {}
        frontier = [start]
        while frontier:
            current = frontier.pop(0)
            for nxt in sorted(self.allowed_from(current), key=str):
                if nxt in previous or nxt == start:
                    continue
                previous[nxt] = current
                if nxt == goal:
                    path = [goal]
                    while path[-1] != start:
                        path.append(previous[path[-1]])
                    return list(reversed(path))[1:]
                frontier.append(nxt)
        raise IllegalTransition(
            self.entity_type, start, goal, (s.value for s in self.allowed_from(start))
        )

    # --- validation ------------------------------------------------------------------------

    def check(self, entity: Any, to_state: S | str) -> tuple[S, S]:
        """Raise if ``entity`` may not move to ``to_state``; return (from, to) otherwise."""
        current = self.states(getattr(entity, self.state_attr))
        target = self.states(to_state)
        if target not in self.allowed_from(current):
            raise IllegalTransition(
                self.entity_type, current, target, (s.value for s in self.allowed_from(current))
            )
        guard = self.guards.get((current, target))
        if guard is not None:
            rejection = guard(entity)
            if rejection is not None:
                raise GuardRejected(self.entity_type, current, target, rejection)
        return current, target

    # --- mutation --------------------------------------------------------------------------

    async def transition(
        self,
        session: AsyncSession,
        entity: StatefulEntity,
        to_state: S | str,
        *,
        actor: Actor,
        reason: str | None = None,
    ) -> StateTransition:
        """Move ``entity`` to ``to_state`` and append the audit row. Does not commit."""
        current, target = self.check(entity, to_state)
        setattr(entity, self.state_attr, target.value)
        record = StateTransition(
            company_id=entity.company_id,
            entity_type=self.entity_type,
            entity_id=entity.id,
            from_state=current.value,
            to_state=target.value,
            reason=reason,
            actor=actor.as_json(),
        )
        session.add(record)
        await session.flush()
        return record

    async def transition_via(
        self,
        session: AsyncSession,
        entity: StatefulEntity,
        to_state: S | str,
        *,
        actor: Actor,
        reason: str | None = None,
    ) -> list[StateTransition]:
        """Reach ``to_state`` along the shortest legal path, auditing every hop.

        For callers that own the outcome but not every intermediate step (the task manager
        completing a run the runner left in RUNNING). Guards apply on each hop.
        """
        records = []
        for hop in self.shortest_path(getattr(entity, self.state_attr), to_state):
            records.append(await self.transition(session, entity, hop, actor=actor, reason=reason))
        return records


def transitions[S: StrEnum](spec: Mapping[S, Iterable[S]]) -> dict[S, frozenset[S]]:
    """Helper for readable declarations: ``transitions({A: [B, C], B: [C]})``."""
    return {source: frozenset(targets) for source, targets in spec.items()}
