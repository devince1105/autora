"""Human approvals (T-206, logs/platform/07_PERMISSION_MODEL.md §4, D-001).

Three ways an approval can be attached:

- **Agent run** (``request_for_run``): a policy decision said ``needs_approval`` in the middle of
  a run. The worker is released, the run stays open in WAITING_APPROVAL and the agent shows
  WAITING{approval}. Approved: the task goes back to READY and the *same* agent resumes the
  *same* run (the task manager only lets that agent claim it). Rejected: the task is cancelled
  (downstream tasks follow through the workflow engine).
- **Human task node** (``request_for_task``): the task itself is the decision (e.g. approve an
  article). Approved: the task SUCCEEDED and the next node unlocks. Rejected: cancelled.
- **Standalone** (``request``): a command needing approval (create a project). Whoever issued it
  reacts to APPROVAL_APPROVED / APPROVAL_REJECTED.

Expiry (D-001): an expired approval is marked EXPIRED, but the task keeps waiting; it is not
cancelled. A new approval can then be requested for the same thing.

Only humans decide. Automatic approval (policy flag) is a PolicyEngine decision, not an approval.

A domain can act on a decision in the same transaction (T-514): ``on_decided(action, hook)``
registers a hook for approvals of that ``action`` (e.g. ``approve_article`` approves or rejects
the article). It runs before the task moves on; if it raises, the decision is not recorded.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Approval, ApprovalKind, ApprovalState, Task, TaskState
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventPayload, new_event
from autora.runtime.lifecycles import APPROVAL_FSM
from autora.runtime.task_manager import Claim, TaskManager

DEFAULT_EXPIRY = timedelta(hours=24)  # D-001

DecisionOutcome = Literal["approve", "reject"]
DecisionHook = Callable[
    [AsyncSession, Approval, DecisionOutcome, Actor, str | None], Awaitable[None]
]


class ApprovalError(Exception):
    pass


@dataclass
class ApprovalService:
    task_manager: TaskManager
    default_expiry: timedelta = DEFAULT_EXPIRY
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    hooks: dict[str, DecisionHook] = field(default_factory=dict)

    def on_decided(self, action: str, hook: DecisionHook) -> None:
        if action in self.hooks:
            raise ApprovalError(f"a decision hook for {action!r} is already registered")
        self.hooks[action] = hook

    # --- requesting ------------------------------------------------------------------------

    async def request_for_run(
        self,
        session: AsyncSession,
        claim: Claim,
        *,
        kind: ApprovalKind,
        action: str,
        payload: dict[str, Any],
        summary: str,
        expires_after: timedelta | None = None,
    ) -> Approval:
        approval = await self._create(
            session,
            company_id=claim.task.company_id,
            kind=kind,
            ref_type="agent_run",
            ref_id=claim.run.id,
            task_id=claim.task.id,
            run_id=claim.run.id,
            action=action,
            payload=payload,
            summary=summary,
            requested_by=Actor.agent(claim.agent.id),
            expires_after=expires_after,
        )
        await self.task_manager.suspend_for_approval(session, claim, approval.id)
        return approval

    async def request_for_task(
        self,
        session: AsyncSession,
        task: Task,
        *,
        kind: ApprovalKind,
        summary: str,
        payload: dict[str, Any] | None = None,
        action: str | None = None,
        requested_by: Actor | None = None,
        expires_after: timedelta | None = None,
    ) -> Approval:
        if task.state != TaskState.READY:
            raise ApprovalError(f"task {task.id} is {task.state}; only READY tasks can await one")
        approval = await self._create(
            session,
            company_id=task.company_id,
            kind=kind,
            ref_type="task",
            ref_id=task.id,
            task_id=task.id,
            run_id=None,
            action=action,
            payload=payload or {},
            summary=summary,
            requested_by=requested_by or Actor.system("workflow_engine"),
            expires_after=expires_after,
        )
        await self.task_manager.await_approval(session, task, approval.id)
        return approval

    async def request(
        self,
        session: AsyncSession,
        *,
        company_id: uuid.UUID,
        kind: ApprovalKind,
        ref_type: str,
        ref_id: uuid.UUID,
        summary: str,
        requested_by: Actor,
        action: str | None = None,
        payload: dict[str, Any] | None = None,
        expires_after: timedelta | None = None,
    ) -> Approval:
        return await self._create(
            session,
            company_id=company_id,
            kind=kind,
            ref_type=ref_type,
            ref_id=ref_id,
            task_id=None,
            run_id=None,
            action=action,
            payload=payload or {},
            summary=summary,
            requested_by=requested_by,
            expires_after=expires_after,
        )

    # --- deciding --------------------------------------------------------------------------

    async def decide(
        self,
        session: AsyncSession,
        approval_id: uuid.UUID,
        *,
        outcome: DecisionOutcome,
        actor: Actor,
        reason: str | None = None,
    ) -> Approval:
        if actor.kind != "human":
            raise ApprovalError("only a human can decide an approval")
        approval = await session.get(Approval, approval_id, with_for_update=True)
        if approval is None:
            raise ApprovalError(f"approval {approval_id} not found")
        if approval.state != ApprovalState.PENDING:
            raise ApprovalError(f"approval {approval_id} is already {approval.state}")

        approved = outcome == "approve"
        approval.decided_by = actor.as_json()
        approval.decided_at = self.clock()
        approval.reason = reason
        target = ApprovalState.APPROVED if approved else ApprovalState.REJECTED
        await APPROVAL_FSM.transition(session, approval, target, actor=actor, reason=reason)
        payload_cls = ev.ApprovalApproved if approved else ev.ApprovalRejected
        await self._emit(
            session,
            approval,
            payload_cls(
                kind=approval.kind,
                ref_type=approval.ref_type,
                ref_id=approval.ref_id,
                reason=reason,
            ),
            actor=actor,
        )
        hook = self.hooks.get(approval.action or "")
        if hook is not None:
            await hook(session, approval, outcome, actor, reason)

        if approval.task_id is None:
            return approval
        task = await session.get(Task, approval.task_id, with_for_update=True)
        if task is None or task.state != TaskState.WAITING_APPROVAL:
            return approval  # the task moved on (cancelled meanwhile): nothing to release
        if not approved:
            await self.task_manager.cancel(
                session, task, reason=f"approval rejected: {reason or 'no reason given'}"
            )
        elif approval.run_id is not None:
            await self.task_manager.release_after_approval(session, task)
        else:
            await self.task_manager.complete_without_run(
                session,
                task,
                {"approval_id": str(approval.id), "approved_by": actor.as_json()},
                output_summary=f"approved by {actor.id}",
            )
        return approval

    async def expire_due(self, session: AsyncSession) -> list[uuid.UUID]:
        """Mark overdue approvals EXPIRED. Their tasks keep waiting (D-001)."""
        now = self.clock()
        due = (
            await session.scalars(
                select(Approval)
                .where(Approval.state == ApprovalState.PENDING, Approval.expires_at <= now)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for approval in due:
            await APPROVAL_FSM.transition(
                session, approval, ApprovalState.EXPIRED, actor=Actor.system("approvals")
            )
            await self._emit(
                session,
                approval,
                ev.ApprovalExpired(
                    kind=approval.kind, ref_type=approval.ref_type, ref_id=approval.ref_id
                ),
                actor=Actor.system("approvals"),
            )
        return [a.id for a in due]

    async def approved_for_run(self, session: AsyncSession, run_id: uuid.UUID) -> list[Approval]:
        """Approvals granted to a run, newest first: what a resumed run is now allowed to do."""
        return list(
            (
                await session.scalars(
                    select(Approval)
                    .where(Approval.run_id == run_id, Approval.state == ApprovalState.APPROVED)
                    .order_by(Approval.decided_at.desc())
                )
            ).all()
        )

    # --- internals -------------------------------------------------------------------------

    async def _create(
        self,
        session: AsyncSession,
        *,
        company_id: uuid.UUID,
        kind: ApprovalKind,
        ref_type: str,
        ref_id: uuid.UUID,
        task_id: uuid.UUID | None,
        run_id: uuid.UUID | None,
        action: str | None,
        payload: dict[str, Any],
        summary: str,
        requested_by: Actor,
        expires_after: timedelta | None,
    ) -> Approval:
        existing = await session.scalar(
            select(Approval).where(
                Approval.ref_type == ref_type,
                Approval.ref_id == ref_id,
                Approval.state == ApprovalState.PENDING,
            )
        )
        if existing is not None:
            raise ApprovalError(f"{ref_type} {ref_id} already has a pending approval")
        approval = Approval(
            company_id=company_id,
            kind=kind.value if isinstance(kind, ApprovalKind) else kind,
            ref_type=ref_type,
            ref_id=ref_id,
            task_id=task_id,
            run_id=run_id,
            action=action,
            payload=payload,
            summary=summary,
            requested_by=requested_by.as_json(),
            state=ApprovalState.PENDING.value,
            expires_at=self.clock() + (expires_after or self.default_expiry),
        )
        session.add(approval)
        await session.flush()
        await self._emit(
            session,
            approval,
            ev.ApprovalRequested(
                kind=approval.kind,
                ref_type=ref_type,
                ref_id=ref_id,
                summary=summary,
                expires_at=approval.expires_at,
            ),
            actor=requested_by,
        )
        return approval

    @staticmethod
    async def _emit(
        session: AsyncSession, approval: Approval, payload: EventPayload, *, actor: Actor
    ) -> None:
        await emit(
            session,
            new_event(
                payload,
                company_id=approval.company_id,
                actor=actor,
                aggregate_type="approval",
                aggregate_id=approval.id,
                task_id=approval.task_id,
                run_id=approval.run_id,
            ),
        )
