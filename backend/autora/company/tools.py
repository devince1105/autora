"""``submit_command``: how an agent asks the company to do something (platform/04, T-605a).

This is the only write an executive agent has. It cannot create a project, move money or start
work directly; it asks, and :mod:`autora.company.commands` decides. Everything that follows from
that — the policy, the limits, the approval, the record — is the pipeline's, not the agent's.

Two consequences worth being deliberate about:

**A refusal comes back as a result, not an error.** The agent is told it was denied and why, and
can do something else in the same run. A tool that raised here would end the run over a
perfectly ordinary answer — "no" is a thing a company says.

**The idempotency key is the tool call's.** The runtime already gives every tool call a key that
is stable across retries, so an agent whose run is retried after a crash re-submits the same
command and gets the first outcome instead of a second project.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from autora.company.commands import CommandBus, UnknownCommand
from autora.db.models import Agent
from autora.db.repositories.companies import get_policies
from autora.runtime.tools import ToolContext, ToolRegistry, ToolResult

TOOL = "submit_command"

Decision = Literal["done", "refused", "awaiting_approval"]


class SubmitCommandArgs(BaseModel):
    command: str = Field(
        description=(
            "What to ask the company to do: CreateCycleGoal, AllocateBudget, "
            "InstantiateWorkflow, CreateProject, PauseProject, ResumeProject, KillProject, "
            "UpdateStrategy, RestartWorkflow."
        )
    )
    payload: dict[str, Any] = Field(
        default_factory=dict, description="The command's fields. Each command has its own."
    )
    reason: str | None = Field(
        default=None, description="Why, in one line. Recorded with the command."
    )


def register_tools(registry: ToolRegistry, bus: CommandBus) -> None:
    async def submit_command(args: SubmitCommandArgs, ctx: ToolContext) -> ToolResult:
        try:
            spec = bus.get(args.command)
        except UnknownCommand as unknown:
            return ToolResult(
                output={"decision": "refused", "reason": str(unknown)},
                summary=f"{args.command}: not a command this company knows",
            )
        role = None
        if ctx.agent_id is not None:
            role = await ctx.session.scalar(select(Agent.role).where(Agent.id == ctx.agent_id))
        result = await bus.submit(
            ctx.session,
            spec.name,
            args.payload,
            company_id=ctx.company_id,
            actor=ctx.actor,
            role=role,
            idempotency_key=ctx.idempotency_key,
            company_policies=await get_policies(ctx.session, ctx.company_id),
            task_id=ctx.task_id,
            run_id=ctx.run_id,
        )
        record = result.record
        output: dict[str, Any] = {
            "decision": record.outcome,
            "command": record.command,
            "reason": record.reason,
            **(record.result or {}),
        }
        if result.awaiting:
            output["next"] = "a person has to approve it; it runs if they do"
        return ToolResult(
            output=output,
            summary=_summary(record.command, record.outcome, record.reason),
        )

    registry.tool(
        TOOL,
        description=(
            "Ask the company to do something: set a goal, allocate a budget, start work, "
            "pause or stop a project, change the strategy. The company decides — you may be "
            "refused, or told a person has to approve it. Check the decision in the result."
        ),
        side_effect="write",
        retryable=False,
    )(submit_command)


def _summary(command: str, outcome: str, reason: str | None) -> str:
    if outcome == "done":
        return f"{command}: done"
    if outcome == "awaiting_approval":
        return f"{command}: waiting for a person"
    return f"{command}: refused ({reason or 'no reason given'})"
