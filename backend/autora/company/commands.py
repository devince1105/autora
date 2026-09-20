"""The command pipeline: the only way anything changes the company (platform/06 §2, T-604).

    submit(name, payload)
      -> parse       the payload becomes a typed command, or it is refused before anything runs
      -> replay      a key already used returns what it did the first time
      -> decide      the PolicyEngine says allow / deny / needs approval / limited
      -> check       the handler's own invariants: a project must be ACTIVE, money non-negative
      -> mutate      one transaction: state, the command record, and the events it caused
      -> record      what was asked, who asked, what was decided, what happened

Agents have no write tool for projects, budgets or transactions; a person's button goes through
the same pipeline with ``actor=human``. So there is exactly one place where "who may change
what" is answered, and exactly one place to read afterwards what the company tried to do.

**A refusal is a result, not an error.** It is recorded like any other outcome: a company that
denies its CEO a budget increase has done something worth seeing, and losing that in an
exception trace would hide the most interesting decisions.

**Approval does not mean "do it now".** A command a person must approve is recorded as waiting
and its payload is kept on the approval; when the person approves, the same handler runs under
the same key. Nothing is executed twice, because the key is still the key.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import (
    Approval,
    ApprovalKind,
    CommandOutcome,
    CommandRecord,
    EventRecord,
)
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from autora.runtime.policy import PolicyEngine

log = logging.getLogger(__name__)

APPROVAL_ACTION = "command"
"""Every command awaiting a person shares one approval action, so one hook runs them all."""


class CommandError(Exception):
    pass


class UnknownCommand(CommandError):
    pass


class Refused(CommandError):
    """A handler saying the company's state does not allow this: a project that is not ACTIVE,
    a budget that would go negative. Recorded as a refusal, not raised at the caller."""


@dataclass(frozen=True)
class Context:
    """What a handler is given: the company, who asked, and the decision that let it through."""

    session: AsyncSession
    company_id: uuid.UUID
    actor: Actor
    role: str | None
    limit: str | None
    """The cap the policy applied, when it answered ``limited``."""
    policy: PolicyEngine | None = None
    workflows: Any = None
    """The workflow engine, for the verbs that start work. None in a bus built without one —
    those verbs then refuse rather than pretending to have started something."""


Handler = Callable[[Context, BaseModel], Awaitable[Mapping[str, Any]]]
"""Performs the command and returns what it did. Raising ``Refused`` from inside is how a
handler reports that the company's state does not allow it (a project that is not ACTIVE).
Anything it emits is captured into the command's record."""

FactsFor = Callable[[AsyncSession, uuid.UUID, BaseModel], Awaitable[Mapping[str, Any]]]
"""Facts the policy needs that only a query can answer: how many workflows this cycle already
started, what is left of a budget."""


@dataclass(frozen=True)
class CommandSpec:
    name: str
    model: type[BaseModel]
    action: str
    """The policy action this command is an instance of (``allocate_budget``)."""
    handler: Handler
    facts: FactsFor | None = None
    summary: Callable[[BaseModel], str] | None = None
    """One line for the person who has to approve it. They should not have to read JSON."""


class Invalid(CommandError):
    """The payload is not a command of that name."""


@dataclass
class CommandResult:
    record: CommandRecord
    approval: Approval | None = None

    @property
    def done(self) -> bool:
        return self.record.outcome == CommandOutcome.DONE.value

    @property
    def awaiting(self) -> bool:
        return self.record.outcome == CommandOutcome.AWAITING_APPROVAL.value

    @property
    def result(self) -> dict[str, Any]:
        return self.record.result or {}


@dataclass
class CommandBus:
    """Holds the commands a company can be asked to perform. Does not commit."""

    policy: PolicyEngine
    approvals: ApprovalService
    workflows: Any = None
    """The workflow engine the ``InstantiateWorkflow`` verb needs."""
    specs: dict[str, CommandSpec] = field(default_factory=dict)

    def register(self, spec: CommandSpec) -> None:
        if spec.name in self.specs:
            raise CommandError(f"{spec.name} is already registered")
        self.specs[spec.name] = spec

    def get(self, name: str) -> CommandSpec:
        try:
            return self.specs[name]
        except KeyError:
            known = ", ".join(sorted(self.specs)) or "none"
            raise UnknownCommand(f"no command {name!r} (known: {known})") from None

    def _context(self, session, company_id, actor, role, limit) -> Context:
        return Context(
            session=session,
            company_id=company_id,
            actor=actor,
            role=role,
            limit=limit,
            policy=self.policy,
            workflows=self.workflows,
        )

    def install(self) -> None:
        """Let an approved command run when the person approves it."""
        self.approvals.on_decided(APPROVAL_ACTION, self._on_decided)

    # --- submitting ---------------------------------------------------------------------------

    async def submit(
        self,
        session: AsyncSession,
        name: str,
        payload: Mapping[str, Any] | BaseModel,
        *,
        company_id: uuid.UUID,
        actor: Actor,
        idempotency_key: str,
        role: str | None = None,
        company_policies: Mapping[str, Any] | None = None,
        task_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> CommandResult:
        """Ask the company to do something. Does not commit.

        Returns the outcome rather than raising for a refusal: refusing is one of the things
        this pipeline is for, and the caller usually wants to show it, not handle it.
        """
        spec = self.get(name)
        replay = await self._replay(session, idempotency_key)
        if replay is not None:
            return replay

        try:
            command = (
                payload
                if isinstance(payload, spec.model)
                else spec.model.model_validate(dict(payload))
            )
        except ValidationError as exc:
            return CommandResult(
                await self._record(
                    session,
                    spec,
                    dict(payload) if not isinstance(payload, BaseModel) else payload.model_dump(),
                    company_id=company_id,
                    actor=actor,
                    role=role,
                    decision="deny",
                    outcome=CommandOutcome.REFUSED,
                    reason=_why(exc),
                    idempotency_key=idempotency_key,
                    task_id=task_id,
                    run_id=run_id,
                )
            )

        args = command.model_dump(mode="json")
        facts = await spec.facts(session, company_id, command) if spec.facts is not None else {}
        decision = await self.policy.decide_and_record(
            session,
            actor,
            spec.action,
            company_id=company_id,
            role=role,
            args=args,
            facts=facts,
            company_policies=company_policies,
        )

        if decision.outcome == "deny":
            return CommandResult(
                await self._record(
                    session,
                    spec,
                    args,
                    company_id=company_id,
                    actor=actor,
                    role=role,
                    decision=decision.outcome,
                    outcome=CommandOutcome.REFUSED,
                    reason=decision.reason,
                    idempotency_key=idempotency_key,
                    task_id=task_id,
                    run_id=run_id,
                )  # fmt: skip
            )

        if decision.outcome == "needs_approval":
            record = await self._record(
                session, spec, args, company_id=company_id, actor=actor, role=role,
                decision=decision.outcome, outcome=CommandOutcome.AWAITING_APPROVAL,
                reason=decision.reason, idempotency_key=idempotency_key,
                task_id=task_id, run_id=run_id,
            )  # fmt: skip
            approval = await self.approvals.request(
                session,
                company_id=company_id,
                kind=ApprovalKind.COMMAND,
                ref_type="command",
                ref_id=record.id,
                action=APPROVAL_ACTION,
                summary=spec.summary(command) if spec.summary else f"{spec.name}",
                requested_by=actor,
                payload={
                    "command": spec.name,
                    "args": args,
                    "role": role,
                    "idempotency_key": idempotency_key,
                },
            )
            record.approval_id = approval.id
            await session.flush()
            return CommandResult(record, approval)

        return CommandResult(
            await self._run(
                session,
                spec,
                command,
                args,
                company_id=company_id,
                actor=actor,
                role=role,
                decision=decision.outcome,
                limit=decision.reason if decision.outcome == "limited" else None,
                idempotency_key=idempotency_key,
                task_id=task_id,
                run_id=run_id,
            )  # fmt: skip
        )

    # --- running ------------------------------------------------------------------------------

    async def _run(
        self,
        session: AsyncSession,
        spec: CommandSpec,
        command: BaseModel,
        args: dict[str, Any],
        *,
        company_id: uuid.UUID,
        actor: Actor,
        role: str | None,
        decision: str,
        limit: str | None,
        idempotency_key: str,
        approval_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> CommandRecord:
        context = self._context(session, company_id, actor, role, limit)
        mark = await _last_seq(session, company_id)
        try:
            result = await spec.handler(context, command)
        except CommandError as refusal:
            return await self._record(
                session, spec, args, company_id=company_id, actor=actor, role=role,
                decision=decision, outcome=CommandOutcome.REFUSED, reason=str(refusal),
                idempotency_key=idempotency_key, approval_id=approval_id,
                task_id=task_id, run_id=run_id,
            )  # fmt: skip
        caused = await _events_since(session, company_id, mark)
        return await self._record(
            session, spec, args, company_id=company_id, actor=actor, role=role,
            decision=decision, outcome=CommandOutcome.DONE, reason=limit,
            result=dict(result), event_ids=caused, idempotency_key=idempotency_key,
            approval_id=approval_id, task_id=task_id, run_id=run_id,
        )  # fmt: skip

    async def _on_decided(
        self,
        session: AsyncSession,
        approval: Approval,
        outcome: str,
        actor: Actor,
        reason: str | None,
    ) -> None:
        """A person decided on a command. Approved, it runs now — under its original key.

        Runs inside the decision's transaction, so a handler that refuses does not undo the
        decision: the person did decide, and what the company could not do afterwards is
        recorded on the command, where the question will be asked.
        """
        payload = approval.payload or {}
        record = await session.scalar(
            select(CommandRecord).where(CommandRecord.approval_id == approval.id)
        )
        if record is None:  # nothing to run: the approval was not made by this pipeline
            return
        if outcome != "approve":
            record.outcome = CommandOutcome.REFUSED.value
            record.reason = f"a person said no: {reason or 'no reason given'}"
            await session.flush()
            return
        spec = self.get(payload["command"])
        command = spec.model.model_validate(payload["args"])
        context = self._context(
            session,
            approval.company_id,
            actor,
            payload.get("role"),
            None,
        )
        mark = await _last_seq(session, approval.company_id)
        try:
            result = await spec.handler(context, command)
        except CommandError as refusal:
            # approved, but the company moved on: the project was killed while it waited
            record.outcome = CommandOutcome.REFUSED.value
            record.reason = str(refusal)
            await session.flush()
            return
        record.outcome = CommandOutcome.DONE.value
        record.result = dict(result)
        record.event_ids = await _events_since(session, approval.company_id, mark)
        await session.flush()

    # --- recording ----------------------------------------------------------------------------

    async def _replay(self, session: AsyncSession, idempotency_key: str) -> CommandResult | None:
        record = await session.scalar(
            select(CommandRecord).where(CommandRecord.idempotency_key == idempotency_key)
        )
        if record is None:
            return None
        approval = await session.get(Approval, record.approval_id) if record.approval_id else None
        return CommandResult(record, approval)

    async def _record(
        self,
        session: AsyncSession,
        spec: CommandSpec,
        args: dict[str, Any],
        *,
        company_id: uuid.UUID,
        actor: Actor,
        role: str | None,
        decision: str,
        outcome: CommandOutcome,
        idempotency_key: str,
        reason: str | None = None,
        result: dict[str, Any] | None = None,
        event_ids: list[uuid.UUID] | None = None,
        approval_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> CommandRecord:
        record = CommandRecord(
            company_id=company_id,
            command=spec.name,
            actor=actor.as_json(),
            role=role,
            payload=args,
            decision=decision,
            outcome=outcome.value,
            reason=(reason or None) and reason[:2000],
            result=result,
            event_ids=event_ids or [],
            approval_id=approval_id,
            task_id=task_id,
            run_id=run_id,
            idempotency_key=idempotency_key,
        )
        session.add(record)
        await session.flush()
        return record


async def _last_seq(session: AsyncSession, company_id: uuid.UUID) -> int:
    """The company's latest event, so a handler's own can be told apart afterwards by
    sequence rather than by comparing every id it ever had."""
    await session.flush()
    seq = await session.scalar(
        select(func.max(EventRecord.seq)).where(EventRecord.company_id == company_id)
    )
    return int(seq or 0)


async def _events_since(session: AsyncSession, company_id: uuid.UUID, mark: int) -> list[uuid.UUID]:
    await session.flush()
    rows = await session.scalars(
        select(EventRecord.id)
        .where(EventRecord.company_id == company_id, EventRecord.seq > mark)
        .order_by(EventRecord.seq)
    )
    return list(rows)


def _why(exc: ValidationError) -> str:
    problems = [
        f"{'.'.join(str(p) for p in error['loc']) or 'payload'}: {error['msg']}"
        for error in exc.errors()[:5]
    ]
    return "; ".join(problems)
