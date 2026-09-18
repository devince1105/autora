"""T-204: tool registry and TOOL_* events."""

import asyncio
import uuid
from decimal import Decimal

import pytest
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from autora.db.models import CompanyGoal, EventRecord
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events import new_event
from autora.runtime.events.outbox import emit
from autora.runtime.tools import (
    InvalidToolDefinition,
    ToolContext,
    ToolRegistry,
    ToolResult,
    UnknownTool,
)
from tests.conftest import unique_company


class SearchArgs(BaseModel):
    query: str = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=20)


class GoalArgs(BaseModel):
    title: str
    fail_after_write: bool = False


@pytest.fixture
async def setup(committed):
    async with committed() as session:
        company = await unique_company(session, "tools")
        await session.commit()

    registry = ToolRegistry(committed)
    calls: list[str] = []

    @registry.tool("web_search", description="Search the web", side_effect="read", retryable=True)
    async def web_search(args: SearchArgs, ctx: ToolContext) -> ToolResult:
        calls.append(ctx.idempotency_key)
        return ToolResult(
            output={"results": [f"{args.query} #{i}" for i in range(args.k)]},
            summary=f"{args.k} results",
            cost_usd=Decimal("0.008"),
        )

    @registry.tool("create_goal", description="Writes a row", side_effect="write")
    async def create_goal(args: GoalArgs, ctx: ToolContext) -> ToolResult:
        goal = CompanyGoal(
            company_id=ctx.company_id, level="cycle", title=args.title, metric="m", target=1
        )
        ctx.session.add(goal)
        await ctx.session.flush()
        if args.fail_after_write:
            raise RuntimeError("storage quota exceeded")
        return ToolResult(
            output={"goal_id": str(goal.id)},
            produced=[ev.ProducedRef(type="goal", id=goal.id)],
        )

    @registry.tool("slow", description="Sleeps", side_effect="read", timeout_s=0.1)
    async def slow(args: SearchArgs, ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(1)
        return ToolResult()

    return {"company": company, "registry": registry, "calls": calls}


def _kw(setup, **extra):
    return {
        "company_id": setup["company"].id,
        "actor": Actor.system("test"),
        "tool_call_id": f"call_{uuid.uuid4().hex[:8]}",
        "run_id": uuid.uuid4(),
        "step_seq": 3,
        **extra,
    }


async def _events(committed, tool_call_id):
    async with committed() as session:
        rows = (
            await session.scalars(
                select(EventRecord)
                .where(EventRecord.payload["tool_call_id"].astext == tool_call_id)
                .order_by(EventRecord.seq)
            )
        ).all()
    return [(r.event_type, r.payload) for r in rows]


# --- registration ------------------------------------------------------------------------


def test_definitions_expose_json_schema(setup):
    [definition] = setup["registry"].definitions(["web_search"])
    assert definition["name"] == "web_search"
    assert definition["input_schema"]["required"] == ["query"]
    assert setup["registry"].names() == ["create_goal", "slow", "web_search"]


def test_invalid_definitions_rejected(committed):
    registry = ToolRegistry(committed)

    async def ok(args: SearchArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult()

    registry.tool("dup", description="", side_effect="read")(ok)
    with pytest.raises(InvalidToolDefinition, match="already registered"):
        registry.tool("dup", description="", side_effect="read")(ok)
    with pytest.raises(InvalidToolDefinition, match="lower_snake_case"):
        registry.tool("Web-Search", description="", side_effect="read")(ok)

    async def untyped(args, ctx):
        return ToolResult()

    with pytest.raises(InvalidToolDefinition, match="annotated with a model"):
        registry.tool("untyped", description="", side_effect="read")(untyped)

    def sync(args: SearchArgs, ctx: ToolContext):
        return ToolResult()

    with pytest.raises(InvalidToolDefinition, match="async"):
        registry.tool("sync", description="", side_effect="read")(sync)


async def test_unknown_tool_raises(setup):
    with pytest.raises(UnknownTool, match="registered: create_goal, slow, web_search"):
        await setup["registry"].invoke("teleport", {}, **_kw(setup))


# --- invocation --------------------------------------------------------------------------


async def test_success_emits_called_then_completed(committed, setup):
    kw = _kw(setup)
    result = await setup["registry"].invoke("web_search", {"query": "EU AI Act", "k": 2}, **kw)

    assert result.ok and result.output == {"results": ["EU AI Act #0", "EU AI Act #1"]}
    assert result.cost_usd == Decimal("0.008")
    events = await _events(committed, kw["tool_call_id"])
    assert [t for t, _ in events] == ["TOOL_CALLED", "TOOL_COMPLETED"]
    called, completed = events[0][1], events[1][1]
    assert called["side_effect"] == "read" and called["step_seq"] == 3
    assert called["args_summary"] == '{"k": 2, "query": "EU AI Act"}'
    assert completed["result_summary"] == "2 results" and completed["cost_usd"] == "0.008"


async def test_domain_write_and_completed_commit_together(committed, setup):
    kw = _kw(setup)
    result = await setup["registry"].invoke("create_goal", {"title": "Publish 3"}, **kw)
    assert result.ok and result.produced[0].type == "goal"

    async with committed() as session:
        goal = await session.get(CompanyGoal, uuid.UUID(result.output["goal_id"]))
    assert goal is not None
    completed = (await _events(committed, kw["tool_call_id"]))[-1][1]
    assert completed["produced"] == [{"type": "goal", "id": result.output["goal_id"]}]


async def test_failure_rolls_back_domain_writes_and_emits_failed(committed, setup):
    kw = _kw(setup)
    title = f"never-{uuid.uuid4().hex[:6]}"
    result = await setup["registry"].invoke(
        "create_goal", {"title": title, "fail_after_write": True}, **kw
    )
    assert not result.ok
    assert (result.error_class, result.message, result.will_retry) == (
        "RuntimeError",
        "storage quota exceeded",
        False,
    )
    async with committed() as session:
        leaked = await session.scalar(
            select(func.count()).select_from(CompanyGoal).where(CompanyGoal.title == title)
        )
    assert leaked == 0
    assert [t for t, _ in await _events(committed, kw["tool_call_id"])] == [
        "TOOL_CALLED",
        "TOOL_FAILED",
    ]


async def test_invalid_arguments_are_a_recorded_failure(committed, setup):
    kw = _kw(setup)
    result = await setup["registry"].invoke("web_search", {"query": "", "k": 99}, **kw)
    assert not result.ok and result.error_class == "InvalidToolArguments"
    assert "query" in result.message and "k" in result.message
    assert setup["calls"] == [], "tool body must not run with invalid args"
    events = await _events(committed, kw["tool_call_id"])
    assert [t for t, _ in events] == ["TOOL_CALLED", "TOOL_FAILED"]


async def test_timeout_is_a_failure_with_retry_flag(committed, setup):
    kw = _kw(setup)
    result = await setup["registry"].invoke("slow", {"query": "x"}, **kw)
    assert (result.ok, result.error_class, result.will_retry) == (False, "ToolTimeout", False)
    failed = (await _events(committed, kw["tool_call_id"]))[-1][1]
    assert failed["error_class"] == "ToolTimeout"


async def test_idempotency_key_is_stable_for_same_call(setup):
    run_id = uuid.uuid4()
    for _ in range(2):
        await setup["registry"].invoke(
            "web_search", {"k": 1, "query": "a"}, **_kw(setup, run_id=run_id)
        )
    await setup["registry"].invoke(
        "web_search", {"query": "b", "k": 1}, **_kw(setup, run_id=run_id)
    )
    first, second, third = setup["calls"]
    assert first == second, "same run, step and args -> same key (retry-safe)"
    assert first != third


async def test_before_call_hook_commits_with_tool_called(committed, setup):
    kw = _kw(setup)
    marker = f"hook-{uuid.uuid4().hex[:6]}"

    async def hook(session):
        session.add(
            CompanyGoal(
                company_id=setup["company"].id, level="cycle", title=marker, metric="m", target=1
            )
        )

    await setup["registry"].invoke("web_search", {"query": "x"}, before_call=hook, **kw)
    async with committed() as session:
        count = await session.scalar(
            select(func.count()).select_from(CompanyGoal).where(CompanyGoal.title == marker)
        )
    assert count == 1


async def test_slow_tool_does_not_hold_the_company_event_lock(committed, setup):
    """Another writer must be able to emit for the same company while a tool is running."""
    registry = setup["registry"]
    started = asyncio.Event()

    @registry.tool("blocking_fetch", description="", side_effect="read", timeout_s=5)
    async def blocking_fetch(args: SearchArgs, ctx: ToolContext) -> ToolResult:
        started.set()
        await asyncio.sleep(1.0)
        return ToolResult()

    tool_task = asyncio.create_task(registry.invoke("blocking_fetch", {"query": "x"}, **_kw(setup)))
    await started.wait()

    async def other_writer():
        async with committed() as session:
            await emit(
                session,
                new_event(
                    ev.AgentThinking(phase="plan", step_seq=0),
                    company_id=setup["company"].id,
                    actor=Actor.system("other"),
                    aggregate_type="agent",
                    aggregate_id=uuid.uuid4(),
                ),
            )
            await session.commit()

    await asyncio.wait_for(other_writer(), timeout=0.5)
    assert not tool_task.done(), "the other writer finished while the tool was still running"
    assert (await tool_task).ok
