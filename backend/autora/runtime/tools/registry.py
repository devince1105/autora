"""Tool registry: an agent's hands (T-204, logs/platform/03_AGENT_RUNTIME.md §1).

A tool is an async function ``fn(args: <pydantic model>, ctx: ToolContext) -> ToolResult``
registered with its side-effect level. ``invoke`` validates arguments, runs the tool with a
timeout, and records the call as a TOOL_CALLED / TOOL_COMPLETED | TOOL_FAILED event pair.

Transaction layout (why it is not one transaction):
``emit`` holds the company's event lock until commit. If TOOL_CALLED shared a transaction with a
30-second fetch, every event writer of that company would stall behind it. So:

1. TOOL_CALLED commits on its own (plus an optional ``before_call`` hook in the same transaction,
   which AgentRunner uses to set activity = WORKING atomically with it).
2. The tool runs in a second transaction. Its domain writes and TOOL_COMPLETED commit together:
   either the evidence row and the event both exist, or neither does.
3. On any failure that transaction is rolled back and TOOL_FAILED commits separately.

Policy decisions (ALLOW / DENY / NEEDS_APPROVAL) happen before ``invoke`` in the PolicyEngine
(T-205); the registry only exposes the metadata policy needs (``side_effect``).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, get_type_hints

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventPayload, new_event

SideEffect = Literal["read", "write", "irreversible"]
_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")
_SUMMARY_LIMIT = 200


class InvalidToolDefinition(Exception):
    pass


class UnknownTool(Exception):
    def __init__(self, name: str, known: Sequence[str]):
        self.name = name
        super().__init__(f"unknown tool {name!r}; registered: {', '.join(sorted(known)) or 'none'}")


@dataclass(frozen=True)
class ToolContext:
    """Everything a tool may know about the call. ``session`` is the tool's transaction."""

    session: AsyncSession
    company_id: uuid.UUID
    actor: Actor
    tool_call_id: str
    idempotency_key: str
    """Stable across retries of the same call; write tools must use it to avoid duplicates."""
    agent_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
    step_seq: int = 0


class ToolResult(BaseModel):
    output: dict[str, Any] = {}
    summary: str | None = None
    produced: list[ev.ProducedRef] = []
    cost_usd: Decimal | None = None
    progress: ev.Progress | None = None
    """Optional progress of the agent's job after this call, e.g. sources 12/40 (T-304)."""


ToolFn = Callable[[Any, ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    side_effect: SideEffect
    timeout_s: float
    retryable: bool
    fn: ToolFn

    def definition(self) -> dict[str, Any]:
        """Provider-neutral tool definition for the model gateway."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


@dataclass(frozen=True)
class ToolInvocation:
    tool: str
    tool_call_id: str
    ok: bool
    output: dict[str, Any] | None
    error_class: str | None
    message: str | None
    will_retry: bool
    duration_ms: int
    produced: list[ev.ProducedRef] = field(default_factory=list)
    cost_usd: Decimal | None = None
    progress: ev.Progress | None = None


class _ToolError(Exception):
    def __init__(self, error_class: str, message: str, retryable: bool):
        self.error_class = error_class
        self.message = message
        self.retryable = retryable


@dataclass
class ToolRegistry:
    session_factory: async_sessionmaker[AsyncSession]
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    # --- registration ----------------------------------------------------------------------

    def tool(
        self,
        name: str,
        *,
        description: str,
        side_effect: SideEffect,
        timeout_s: float = 30.0,
        retryable: bool = False,
    ) -> Callable[[ToolFn], ToolFn]:
        def decorator(fn: ToolFn) -> ToolFn:
            self.add(
                ToolSpec(
                    name=name,
                    description=description,
                    input_model=_input_model(fn, name),
                    side_effect=side_effect,
                    timeout_s=timeout_s,
                    retryable=retryable,
                    fn=fn,
                )
            )
            return fn

        return decorator

    def add(self, spec: ToolSpec) -> None:
        if not spec.name or set(spec.name) - _NAME_CHARS or not spec.name[0].isalpha():
            raise InvalidToolDefinition(f"tool name must be lower_snake_case: {spec.name!r}")
        if spec.name in self._tools:
            raise InvalidToolDefinition(f"tool {spec.name!r} is already registered")
        if spec.side_effect not in ("read", "write", "irreversible"):
            raise InvalidToolDefinition(f"{spec.name}: invalid side_effect {spec.side_effect!r}")
        if spec.timeout_s <= 0:
            raise InvalidToolDefinition(f"{spec.name}: timeout_s must be positive")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownTool(name, self._tools) from None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def definitions(self, names: Sequence[str]) -> list[dict[str, Any]]:
        return [self.get(name).definition() for name in names]

    # --- invocation ------------------------------------------------------------------------

    async def invoke(
        self,
        name: str,
        args: dict[str, Any],
        *,
        company_id: uuid.UUID,
        actor: Actor,
        tool_call_id: str,
        agent_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        workflow_run_id: uuid.UUID | None = None,
        step_seq: int = 0,
        before_call: Callable[[AsyncSession], Awaitable[None]] | None = None,
    ) -> ToolInvocation:
        """Run one tool call. Never raises for tool failures (returns ok=False); raises
        ``UnknownTool`` because there is no side-effect level to record a call against."""
        spec = self.get(name)
        refs = {
            "company_id": company_id,
            "actor": actor,
            "agent_id": agent_id,
            "run_id": run_id,
            "task_id": task_id,
            "workflow_run_id": workflow_run_id,
        }

        async with self.session_factory() as session:
            if before_call is not None:
                await before_call(session)
            await self._emit(
                session,
                ev.ToolCalled(
                    tool=name,
                    tool_call_id=tool_call_id,
                    args_summary=_summarize(args),
                    side_effect=spec.side_effect,
                    step_seq=step_seq,
                ),
                **refs,
            )
            await session.commit()

        started = time.monotonic()
        try:
            async with self.session_factory() as session:
                try:
                    parsed = spec.input_model.model_validate(args)
                except ValidationError as exc:
                    raise _ToolError(
                        "InvalidToolArguments", _validation_message(exc), False
                    ) from None

                ctx = ToolContext(
                    session=session,
                    tool_call_id=tool_call_id,
                    idempotency_key=_idempotency_key(task_id or run_id, step_seq, name, args),
                    step_seq=step_seq,
                    **refs,
                )
                try:
                    async with asyncio.timeout(spec.timeout_s):
                        result = await spec.fn(parsed, ctx)
                except TimeoutError:
                    raise _ToolError(
                        "ToolTimeout", f"{name} exceeded {spec.timeout_s}s", spec.retryable
                    ) from None
                except _ToolError:
                    raise
                except Exception as exc:  # noqa: BLE001 - tool bugs and upstream errors alike
                    raise _ToolError(type(exc).__name__, str(exc), spec.retryable) from exc

                if not isinstance(result, ToolResult):
                    raise _ToolError(
                        "InvalidToolResult", f"{name} returned {type(result).__name__}", False
                    )

                duration_ms = int((time.monotonic() - started) * 1000)
                await self._emit(
                    session,
                    ev.ToolCompleted(
                        tool=name,
                        tool_call_id=tool_call_id,
                        duration_ms=duration_ms,
                        result_summary=result.summary,
                        cost_usd=result.cost_usd,
                        produced=result.produced,
                    ),
                    **refs,
                )
                await session.commit()
                return ToolInvocation(
                    tool=name,
                    tool_call_id=tool_call_id,
                    ok=True,
                    output=result.output,
                    error_class=None,
                    message=None,
                    will_retry=False,
                    duration_ms=duration_ms,
                    produced=list(result.produced),
                    cost_usd=result.cost_usd,
                    progress=result.progress,
                )
        except _ToolError as failure:
            duration_ms = int((time.monotonic() - started) * 1000)
            async with self.session_factory() as session:
                await self._emit(
                    session,
                    ev.ToolFailed(
                        tool=name,
                        tool_call_id=tool_call_id,
                        error_class=failure.error_class,
                        message=failure.message[:2000],
                        will_retry=failure.retryable,
                    ),
                    **refs,
                )
                await session.commit()
            return ToolInvocation(
                tool=name,
                tool_call_id=tool_call_id,
                ok=False,
                output=None,
                error_class=failure.error_class,
                message=failure.message,
                will_retry=failure.retryable,
                duration_ms=duration_ms,
            )

    @staticmethod
    async def _emit(session: AsyncSession, payload: EventPayload, **refs: Any) -> None:
        run_id = refs["run_id"]
        await emit(
            session,
            new_event(
                payload,
                company_id=refs["company_id"],
                actor=refs["actor"],
                aggregate_type="agent_run" if run_id else "tool_call",
                aggregate_id=run_id or uuid.uuid4(),
                agent_id=refs["agent_id"],
                run_id=run_id,
                task_id=refs["task_id"],
                workflow_run_id=refs["workflow_run_id"],
                correlation_id=refs["workflow_run_id"],
            ),
        )


def _input_model(fn: ToolFn, name: str) -> type[BaseModel]:
    if not inspect.iscoroutinefunction(fn):
        raise InvalidToolDefinition(f"{name}: tool must be an async function")
    params = list(inspect.signature(fn).parameters)
    if len(params) != 2:
        raise InvalidToolDefinition(f"{name}: signature must be (args, ctx)")
    hints = get_type_hints(fn)
    model = hints.get(params[0])
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise InvalidToolDefinition(f"{name}: first parameter must be annotated with a model")
    return model


def _summarize(args: dict[str, Any]) -> str:
    text = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= _SUMMARY_LIMIT else text[: _SUMMARY_LIMIT - 1] + "…"


def _idempotency_key(scope: uuid.UUID | None, step_seq: int, name: str, args: dict) -> str:
    digest = hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"{scope or 'adhoc'}:{step_seq}:{name}:{digest}"


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '(args)'}: {err['msg']}" for err in exc.errors()
    )
