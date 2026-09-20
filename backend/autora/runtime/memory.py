"""What an agent remembers between runs (platform/08 §1, T-609).

An agent's *working* memory is the transcript of the run it is in, and it already has one:
``agent_steps``. This is the other half — the few lines it carries into the *next* run. Without
it every run starts from nothing, and an agent asked for a third revision of the same draft has
no way to know it is the third.

What is kept is deliberately small and structured: the task, how it went, the one-line summary
the behavior already produces, and the issues that sent it back. Not the transcript, not the
output — those are in the run, one join away, and copying them here would make the memory grow
until reading it costs more than the run it is meant to help.

**Bounded twice, on write.** Rows expire (30 days) and an agent keeps only its newest
``ROW_CAP``. Both are enforced when remembering, so the bound holds even if no cleanup job ever
runs — a maintenance job that is required for correctness is a bound that fails quietly the
first time the job does.

**Trimmed first.** When the context does not fit, this is what goes before anything else
(platform/08): a run can be done without remembering the last one, but not without its task.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import AgentMemoryEntry, MemoryKind

TTL = timedelta(days=30)
ROW_CAP = 50
"""Per agent. Older entries are dropped when a new one arrives (platform/08 §4)."""

RECENT_RUNS = 3
"""How many go into a run's context. Three is what the spec asks for, and it is about as many
as a model will actually use: the point is "what did I just do", not a career history."""

MAX_CHARS = 1200
"""What the recollection may take up in the prompt. A bound here as well as a row cap, because
three long summaries are still long."""


@dataclass(frozen=True)
class Recollection:
    """The lines an agent is given about its recent work."""

    entries: tuple[AgentMemoryEntry, ...]

    def text(self, limit: int = MAX_CHARS) -> str:
        """Render for the prompt, newest last, cut to ``limit`` characters.

        Newest last because that is the order a reader expects of a diary, and because the most
        recent run is the one most likely to matter — it should be nearest the question.
        """
        lines = []
        for entry in reversed(self.entries):
            content = entry.content or {}
            task = content.get("task") or "a task"
            outcome = content.get("outcome") or "finished"
            line = f"- {task}: {outcome}"
            summary = content.get("summary")
            if summary:
                line += f" — {summary}"
            issues = content.get("issues") or []
            if issues:
                line += f" — because: {'; '.join(str(i) for i in issues[:3])}"
            lines.append(line)
        if not lines:
            return ""
        body = "\n".join(lines)
        if len(body) > limit:
            body = body[: limit - 1].rstrip() + "…"
        return f"Your recent runs:\n{body}"

    def __bool__(self) -> bool:
        return bool(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


@dataclass
class AgentMemory:
    """Remembers and recalls. Does not commit."""

    ttl: timedelta = TTL
    row_cap: int = ROW_CAP
    clock: Any = field(default=lambda: datetime.now(UTC))

    async def remember(
        self,
        session: AsyncSession,
        *,
        company_id: uuid.UUID,
        agent_id: uuid.UUID,
        content: dict[str, Any],
        kind: MemoryKind = MemoryKind.RUN,
        task_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> AgentMemoryEntry:
        """Keep one thing, and drop whatever that pushes past the bounds."""
        now = self.clock()
        entry = AgentMemoryEntry(
            company_id=company_id,
            agent_id=agent_id,
            kind=kind.value,
            content=content,
            task_id=task_id,
            run_id=run_id,
            created_at=now,
            expires_at=now + self.ttl,
        )
        session.add(entry)
        await session.flush()
        await self._enforce_cap(session, agent_id)
        return entry

    async def recall(
        self,
        session: AsyncSession,
        agent_id: uuid.UUID,
        *,
        limit: int = RECENT_RUNS,
        kind: MemoryKind | None = None,
    ) -> Recollection:
        """The newest entries that have not expired, newest first."""
        now = self.clock()
        stmt = (
            select(AgentMemoryEntry)
            .where(
                AgentMemoryEntry.agent_id == agent_id,
                (AgentMemoryEntry.expires_at.is_(None)) | (AgentMemoryEntry.expires_at > now),
            )
            .order_by(AgentMemoryEntry.created_at.desc())
            .limit(limit)
        )
        if kind is not None:
            stmt = stmt.where(AgentMemoryEntry.kind == kind.value)
        return Recollection(tuple(await session.scalars(stmt)))

    async def forget_expired(self, session: AsyncSession, limit: int = 500) -> int:
        """Delete what has aged out. Housekeeping, not correctness: ``recall`` already ignores
        expired rows, so this only keeps the table from holding what nobody will read."""
        now = self.clock()
        stale = (
            await session.scalars(
                select(AgentMemoryEntry.id)
                .where(AgentMemoryEntry.expires_at.is_not(None), AgentMemoryEntry.expires_at <= now)
                .limit(limit)
            )
        ).all()
        if not stale:
            return 0
        await session.execute(delete(AgentMemoryEntry).where(AgentMemoryEntry.id.in_(stale)))
        return len(stale)

    def maintenance_job(self):
        """For the worker's periodic jobs (registered by the composition root)."""

        async def forget(session: AsyncSession) -> None:
            await self.forget_expired(session)

        return forget

    async def _enforce_cap(self, session: AsyncSession, agent_id: uuid.UUID) -> None:
        kept = (
            await session.scalars(
                select(AgentMemoryEntry.id)
                .where(AgentMemoryEntry.agent_id == agent_id)
                .order_by(AgentMemoryEntry.created_at.desc(), AgentMemoryEntry.id.desc())
                .limit(self.row_cap)
            )
        ).all()
        if len(kept) < self.row_cap:
            return
        await session.execute(
            delete(AgentMemoryEntry).where(
                AgentMemoryEntry.agent_id == agent_id,
                AgentMemoryEntry.id.not_in(kept),
            )
        )
        await session.flush()


def run_memory(
    *,
    task: str,
    outcome: str,
    summary: str | None = None,
    issues: Sequence[str] = (),
) -> dict[str, Any]:
    """The shape of what a finished run leaves behind."""
    content: dict[str, Any] = {"task": task, "outcome": outcome}
    if summary:
        content["summary"] = summary[:400]
    if issues:
        content["issues"] = [str(issue)[:200] for issue in issues[:5]]
    return content


async def count(session: AsyncSession, agent_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(AgentMemoryEntry)
            .where(AgentMemoryEntry.agent_id == agent_id)
        )
        or 0
    )
