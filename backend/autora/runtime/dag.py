"""Workflow templates and the engine that runs them as task graphs (T-203).

A ``WorkflowTemplate`` is a declarative DAG written in code by a domain (logs/platform/01 §5:
LLMs never generate DAGs in the MVP). ``WorkflowEngine.instantiate`` turns it into a
``workflow_runs`` row plus one task per node. From then on the engine only reacts:

- a task SUCCEEDED -> every downstream task whose dependencies have all succeeded becomes READY
  and is reported in ``TASK_SUCCEEDED.unlocks`` (the 3D office animates that as a hand-off);
- a task FAILED for good or was CANCELLED -> every task that depends on it, directly or
  transitively, is cancelled (it can never run);
- when no task is left unfinished the workflow run closes: SUCCEEDED if every task succeeded,
  FAILED if any failed, otherwise CANCELLED.

The engine plugs into the TaskManager's hooks, so all of this happens inside the same
transaction as the task change that triggered it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Task, TaskState, WorkflowRun, WorkflowRunState
from autora.infra.ids import uuid7
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventPayload, new_event
from autora.runtime.lifecycles import TASK_FSM, WORKFLOW_RUN_FSM
from autora.runtime.task_manager import TaskManager

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_TEMPLATE_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class WorkflowError(Exception):
    pass


class InvalidTemplate(WorkflowError):
    pass


@dataclass(frozen=True)
class NodeSpec:
    name: str
    """Task name, unique within the template, e.g. "research"."""
    display_name: str
    """Shown to people. ``str.format`` placeholders are filled from the run's params."""
    required_role: str
    depends_on: tuple[str, ...] = ()
    input: dict[str, Any] = field(default_factory=dict)
    output_schema_ref: str | None = None
    budget_usd: Decimal | None = None
    max_attempts: int = 3
    priority: int = 100


@dataclass(frozen=True)
class WorkflowTemplate:
    name: str
    nodes: tuple[NodeSpec, ...]

    def __post_init__(self) -> None:
        if not _TEMPLATE_NAME.match(self.name):
            raise InvalidTemplate(f"template name must be dotted lower_snake_case: {self.name!r}")
        if not self.nodes:
            raise InvalidTemplate(f"{self.name}: a template needs at least one node")
        names = [n.name for n in self.nodes]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise InvalidTemplate(f"{self.name}: duplicate node names {duplicates}")
        for node in self.nodes:
            if not _NAME.match(node.name) or not _NAME.match(node.required_role):
                raise InvalidTemplate(f"{self.name}: invalid node name or role in {node.name!r}")
            unknown = set(node.depends_on) - set(names)
            if unknown:
                raise InvalidTemplate(f"{self.name}.{node.name}: unknown dependencies {unknown}")
        self.topological_order()  # raises on cycles

    def node(self, name: str) -> NodeSpec:
        for node in self.nodes:
            if node.name == name:
                return node
        raise KeyError(name)

    def topological_order(self) -> list[NodeSpec]:
        """Nodes ordered so every node comes after its dependencies (stable by declaration)."""
        done: list[str] = []
        remaining = list(self.nodes)
        while remaining:
            ready = [n for n in remaining if set(n.depends_on) <= set(done)]
            if not ready:
                cycle = ", ".join(n.name for n in remaining)
                raise InvalidTemplate(f"{self.name}: dependency cycle among {cycle}")
            for node in ready:
                done.append(node.name)
                remaining.remove(node)
        return [self.node(name) for name in done]


class TemplateRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, WorkflowTemplate] = {}

    def register(self, template: WorkflowTemplate) -> WorkflowTemplate:
        if template.name in self._templates:
            raise InvalidTemplate(f"template {template.name!r} is already registered")
        self._templates[template.name] = template
        return template

    def get(self, name: str) -> WorkflowTemplate:
        try:
            return self._templates[name]
        except KeyError:
            raise WorkflowError(f"unknown workflow template {name!r}") from None

    def names(self) -> list[str]:
        return sorted(self._templates)


@dataclass
class WorkflowEngine:
    task_manager: TaskManager
    templates: TemplateRegistry
    actor: Actor = field(default_factory=lambda: Actor.system("workflow_engine"))
    _cancelling: dict[uuid.UUID, str] = field(default_factory=dict, init=False, repr=False)
    """Runs being cancelled as a whole, with the operator's reason. Closes triggered by the
    per-task cancellations inside ``cancel`` must record that reason, not a generic one."""

    def __post_init__(self) -> None:
        tm = self.task_manager
        if tm.on_task_finished is not None or tm.on_task_failed is not None:
            raise WorkflowError("the task manager is already bound to another workflow engine")
        tm.on_task_finished = self.on_task_finished
        tm.on_task_failed = self.on_task_failed

    # --- instantiate -----------------------------------------------------------------------

    async def instantiate(
        self,
        session: AsyncSession,
        template_name: str,
        *,
        company_id: uuid.UUID,
        project_id: uuid.UUID,
        params: dict[str, Any] | None = None,
        cycle_id: uuid.UUID | None = None,
    ) -> tuple[WorkflowRun, dict[str, Task]]:
        template = self.templates.get(template_name)
        params = params or {}
        order = template.topological_order()
        ids = {node.name: uuid7() for node in order}
        display = {node.name: _render(template, node, params) for node in order}

        run = WorkflowRun(
            id=uuid7(),
            company_id=company_id,
            project_id=project_id,
            cycle_id=cycle_id,
            template_name=template.name,
            params=params,
            state=WorkflowRunState.RUNNING.value,
        )
        session.add(run)
        await session.flush()
        await self._emit(
            session,
            run,
            ev.WorkflowRunCreated(
                template=template.name,
                params=params,
                project_id=project_id,
                task_ids=[ids[node.name] for node in order],
            ),
        )

        tasks: dict[str, Task] = {}
        for node in order:
            tasks[node.name] = await self.task_manager.add_task(
                session,
                task_id=ids[node.name],
                company_id=company_id,
                project_id=project_id,
                workflow_run_id=run.id,
                cycle_id=cycle_id,
                name=node.name,
                display_name=display[node.name],
                required_role=node.required_role,
                depends_on=[ids[dep] for dep in node.depends_on],
                input={**node.input, "params": params},
                output_schema_ref=node.output_schema_ref,
                budget_usd=node.budget_usd,
                max_attempts=node.max_attempts,
                priority=node.priority,
            )
        return run, tasks

    async def cancel(self, session: AsyncSession, run: WorkflowRun, *, reason: str) -> None:
        """Cancel every unfinished task of the run (operator action, stage timeout)."""
        self._cancelling[run.id] = reason
        try:
            for task in await self._tasks(session, run.id, lock=True):
                if not TASK_FSM.is_terminal(task.state):
                    await self.task_manager.cancel(session, task, reason=reason)
            await self._close_if_done(session, run.id)
        finally:
            self._cancelling.pop(run.id, None)

    # --- task manager hooks ----------------------------------------------------------------

    async def on_task_finished(self, session: AsyncSession, task: Task) -> list[ev.UnlockedTask]:
        if task.workflow_run_id is None:
            return []
        siblings = await self._tasks(session, task.workflow_run_id, lock=True)
        succeeded = {t.id for t in siblings if t.state == TaskState.SUCCEEDED}
        unlocked = []
        for downstream in siblings:
            if (
                task.id in downstream.depends_on
                and downstream.state == TaskState.PENDING
                and set(downstream.depends_on) <= succeeded
            ):
                await self.task_manager.mark_ready(session, downstream)
                unlocked.append(
                    ev.UnlockedTask(task_id=downstream.id, required_role=downstream.required_role)
                )
        await self._close_if_done(session, task.workflow_run_id, siblings=siblings)
        return unlocked

    async def on_task_failed(self, session: AsyncSession, task: Task) -> None:
        """``task`` is FAILED or CANCELLED: nothing that depends on it can run any more."""
        if task.workflow_run_id is None:
            return
        for downstream in await self._tasks(session, task.workflow_run_id, lock=True):
            if task.id in downstream.depends_on and not TASK_FSM.is_terminal(downstream.state):
                # TaskManager.cancel calls this hook again for the cancelled task: that is what
                # makes the cancellation transitive.
                await self.task_manager.cancel(
                    session, downstream, reason=f"upstream {task.name} {task.state.lower()}"
                )
        await self._close_if_done(session, task.workflow_run_id)

    # --- internals -------------------------------------------------------------------------

    async def _close_if_done(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        *,
        siblings: list[Task] | None = None,
    ) -> None:
        run = await session.get(WorkflowRun, run_id, with_for_update=True)
        if run is None or WORKFLOW_RUN_FSM.is_terminal(run.state):
            return
        tasks = siblings if siblings is not None else await self._tasks(session, run_id)
        if any(not TASK_FSM.is_terminal(t.state) for t in tasks):
            return

        now = datetime.now(UTC)
        run.finished_at = now
        duration_ms = max(0, int((now - run.created_at).total_seconds() * 1000))
        failed = [t for t in tasks if t.state == TaskState.FAILED]
        if all(t.state == TaskState.SUCCEEDED for t in tasks):
            await WORKFLOW_RUN_FSM.transition(
                session, run, WorkflowRunState.SUCCEEDED, actor=self.actor
            )
            await self._emit(session, run, ev.WorkflowRunCompleted(duration_ms=duration_ms))
        elif failed:
            first = failed[0]  # tasks are listed in creation (= topological) order
            await WORKFLOW_RUN_FSM.transition(
                session, run, WorkflowRunState.FAILED, actor=self.actor
            )
            await self._emit(
                session,
                run,
                ev.WorkflowRunFailed(
                    duration_ms=duration_ms,
                    failed_task_id=first.id,
                    reason=f"task {first.name} failed",
                ),
            )
        else:
            reason = self._cancelling.get(run_id, "tasks cancelled")
            await WORKFLOW_RUN_FSM.transition(
                session, run, WorkflowRunState.CANCELLED, actor=self.actor, reason=reason
            )
            await self._emit(
                session, run, ev.WorkflowRunCancelled(duration_ms=duration_ms, reason=reason)
            )

    @staticmethod
    async def _tasks(session: AsyncSession, run_id: uuid.UUID, *, lock: bool = False) -> list[Task]:
        stmt = select(Task).where(Task.workflow_run_id == run_id).order_by(Task.created_at, Task.id)
        if lock:
            stmt = stmt.with_for_update()
        return list((await session.scalars(stmt)).all())

    async def _emit(self, session: AsyncSession, run: WorkflowRun, payload: EventPayload) -> None:
        await emit(
            session,
            new_event(
                payload,
                company_id=run.company_id,
                actor=self.actor,
                aggregate_type="workflow_run",
                aggregate_id=run.id,
                workflow_run_id=run.id,
                cycle_id=run.cycle_id,
                correlation_id=run.id,
            ),
        )


def _render(template: WorkflowTemplate, node: NodeSpec, params: dict[str, Any]) -> str:
    try:
        return node.display_name.format(**params)
    except (KeyError, IndexError) as exc:
        raise WorkflowError(
            f"{template.name}.{node.name}: display_name needs parameter {exc}"
        ) from None
