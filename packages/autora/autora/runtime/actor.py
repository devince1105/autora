"""Who caused a change: an agent, a system component, or a human.

Stored as JSONB on audit rows (state_transitions, policies, projects) and carried on every
event envelope.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Actor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["agent", "system", "human"]
    id: str = Field(min_length=1)
    """Agent UUID, system component name (e.g. "scheduler"), or human user id."""

    @classmethod
    def agent(cls, agent_id: object) -> Actor:
        return cls(kind="agent", id=str(agent_id))

    @classmethod
    def system(cls, component: str) -> Actor:
        return cls(kind="system", id=component)

    @classmethod
    def human(cls, user_id: str) -> Actor:
        return cls(kind="human", id=user_id)

    def as_json(self) -> dict[str, str]:
        return self.model_dump(mode="json")
