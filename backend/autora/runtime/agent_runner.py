"""Agent runner: executes one claimed task as an agent run (T-211, platform/03 §3).

    OBSERVE   first message: task input (+ domain context from the behavior)
    THINK     activity THINKING; one model call (may carry tool calls); step ``think``
    ACT       each tool call: PolicyEngine -> allow: activity WORKING, tool runs, step ``act``
                                            -> needs_approval: run suspended, worker released
                                            -> deny: run ABORTED(policy)
    EVALUATE  activity REVIEWING{evaluate}; schema (gateway), then validators; step ``evaluate``
    REPAIR    issues fed back to the model, activity REVIEWING{repair}; step ``repair``
    FINISH    COMPLETED (task manager computes the hand-off) / FAILED / ABORTED

Transactions are short and many: one per activity change, per step, per policy decision. Model
calls and tool executions happen outside them (a tool call would otherwise hold the company's
event lock for its whole duration). The trade-off is that a crash mid-run leaves a partial trace;
the lease reaper then re-queues the task and a new attempt re-runs from the start (03 §5).

Resuming after an approval: the run's conversation is not in memory any more (the worker was
released, maybe restarted). Every step blob therefore stores ``messages_after``, the whole
conversation at that point, and the resumed run continues from the last one. The approved tool
call is identified by its ``tool_call_id`` in the approval payload.

Everything that ends a run goes through the TaskManager, which owns the task/run FSMs, the
lease check (``LeaseLost``: a reaped worker cannot finish anything) and the final activity.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.db.models import AgentRun, AgentRunState, AgentStep, ApprovalKind, StepKind
from autora.db.repositories.companies import get_policies
from autora.infra.blobstore import BlobStore
from autora.runtime.activity import set_activity
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from autora.runtime.behaviors import AgentBehavior, BehaviorRegistry, RunContext
from autora.runtime.cost.guard import BudgetExceeded
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.lifecycles import AGENT_RUN_FSM
from autora.runtime.models.gateway import ModelCallFailed, ModelGateway
from autora.runtime.models.types import (
    CallContext,
    Message,
    ModelRequest,
    ModelResponse,
    ToolResultBlock,
    ToolUseBlock,
)
from autora.runtime.policy import PolicyEngine
from autora.runtime.progress import ProgressPublisher
from autora.runtime.task_manager import Claim, LeaseLost, TaskManager
from autora.runtime.tools import ToolRegistry, UnknownTool
from autora.runtime.trace.recorder import record_step

RunStatus = Literal["completed", "failed", "aborted", "suspended", "lost"]
_SUMMARY = 200
_MODEL_CALL_KINDS = (StepKind.THINK.value, StepKind.REPAIR.value)


@dataclass(frozen=True)
class RunOutcome:
    status: RunStatus
    run_id: uuid.UUID
    error_class: str | None = None
    message: str | None = None
    will_retry: bool = False
    """failed only: the task manager re-queued the task for another attempt."""


@dataclass
class _State:
    claim: Claim
    behavior: AgentBehavior
    ctx: RunContext
    policies: dict[str, Any]
    tool_names: list[str]
    system: str
    prompt_hash: str
    messages: list[Message]
    next_seq: int
    model_calls: int = 0
    repairs_used: int = 0
    eval_round: int = 0
    pending: list[ToolUseBlock] = field(default_factory=list)
    """Tool calls of the last assistant turn still to run (resumed run)."""
    results: list[ToolResultBlock] = field(default_factory=list)
    """Results collected so far for the current assistant turn."""
    approved: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Approved tool calls by tool_call_id (payload of the approval)."""
    repairing: bool = False
    tokens: int = 0
    """Tokens used by the run so far (reported live, T-304)."""

    @property
    def agent_actor(self) -> Actor:
        return Actor.agent(self.claim.agent.id)


@dataclass
class AgentRunner:
    session_factory: async_sessionmaker[AsyncSession]
    task_manager: TaskManager
    gateway: ModelGateway
    tools: ToolRegistry
    policy: PolicyEngine
    approvals: ApprovalService
    blobs: BlobStore
    behaviors: BehaviorRegistry
    progress: ProgressPublisher | None = None
    """Live AGENT_STEP_PROGRESS (ephemeral). None: no live progress."""
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    actor: Actor = field(default_factory=lambda: Actor.system("agent_runner"))

    async def run(self, claim: Claim) -> RunOutcome:
        """Run ``claim`` to an end state. Never raises for the work itself; ``LeaseLost``
        (the lease was reaped meanwhile) ends the run without writing anything more."""
        try:
            state = await self._start(claim)
            return await self._loop(state)
        except LeaseLost as exc:
            return RunOutcome("lost", claim.run.id, "LeaseLost", str(exc))
        except BudgetExceeded as exc:
            async with self.session_factory() as session:
                await self.task_manager.abort(session, claim, reason="budget", message=str(exc))
                await session.commit()
            return RunOutcome("aborted", claim.run.id, "BudgetExceeded", str(exc))
        except ModelCallFailed as exc:
            return await self._fail(claim, "ProviderError", str(exc.last), retryable=True)
        except Exception as exc:  # noqa: BLE001 - a bug (or misconfiguration) must not take
            # the worker down: the attempt is consumed and the failure is visible in the trace.
            return await self._fail(claim, type(exc).__name__, str(exc), retryable=True)
        finally:
            if self.progress is not None:
                await self.progress.finish(claim.run.id)

    # --- start / resume --------------------------------------------------------------------

    async def _start(self, claim: Claim) -> _State:
        task, agent = claim.task, claim.agent
        behavior = self.behaviors.resolve(agent.role, task.name)
        ctx = RunContext(
            company_id=task.company_id,
            project_id=task.project_id,
            task=task,
            agent=agent,
            run_id=claim.run.id,
        )
        async with self.session_factory() as session:
            await self.task_manager.heartbeat(session, claim)
            run = await session.get(AgentRun, claim.run.id, with_for_update=True)
            policies = await get_policies(session, task.company_id)
            state = _State(
                claim=claim,
                behavior=behavior,
                ctx=ctx,
                policies=policies,
                tool_names=[self.tools.get(t).name for t in behavior.offered_tools(agent)],
                system=behavior.system_prompt,
                prompt_hash="",
                messages=[],
                next_seq=run.steps_count,
                tokens=(run.tokens_in or 0) + (run.tokens_out or 0),
            )
            if claim.resumed:
                await self._resume(session, state, run)
            else:
                await AGENT_RUN_FSM.transition(
                    session, run, AgentRunState.RUNNING, actor=self.actor
                )
                run.started_at = self.clock()
                observe = await self._observe(session, state)
                state.messages = [Message.user(observe)]
                await self._emit(
                    session,
                    state,
                    ev.AgentRunStarted(
                        attempt=run.attempt,
                        task_name=task.name,
                        required_role=task.required_role,
                        input_summary=_truncate(json.dumps(task.input, ensure_ascii=False)),
                    ),
                )
            state.prompt_hash = hashlib.sha256(
                f"{behavior.prompt_version}\n{state.system}\n{state.messages[0].content}".encode()
            ).hexdigest()
            await session.commit()
        return state

    async def _observe(self, session: AsyncSession, state: _State) -> str:
        task = state.ctx.task
        parts = [
            f"Task: {task.display_name} ({task.name})",
            "Input:",
            json.dumps(task.input, ensure_ascii=False, indent=2),
        ]
        if state.behavior.context is not None:
            extra = await state.behavior.context(session, state.ctx)
            if extra:
                parts += ["", extra]
        return "\n".join(parts)

    async def _resume(self, session: AsyncSession, state: _State, run: AgentRun) -> None:
        await AGENT_RUN_FSM.transition(session, run, AgentRunState.RUNNING, actor=self.actor)
        last = await session.scalar(
            select(AgentStep)
            .where(AgentStep.run_id == run.id, AgentStep.blob_key.is_not(None))
            .order_by(AgentStep.seq.desc())
            .limit(1)
        )
        if last is None:
            raise RuntimeError(f"run {run.id} resumed without any recorded step")
        stored = json.loads(await self.blobs.get(last.blob_key))
        messages = [Message.model_validate(m) for m in stored["messages_after"]]

        # The last assistant turn's tool calls without a result are still to be run.
        results: list[ToolResultBlock] = []
        if (
            messages
            and messages[-1].role == "user"
            and any(isinstance(b, ToolResultBlock) for b in messages[-1].content)
        ):
            results = [b for b in messages.pop().content if isinstance(b, ToolResultBlock)]
        done = {r.tool_use_id for r in results}
        if messages and messages[-1].role == "assistant":
            uses = [b for b in messages[-1].content if isinstance(b, ToolUseBlock)]
            state.pending = [u for u in uses if u.id not in done]
        state.messages, state.results = messages, results

        for approval in await self.approvals.approved_for_run(session, run.id):
            call_id = (approval.payload or {}).get("tool_call_id")
            if call_id:
                state.approved[call_id] = approval.payload
        kinds = (
            await session.execute(
                select(AgentStep.kind, func.count())
                .where(AgentStep.run_id == run.id)
                .group_by(AgentStep.kind)
            )
        ).all()
        counts = dict(kinds)
        state.model_calls = sum(counts.get(k, 0) for k in _MODEL_CALL_KINDS)
        state.repairs_used = counts.get(StepKind.REPAIR.value, 0)
        state.eval_round = counts.get(StepKind.EVALUATE.value, 0)

    # --- the loop --------------------------------------------------------------------------

    async def _loop(self, state: _State) -> RunOutcome:
        while True:
            if state.pending:
                outcome = await self._act(state, state.pending)
                if outcome is not None:
                    return outcome
                continue

            if state.model_calls >= state.behavior.max_steps:
                return await self._fail(
                    state.claim,
                    "MaxStepsExceeded",
                    f"{state.model_calls} model calls without a final answer",
                    retryable=True,
                )
            response = await self._call_model(state)

            if response.stop_reason == "tool_use" and response.tool_uses:
                outcome = await self._act(state, response.tool_uses)
                if outcome is not None:
                    return outcome
                continue

            if response.stop_reason == "refusal":
                return await self._fail(
                    state.claim,
                    "ModelRefusal",
                    _truncate(response.text) or "refused",
                    retryable=False,
                )
            issues = list(response.output_issues)
            if response.stop_reason == "max_tokens":
                issues.append("the reply was cut off (max_tokens); reply with the complete output")
            if response.parsed is None and not issues:
                issues.append("no structured output in the reply")
            outcome = await self._evaluate(state, response, issues)
            if outcome is not None:
                return outcome

    async def _call_model(self, state: _State) -> ModelResponse:
        claim, behavior, task = state.claim, state.behavior, state.ctx.task
        kind = StepKind.REPAIR if state.repairing else StepKind.THINK
        async with self.session_factory() as session:
            await self.task_manager.heartbeat(session, claim)
            if not state.repairing:  # a repair call keeps the REVIEWING{repair} activity
                phase = "plan" if state.model_calls == 0 else "reason"
                await self._activity(
                    session, state, ev.AgentThinking(phase=phase, step_seq=state.next_seq)
                )
            await session.commit()

        request = ModelRequest(
            capability=behavior.capability,
            context=CallContext(
                company_id=task.company_id,
                role=claim.agent.role,
                project_id=task.project_id,
                agent_id=claim.agent.id,
                run_id=claim.run.id,
                task_id=task.id,
                task_name=task.name,
                attempt=task.attempt,
                step_seq=state.next_seq,
            ),
            messages=list(state.messages),
            system=state.system,
            tools=self.tools.definitions(state.tool_names),
            output_model=behavior.output_model,
            max_output_tokens=behavior.max_output_tokens,
        )
        response = await self.gateway.complete(request)
        state.model_calls += 1
        state.repairing = False
        state.messages.append(Message(role="assistant", content=response.content))
        state.results = []

        async with self.session_factory() as session:
            await self._record(
                session,
                state,
                kind=kind,
                summary=_truncate(response.text) or f"{len(response.tool_uses)} tool call(s)",
                tool_calls=[
                    {"id": u.id, "name": u.name, "input": u.input} for u in response.tool_uses
                ]
                or None,
                cost=response.cost_usd,
                payload={
                    "request": {
                        "system": state.system,
                        "messages": _dump(request.messages),
                        "tools": state.tool_names,
                    },
                    "response": {
                        "content": _dump(response.content),
                        "stop_reason": response.stop_reason,
                        "alias": response.alias,
                        "model_id": response.model_id,
                        "usage": response.usage.model_dump(),
                        "output_issues": response.output_issues,
                    },
                },
                usage=(response.usage.input_tokens, response.usage.output_tokens),
            )
            await session.commit()
        state.tokens += response.usage.input_tokens + response.usage.output_tokens
        await self._report(state, state.next_seq - 1)
        return response

    async def _act(self, state: _State, uses: list[ToolUseBlock]) -> RunOutcome | None:
        for index, use in enumerate(uses):
            result = await self._one_tool(state, use)
            if isinstance(result, RunOutcome):
                state.pending = uses[index:]
                return result
            state.results.append(result)
        state.pending = []
        state.messages.append(Message(role="user", content=list(state.results)))
        state.results = []
        return None

    async def _one_tool(self, state: _State, use: ToolUseBlock) -> ToolResultBlock | RunOutcome:
        claim, task, agent = state.claim, state.ctx.task, state.claim.agent
        if use.name not in state.tool_names:
            return ToolResultBlock(
                tool_use_id=use.id,
                content=f"unknown tool {use.name!r}; available: {', '.join(state.tool_names)}",
                is_error=True,
            )

        if use.id not in state.approved:
            async with self.session_factory() as session:
                await self.task_manager.heartbeat(session, claim)
                facts = {}
                if state.behavior.policy_facts is not None:
                    facts = await state.behavior.policy_facts(
                        session, state.ctx, use.name, use.input
                    )
                decision = await self.policy.decide_and_record(
                    session,
                    state.agent_actor,
                    use.name,
                    company_id=task.company_id,
                    role=agent.role,
                    args=use.input,
                    facts=facts,
                    company_policies=state.policies,
                    agent_id=agent.id,
                    run_id=claim.run.id,
                    task_id=task.id,
                )
                if decision.outcome == "deny":
                    message = f"{use.name}: {decision.reason}"
                    await self.task_manager.abort(session, claim, reason="policy", message=message)
                    await session.commit()
                    return RunOutcome("aborted", claim.run.id, "PolicyDenied", message)
                if decision.outcome == "needs_approval":
                    await self._record(
                        session,
                        state,
                        kind=StepKind.OBSERVE,
                        summary=f"waiting for approval: {use.name}",
                        tool_calls=[{"id": use.id, "name": use.name, "input": use.input}],
                        payload={"pending_tool_call_id": use.id, "policy": decision.reason},
                    )
                    approval = await self.approvals.request_for_run(
                        session,
                        claim,
                        kind=ApprovalKind.TOOL_CALL,
                        action=use.name,
                        payload={
                            "tool": use.name,
                            "args": use.input,
                            "tool_call_id": use.id,
                            "step_seq": state.next_seq,
                        },
                        summary=_truncate(
                            f"{agent.display_name} wants to {use.name} "
                            f"{json.dumps(use.input, ensure_ascii=False)}"
                        ),
                    )
                    await session.commit()
                    return RunOutcome(
                        "suspended", claim.run.id, "NeedsApproval", f"approval {approval.id}"
                    )
                await session.commit()

        step_seq = state.next_seq

        async def working(session: AsyncSession) -> None:
            await self.task_manager.heartbeat(session, claim)
            await self._activity(
                session,
                state,
                ev.AgentWorking(tool=use.name, tool_call_id=use.id, step_seq=step_seq),
            )

        try:
            invocation = await self.tools.invoke(
                use.name,
                use.input,
                company_id=task.company_id,
                actor=state.agent_actor,
                tool_call_id=use.id,
                agent_id=agent.id,
                run_id=claim.run.id,
                task_id=task.id,
                workflow_run_id=task.workflow_run_id,
                step_seq=step_seq,
                before_call=working,
            )
        except UnknownTool as exc:
            return ToolResultBlock(tool_use_id=use.id, content=str(exc), is_error=True)

        if invocation.ok:
            content = json.dumps(invocation.output, ensure_ascii=False, default=str)
        else:
            content = f"{invocation.error_class}: {invocation.message}"
        result = ToolResultBlock(tool_use_id=use.id, content=content, is_error=not invocation.ok)

        async with self.session_factory() as session:
            await self._record(
                session,
                state,
                kind=StepKind.ACT,
                summary=_truncate(f"{use.name}: {'ok' if invocation.ok else content}"),
                tool_calls=[
                    {
                        "id": use.id,
                        "name": use.name,
                        "ok": invocation.ok,
                        "error_class": invocation.error_class,
                        "duration_ms": invocation.duration_ms,
                    }
                ],
                cost=invocation.cost_usd or Decimal(0),
                payload={
                    "tool": use.name,
                    "args": use.input,
                    "ok": invocation.ok,
                    "output": invocation.output,
                    "error": invocation.message,
                    "produced": [p.model_dump(mode="json") for p in invocation.produced],
                },
                extra_result=result,
            )
            await session.commit()
        await self._report(state, state.next_seq - 1, invocation.progress)
        return result

    async def _evaluate(
        self, state: _State, response: ModelResponse, issues: list[str]
    ) -> RunOutcome | None:
        claim, behavior = state.claim, state.behavior
        state.eval_round += 1
        parsed = response.parsed
        async with self.session_factory() as session:
            await self.task_manager.heartbeat(session, claim)
            if parsed is not None and not issues:
                for validator in behavior.validators:
                    issues.extend(await validator(session, state.ctx, parsed))
            passed = not issues
            await self._activity(
                session,
                state,
                ev.AgentReviewing(
                    phase="evaluate", attempt=state.eval_round, issues_count=len(issues)
                ),
            )
            await self._record(
                session,
                state,
                kind=StepKind.EVALUATE,
                summary="passed" if passed else _truncate("; ".join(issues)),
                payload={"passed": passed, "issues": issues, "round": state.eval_round},
            )
            run = await session.get(AgentRun, claim.run.id)
            run.evaluation = {"passed": passed, "issues": issues, "round": state.eval_round}
            await AGENT_RUN_FSM.transition(session, run, AgentRunState.EVALUATING, actor=self.actor)

            if passed:
                assert parsed is not None
                output = parsed.model_dump(mode="json")
                summary = (
                    behavior.summarize(parsed) if behavior.summarize else _truncate(response.text)
                )
                await self.task_manager.succeed(session, claim, output, output_summary=summary)
                await session.commit()
                return RunOutcome("completed", claim.run.id)

            if state.repairs_used < behavior.repair_limit:
                state.repairs_used += 1
                for hop in (AgentRunState.REPAIRING, AgentRunState.RUNNING):
                    await AGENT_RUN_FSM.transition(session, run, hop, actor=self.actor)
                await self._activity(
                    session,
                    state,
                    ev.AgentReviewing(
                        phase="repair", attempt=state.repairs_used, issues_count=len(issues)
                    ),
                )
                await session.commit()
                state.messages.append(Message.user(_repair_prompt(issues)))
                state.repairing = True
                return None

            message = _truncate("; ".join(issues), 2000)
            retry = await self.task_manager.fail(
                session, claim, error_class="EvaluationFailed", message=message, retryable=True
            )
            await session.commit()
            return RunOutcome("failed", claim.run.id, "EvaluationFailed", message, retry)

    # --- endings and helpers ---------------------------------------------------------------

    async def _fail(
        self, claim: Claim, error_class: str, message: str, *, retryable: bool
    ) -> RunOutcome:
        try:
            async with self.session_factory() as session:
                retry = await self.task_manager.fail(
                    session, claim, error_class=error_class, message=message, retryable=retryable
                )
                await session.commit()
        except LeaseLost as exc:
            return RunOutcome("lost", claim.run.id, "LeaseLost", str(exc))
        return RunOutcome("failed", claim.run.id, error_class, message, retry)

    async def _record(
        self,
        session: AsyncSession,
        state: _State,
        *,
        kind: StepKind,
        summary: str,
        payload: dict[str, Any],
        tool_calls: list[dict[str, Any]] | None = None,
        cost: Decimal = Decimal(0),
        usage: tuple[int, int] | None = None,
        extra_result: ToolResultBlock | None = None,
    ) -> None:
        """Append a step with the conversation so far (``messages_after``) and charge the run."""
        await self.task_manager.heartbeat(session, state.claim)
        results = [*state.results, extra_result] if extra_result else list(state.results)
        after = (
            [*state.messages, Message(role="user", content=results)] if results else state.messages
        )
        step = await record_step(
            session,
            self.blobs,
            state.claim.run.id,
            kind=kind,
            summary=summary,
            tool_calls=tool_calls,
            prompt_hash=state.prompt_hash,
            cost_usd=cost,
            payload={**payload, "messages_after": _dump(after)},
        )
        state.next_seq = step.seq + 1
        run = await session.get(AgentRun, state.claim.run.id)
        run.cost_usd = (run.cost_usd or Decimal(0)) + cost
        if usage is not None:
            run.tokens_in = (run.tokens_in or 0) + usage[0]
            run.tokens_out = (run.tokens_out or 0) + usage[1]

    async def _report(
        self, state: _State, step_seq: int, progress: ev.Progress | None = None
    ) -> None:
        """Live progress after a step (ephemeral, rate-limited, best-effort)."""
        if self.progress is None:
            return
        task, run = state.ctx.task, state.claim.run
        envelope = new_event(
            ev.AgentStepProgress(step_seq=step_seq, tokens_so_far=state.tokens, progress=progress),
            company_id=task.company_id,
            actor=self.actor,
            aggregate_type="agent_run",
            aggregate_id=run.id,
            agent_id=run.agent_id,
            task_id=task.id,
            run_id=run.id,
            workflow_run_id=task.workflow_run_id,
        )
        await self.progress.report(run.id, envelope)

    async def _activity(self, session: AsyncSession, state: _State, payload: Any) -> None:
        task = state.ctx.task
        await set_activity(
            session,
            state.claim.agent,
            payload,
            actor=self.actor,
            run_id=state.claim.run.id,
            task_id=task.id,
            workflow_run_id=task.workflow_run_id,
            task_name=task.display_name,
            links=await self.task_manager.activity_links(session, task, state.claim.run),
        )

    async def _emit(self, session: AsyncSession, state: _State, payload: Any) -> None:
        task, run = state.ctx.task, state.claim.run
        await emit(
            session,
            new_event(
                payload,
                company_id=task.company_id,
                actor=self.actor,
                aggregate_type="agent_run",
                aggregate_id=run.id,
                agent_id=run.agent_id,
                task_id=task.id,
                run_id=run.id,
                workflow_run_id=task.workflow_run_id,
                cycle_id=task.cycle_id,
                correlation_id=task.workflow_run_id,
            ),
        )


def _repair_prompt(issues: list[str]) -> str:
    lines = "\n".join(f"- {issue}" for issue in issues)
    return (
        "Your previous reply did not pass evaluation. Issues:\n"
        f"{lines}\n"
        "Reply again with the complete, corrected output."
    )


def _dump(items: list[BaseModel]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]


def _truncate(text: str, limit: int = _SUMMARY) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
