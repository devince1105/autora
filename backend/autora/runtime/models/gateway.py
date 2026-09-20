"""Model gateway: the only way anything in Autora calls a model (T-207).

For each request:
1. Route (role, capability) to an alias; walk the alias's fallback chain if a provider fails in a
   way that allows it.
2. Ask the cost guard for a reservation (T-209). A budget refusal is final: no fallback.
3. Call the provider, compute cost from usage and the alias's price.
4. Record a ``model_calls`` row (also for failures) and settle the reservation in that same
   transaction, so recorded cost and budget accounting cannot diverge.
5. If the request has an ``output_model``, validate the reply. Invalid output is not an error
   here: it comes back as ``output_issues`` for the agent's repair step.

The gateway writes no events: model calls are high-volume ledger rows, not domain facts.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.db.models import ModelCall, ModelCallStatus
from autora.runtime.models.providers.base import ModelProvider, ProviderError
from autora.runtime.models.router import ModelBinding, ModelConfigError, ModelRouter
from autora.runtime.models.types import ModelRequest, ModelResponse, TextBlock, Usage

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class ModelCallFailed(Exception):
    def __init__(self, failures: list[tuple[str, ProviderError]]):
        self.failures = failures
        detail = "; ".join(f"{alias}: {err}" for alias, err in failures)
        super().__init__(f"model call failed ({detail})")

    @property
    def last(self) -> ProviderError:
        return self.failures[-1][1]


class CostGuard(Protocol):
    async def reserve(self, request: ModelRequest, binding: ModelBinding) -> Any: ...

    async def settle(
        self, session: AsyncSession, reservation: Any, cost: Decimal, model_call_id: uuid.UUID
    ) -> None: ...

    async def release(self, session: AsyncSession, reservation: Any) -> None: ...


class NoCostGuard:
    """No budget enforcement (unit tests, tooling). Production wires ``DbCostGuard``."""

    async def reserve(self, request: ModelRequest, binding: ModelBinding) -> None:
        return None

    async def settle(self, session, reservation, cost, model_call_id) -> None:
        return None

    async def release(self, session, reservation) -> None:
        return None


@dataclass
class ModelGateway:
    router: ModelRouter
    providers: Mapping[str, ModelProvider]
    session_factory: async_sessionmaker[AsyncSession]
    cost_guard: CostGuard = field(default_factory=NoCostGuard)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        ctx = request.context
        alias = self.router.resolve(ctx.role, request.capability)
        failures: list[tuple[str, ProviderError]] = []

        for candidate in self.router.chain(alias):
            binding = self.router.binding(candidate)
            provider = self.providers.get(binding.provider)
            if provider is None:
                raise ModelConfigError(
                    f"alias {candidate!r} uses provider {binding.provider!r}, not installed"
                )

            reservation = await self.cost_guard.reserve(request, binding)
            started = time.monotonic()
            try:
                raw = await provider.complete(request, binding)
            except ProviderError as err:
                latency = _ms(started)
                async with self.session_factory() as session:
                    await self._record(
                        session, request, candidate, binding, Usage(), Decimal(0), latency,
                        status=ModelCallStatus.ERROR, stop_reason=None,
                        error={"error_class": err.error_class, "message": err.message[:2000]},
                    )  # fmt: skip
                    await self.cost_guard.release(session, reservation)
                    await session.commit()
                failures.append((candidate, err))
                if err.fallback_allowed:
                    continue
                raise ModelCallFailed(failures) from err

            latency = _ms(started)
            cost = self.router.price_for(raw.model_id, binding.price).cost(raw.usage)
            async with self.session_factory() as session:
                call_id = await self._record(
                    session, request, candidate, binding, raw.usage, cost, latency,
                    status=ModelCallStatus.OK, stop_reason=raw.stop_reason, error=None,
                    model_id=raw.model_id,
                )  # fmt: skip
                await self.cost_guard.settle(session, reservation, cost, call_id)
                await session.commit()

            parsed, issues = None, []
            if request.output_model is not None and raw.stop_reason != "tool_use":
                parsed, issues = _parse(request.output_model, raw.content)

            return ModelResponse(
                model_call_id=call_id,
                content=raw.content,
                stop_reason=raw.stop_reason,
                usage=raw.usage,
                provider=binding.provider,
                alias=candidate,
                model_id=raw.model_id,
                cost_usd=cost,
                latency_ms=latency,
                parsed=parsed,
                output_issues=issues,
            )

        raise ModelCallFailed(failures)

    @staticmethod
    async def _record(
        session: AsyncSession,
        request: ModelRequest,
        alias: str,
        binding: ModelBinding,
        usage: Usage,
        cost: Decimal,
        latency_ms: int,
        *,
        status: ModelCallStatus,
        stop_reason: str | None,
        error: dict[str, Any] | None,
        model_id: str | None = None,
    ) -> uuid.UUID:
        ctx = request.context
        row = ModelCall(
            company_id=ctx.company_id,
            project_id=ctx.project_id,
            agent_id=ctx.agent_id,
            task_id=ctx.task_id,
            run_id=ctx.run_id,
            workflow_run_id=ctx.workflow_run_id,
            cycle_id=ctx.cycle_id,
            role=ctx.role,
            capability=request.capability,
            alias=alias,
            provider=binding.provider,
            model_id=model_id or binding.model_id,
            status=status.value,
            stop_reason=stop_reason,
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            error=error,
        )
        session.add(row)
        await session.flush()
        return row.id


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _parse(model: type[BaseModel], content) -> tuple[BaseModel | None, list[str]]:
    text = "".join(b.text for b in content if isinstance(b, TextBlock)).strip()
    if not text:
        return None, ["expected a JSON object in the reply, got no text"]
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"reply is not valid JSON: {exc.msg} at position {exc.pos}"]
    try:
        return model.model_validate(data), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
            for err in exc.errors()
        ]
