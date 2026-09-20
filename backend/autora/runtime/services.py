"""Service nodes: workflow steps no agent runs (T-514), e.g. approving and publishing an article.

A template node with ``service="<name>"`` becomes a task whose input carries that name. Each worker
tick, ``ServiceDispatcher.dispatch`` takes the READY service tasks (``FOR UPDATE SKIP LOCKED``, so
several workers never run the same one) and calls the handler registered under the name, inside
the task's transaction. The handler decides what happens to the task through its context:

- ``complete(output)``: the step is done (SUCCEEDED; the workflow engine unlocks what follows);
- ``request_approval(...)``: a person must decide (WAITING_APPROVAL; the approval's decision
  finishes the task, see ``ApprovalService``);
- ``fail(error_class, message)``: the step cannot be done (FAILED for good; what depends on it is
  cancelled).

A handler that raises fails its task the same way (the error is recorded on the task), after the
failed transaction is rolled back. A handler that returns without deciding is a bug and fails the
task too.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.db.models import Approval, ApprovalKind, Task, TaskState
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from autora.runtime.policy import PolicyEngine
from autora.runtime.task_manager import TaskManager

log = logging.getLogger("autora.services")

BATCH = 20


class ServiceError(Exception):
    pass


@dataclass
class ServiceContext:
    session: AsyncSession
    task: Task
    task_manager: TaskManager
    approvals: ApprovalService
    policy: PolicyEngine
    actor: Actor
    decided: bool = False

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.task.input.get("params") or {})

    async def complete(self, output: dict[str, Any] | None, *, summary: str | None = None) -> None:
        self._decide()
        await self.task_manager.complete_without_run(
            self.session, self.task, output, output_summary=summary
        )

    async def request_approval(
        self,
        *,
        kind: ApprovalKind | str,
        summary: str,
        action: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Approval:
        self._decide()
        return await self.approvals.request_for_task(
            self.session,
            self.task,
            kind=kind,
            summary=summary,
            action=action,
            payload=payload,
            requested_by=self.actor,
        )

    async def fail(self, error_class: str, message: str) -> None:
        self._decide()
        await self.task_manager.fail_without_run(
            self.session, self.task, error_class=error_class, message=message
        )

    def _decide(self) -> None:
        if self.decided:
            raise ServiceError(f"service task {self.task.id} was already decided")
        self.decided = True


ServiceHandler = Callable[[ServiceContext], Awaitable[None]]


class ServiceRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ServiceHandler] = {}

    def register(self, name: str, handler: ServiceHandler) -> None:
        if name in self._handlers:
            raise ServiceError(f"service {name!r} is already registered")
        self._handlers[name] = handler

    def get(self, name: str) -> ServiceHandler | None:
        return self._handlers.get(name)

    def names(self) -> list[str]:
        return sorted(self._handlers)


@dataclass
class ServiceDispatcher:
    session_factory: async_sessionmaker[AsyncSession]
    registry: ServiceRegistry
    task_manager: TaskManager
    approvals: ApprovalService
    policy: PolicyEngine
    company_ids: frozenset[uuid.UUID] | None = None
    batch: int = BATCH
    _unknown: set[str] = field(default_factory=set)

    async def dispatch(self) -> int:
        """Run every READY service task (up to ``batch``). Returns how many were handled."""
        names = self.registry.names()
        if not names:
            return 0
        async with self.session_factory() as session:
            ids = (
                await session.scalars(
                    select(Task.id)
                    .where(
                        Task.state == TaskState.READY,
                        Task.input["service"].astext.in_(names),
                        *([Task.company_id.in_(self.company_ids)] if self.company_ids else []),
                    )
                    .order_by(Task.priority, Task.created_at)
                    .limit(self.batch)
                )
            ).all()
        handled = 0
        for task_id in ids:
            handled += await self._run(task_id)
        return handled

    async def _run(self, task_id: uuid.UUID) -> int:
        async with self.session_factory() as session:
            task = await session.scalar(
                select(Task).where(Task.id == task_id).with_for_update(skip_locked=True)
            )
            if task is None or task.state != TaskState.READY:
                return 0  # another worker has it, or it moved on
            name = task.input["service"]
            ctx = ServiceContext(
                session=session,
                task=task,
                task_manager=self.task_manager,
                approvals=self.approvals,
                policy=self.policy,
                actor=Actor.system(f"service:{name}"),
            )
            try:
                await self.registry.get(name)(ctx)  # type: ignore[misc]
                if not ctx.decided:
                    raise ServiceError(f"service {name!r} did not complete, fail or wait")
                await session.commit()
                return 1
            except Exception as exc:  # noqa: BLE001 - recorded on the task below
                log.exception("service %s failed on task %s", name, task_id)
                error = (type(exc).__name__, str(exc)[:1000])
        async with self.session_factory() as session:
            task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is not None and task.state == TaskState.READY:
                await self.task_manager.fail_without_run(
                    session, task, error_class=error[0], message=error[1]
                )
                await session.commit()
        return 1
