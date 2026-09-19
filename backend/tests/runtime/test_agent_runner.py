"""T-211: agent runner (think / act / evaluate / repair / finish) with the fake provider.

Uses committed transactions like the worker will: the claim comes from one session, the runner
opens its own, and every write is checked against the lease.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel, Field
from sqlalchemy import select

from autora.db.models import (
    ActivityState,
    Agent,
    AgentActivity,
    AgentRun,
    AgentStep,
    Approval,
    EventRecord,
    Project,
    Task,
)
from autora.infra.blobstore import LocalFSBlobStore
from autora.runtime.activity import initialize_activity
from autora.runtime.actor import Actor
from autora.runtime.agent_runner import AgentRunner
from autora.runtime.approvals import ApprovalService
from autora.runtime.behaviors import AgentBehavior, BehaviorRegistry, UnknownBehavior
from autora.runtime.cost.guard import DbCostGuard
from autora.runtime.models import ModelBinding, ModelGateway, ModelRouter, Price
from autora.runtime.models.providers.fake import FakeModelProvider, FakeToolUse, FakeTurn
from autora.runtime.policy import PolicyEngine, allow
from autora.runtime.task_manager import AgentBusy, TaskManager
from autora.runtime.tools import ToolContext, ToolRegistry, ToolResult
from tests.conftest import unique_company

OPERATOR = Actor.human("operator")
ROLE = "researcher"
CALL_COST = Decimal("0.003500")  # 100 in @ $10/MTok + 50 out @ $50/MTok (fake defaults)


class Note(BaseModel):
    story: str
    sources: list[str] = Field(min_length=1)


class SearchArgs(BaseModel):
    query: str


class PublishArgs(BaseModel):
    story: str


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


async def sources_are_urls(session, ctx, output: Note) -> list[str]:
    return [f"source {s!r} is not a URL" for s in output.sources if not s.startswith("http")]


def _turn(**structured) -> FakeTurn:
    return FakeTurn(structured=structured)


def _use(name: str, **args) -> FakeTurn:
    return FakeTurn(text="Let me use a tool.", tool_uses=[FakeToolUse(name=name, input=args)])


@pytest.fixture
def blobs(tmp_path):
    return LocalFSBlobStore(tmp_path / "blobs")


@pytest.fixture
async def world(committed, blobs):
    async with committed() as session:
        company = await unique_company(session, "runner")
        project = Project(company_id=company.id, name="p")
        agent = Agent(company_id=company.id, role=ROLE, display_name="Rae")
        session.add_all([project, agent])
        await session.flush()
        await initialize_activity(session, agent, actor=Actor.system("setup"))
        await session.commit()

    clock = Clock()
    calls: dict[str, list[dict]] = {"web_search": [], "publish": [], "flaky": [], "slow": []}
    registry = ToolRegistry(committed)
    tm = TaskManager(clock=clock)

    @registry.tool("web_search", description="Search", side_effect="read", retryable=True)
    async def web_search(args: SearchArgs, ctx: ToolContext) -> ToolResult:
        calls["web_search"].append(args.model_dump())
        return ToolResult(
            output={"results": ["https://a.example/1", "https://b.example/2"]},
            summary="2 results",
            cost_usd=Decimal("0.01"),
        )

    @registry.tool("publish", description="Publish", side_effect="irreversible")
    async def publish(args: PublishArgs, ctx: ToolContext) -> ToolResult:
        calls["publish"].append(args.model_dump())
        return ToolResult(output={"published": True})

    @registry.tool("flaky", description="Breaks", side_effect="read")
    async def flaky(args: SearchArgs, ctx: ToolContext) -> ToolResult:
        calls["flaky"].append(args.model_dump())
        raise RuntimeError("upstream is down")

    @registry.tool("forbidden", description="No rule allows it", side_effect="write")
    async def forbidden(args: SearchArgs, ctx: ToolContext) -> ToolResult:
        raise AssertionError("must never run")

    @registry.tool("slow", description="Outlives its lease", side_effect="read", timeout_s=5)
    async def slow(args: SearchArgs, ctx: ToolContext) -> ToolResult:
        calls["slow"].append(args.model_dump())
        clock.now += timedelta(minutes=30)  # the worker stalls; the reaper takes the lease
        await tm.reap_expired_leases(ctx.session)
        return ToolResult(output={"done": True})

    policy = PolicyEngine()
    for name in registry.names():
        policy.declare(name, registry.get(name).side_effect)
    policy.add(allow("web_search", ROLE) + allow("publish", ROLE) + allow("flaky", ROLE))
    policy.add(allow("slow", ROLE))  # no rule for "forbidden": default deny

    fake = FakeModelProvider()
    router = ModelRouter(
        aliases={
            "frontier": ModelBinding(
                provider="fake",
                model_id="fake-frontier",
                price=Price(input=Decimal(10), output=Decimal(50)),
            )
        },
        routes={"*.*": "frontier"},
    )
    gateway = ModelGateway(
        router=router, providers={"fake": fake}, session_factory=committed,
        cost_guard=DbCostGuard(committed, clock=clock),
    )  # fmt: skip
    behaviors = BehaviorRegistry()
    behaviors.register(
        AgentBehavior(
            role=ROLE,
            capability="research_extraction",
            system_prompt="You research stories.",
            output_model=Note,
            tools=("web_search", "publish", "flaky", "slow", "forbidden"),
            validators=(sources_are_urls,),
            max_steps=4,
            repair_limit=1,
        )
    )
    runner = AgentRunner(
        session_factory=committed,
        task_manager=tm,
        gateway=gateway,
        tools=registry,
        policy=policy,
        approvals=ApprovalService(tm, clock=clock),
        blobs=blobs,
        behaviors=behaviors,
        clock=clock,
    )
    return {
        "company": company,
        "project": project,
        "agent": agent,
        "tm": tm,
        "fake": fake,
        "runner": runner,
        "calls": calls,
        "clock": clock,
        "committed": committed,
        "behaviors": behaviors,
    }


async def _task(world, name="research", **kw) -> Task:
    async with world["committed"]() as session:
        task = await world["tm"].add_task(
            session,
            company_id=world["company"].id,
            project_id=world["project"].id,
            name=name,
            display_name="Find sources",
            required_role=ROLE,
            input={"topic": "EU AI Act"},
            **kw,
        )
        await session.commit()
    return task


async def _claim(world, worker="worker-1"):
    async with world["committed"]() as session:
        claim = await world["tm"].claim_next(session, world["agent"], worker)
        await session.commit()
    return claim


async def _get(world, model, id_):
    async with world["committed"]() as session:
        return await session.get(model, id_)


async def _activity_events(world, run_id) -> list[tuple[str, dict]]:
    async with world["committed"]() as session:
        rows = (
            await session.scalars(
                select(EventRecord)
                .where(EventRecord.run_id == run_id, EventRecord.event_type.like("AGENT_%"))
                .order_by(EventRecord.seq)
            )
        ).all()
    return [(r.event_type, r.payload) for r in rows]


async def _steps(world, run_id) -> list[AgentStep]:
    async with world["committed"]() as session:
        return list(
            (
                await session.scalars(
                    select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.seq)
                )
            ).all()
        )


def _script(world, attempt, *turns):
    world["fake"].script(ROLE, "research", attempt, *turns)


# --- the happy path ------------------------------------------------------------------------


async def test_think_act_evaluate_complete(world, blobs):
    _script(
        world,
        1,
        _use("web_search", query="EU AI Act"),
        _turn(story="EU AI Act explained", sources=["https://a.example/1"]),
    )
    task = await _task(world)
    claim = await _claim(world)

    outcome = await world["runner"].run(claim)
    assert outcome.status == "completed", outcome

    events = await _activity_events(world, claim.run.id)
    assert [e for e, _ in events] == [
        "AGENT_RUN_STARTED",
        "AGENT_THINKING",
        "AGENT_WORKING",
        "AGENT_THINKING",
        "AGENT_REVIEWING",
        "AGENT_RUN_COMPLETED",
    ]
    assert events[1][1]["phase"] == "plan" and events[3][1]["phase"] == "reason"
    assert events[2][1] == {"tool": "web_search", "tool_call_id": events[2][1]["tool_call_id"],
                            "step_seq": 1, "progress": None, "links": []}  # fmt: skip
    assert events[4][1] == {"phase": "evaluate", "attempt": 1, "issues_count": 0, "links": []}
    completed = events[5][1]
    assert Decimal(completed["cost_usd"]) == CALL_COST * 2 + Decimal("0.01")
    assert completed["steps"] == 4 and completed["output_summary"].startswith("{")

    task = await _get(world, Task, task.id)
    run = await _get(world, AgentRun, claim.run.id)
    assert task.state == "SUCCEEDED"
    assert task.output == {"story": "EU AI Act explained", "sources": ["https://a.example/1"]}
    assert run.state == "COMPLETED" and run.output == task.output
    assert run.evaluation == {"passed": True, "issues": [], "round": 1}
    assert run.cost_usd == CALL_COST * 2 + Decimal("0.01")
    assert (run.tokens_in, run.tokens_out) == (200, 100)
    assert (await _get(world, AgentActivity, world["agent"].id)).state == ActivityState.COMPLETED

    steps = await _steps(world, claim.run.id)
    assert [(s.seq, s.kind) for s in steps] == [
        (0, "think"),
        (1, "act"),
        (2, "think"),
        (3, "evaluate"),
    ]
    assert steps[0].tool_calls[0]["name"] == "web_search" and steps[0].cost_usd == CALL_COST
    assert steps[1].cost_usd == Decimal("0.01") and steps[1].tool_calls[0]["ok"] is True
    assert len({s.prompt_hash for s in steps}) == 1
    think = json.loads(await blobs.get(steps[0].blob_key))
    assert think["request"]["system"] == "You research stories."
    assert "EU AI Act" in think["request"]["messages"][0]["content"][0]["text"]
    assert think["response"]["stop_reason"] == "tool_use"
    act = json.loads(await blobs.get(steps[1].blob_key))
    assert act["output"] == {"results": ["https://a.example/1", "https://b.example/2"]}
    assert act["messages_after"][-1]["content"][0]["type"] == "tool_result"

    # The model saw the tool result on its second call, with the tool definitions offered.
    second = world["fake"].requests[1]
    assert [m.role for m in second.messages] == ["user", "assistant", "user"]
    assert second.messages[2].content[0].content.startswith('{"results"')
    assert [t.name for t in second.tools][:2] == ["web_search", "publish"]
    assert second.context.step_seq == 2 and second.context.attempt == 1


async def test_agent_tools_narrow_the_behaviors_tools(world):
    async with world["committed"]() as session:
        agent = await session.get(Agent, world["agent"].id)
        agent.tools = ["web_search"]
        await session.commit()
    world["agent"].tools = ["web_search"]
    _script(world, 1, _turn(story="s", sources=["https://x"]))
    await _task(world)
    outcome = await world["runner"].run(await _claim(world))
    assert outcome.status == "completed", outcome
    assert [t.name for t in world["fake"].requests[0].tools] == ["web_search"]


# --- evaluate and repair -------------------------------------------------------------------


async def test_validator_issues_are_repaired_once(world):
    _script(
        world,
        1,
        _turn(story="s", sources=["not a url"]),
        _turn(story="s", sources=["https://fixed.example"]),
    )
    task = await _task(world)
    claim = await _claim(world)
    outcome = await world["runner"].run(claim)
    assert outcome.status == "completed", outcome

    events = await _activity_events(world, claim.run.id)
    reviewing = [p for e, p in events if e == "AGENT_REVIEWING"]
    assert reviewing == [
        {"phase": "evaluate", "attempt": 1, "issues_count": 1, "links": []},
        {"phase": "repair", "attempt": 1, "issues_count": 1, "links": []},
        {"phase": "evaluate", "attempt": 2, "issues_count": 0, "links": []},
    ]
    assert [e for e, _ in events].count("AGENT_THINKING") == 1, "a repair call is not a think"
    repair_request = world["fake"].requests[1]
    assert "source 'not a url' is not a URL" in repair_request.messages[-1].content[0].text
    steps = await _steps(world, claim.run.id)
    assert [s.kind for s in steps] == ["think", "evaluate", "repair", "evaluate"]
    run = await _get(world, AgentRun, claim.run.id)
    assert run.evaluation == {"passed": True, "issues": [], "round": 2}
    assert (await _get(world, Task, task.id)).output["sources"] == ["https://fixed.example"]


async def test_schema_issues_from_the_gateway_are_repaired_too(world):
    _script(world, 1, FakeTurn(text="not json at all"), _turn(story="s", sources=["https://x"]))
    await _task(world)
    outcome = await world["runner"].run(await _claim(world))
    assert outcome.status == "completed", outcome
    assert "Issues:" in world["fake"].requests[1].messages[-1].content[0].text


async def test_evaluation_exhausted_retries_the_task_then_fails_for_good(world):
    bad = _turn(story="s", sources=["nope"])
    _script(world, 1, bad, bad)
    _script(world, 2, bad, bad)
    task = await _task(world, max_attempts=2)

    claim = await _claim(world)
    outcome = await world["runner"].run(claim)
    assert (outcome.status, outcome.error_class, outcome.will_retry) == (
        "failed", "EvaluationFailed", True,
    )  # fmt: skip
    assert (await _get(world, Task, task.id)).state == "READY"
    assert (await _get(world, AgentRun, claim.run.id)).state == "FAILED"
    assert (await _get(world, AgentActivity, world["agent"].id)).state == ActivityState.IDLE

    world["clock"].now += timedelta(minutes=10)  # past the retry backoff
    claim2 = await _claim(world)
    assert claim2.task.attempt == 2
    outcome = await world["runner"].run(claim2)
    assert outcome.will_retry is False
    assert (await _get(world, Task, task.id)).state == "FAILED"
    activity = await _get(world, AgentActivity, world["agent"].id)
    assert activity.state == ActivityState.FAILED
    assert activity.detail["error_class"] == "EvaluationFailed"


# --- tools: failures, unknown names, policy -----------------------------------------------


async def test_tool_failure_is_fed_back_to_the_model(world):
    _script(world, 1, _use("flaky", query="x"), _turn(story="s", sources=["https://x"]))
    await _task(world)
    claim = await _claim(world)
    assert (await world["runner"].run(claim)).status == "completed"
    result = world["fake"].requests[1].messages[-1].content[0]
    assert result.is_error and result.content == "RuntimeError: upstream is down"
    steps = await _steps(world, claim.run.id)
    assert steps[1].kind == "act" and steps[1].tool_calls[0]["error_class"] == "RuntimeError"
    async with world["committed"]() as session:
        kinds = (
            await session.scalars(
                select(EventRecord.event_type)
                .where(EventRecord.run_id == claim.run.id, EventRecord.event_type.like("TOOL_%"))
                .order_by(EventRecord.seq)
            )
        ).all()
    assert kinds == ["TOOL_CALLED", "TOOL_FAILED"]


async def test_unknown_tool_names_are_fed_back_not_crashed(world):
    _script(
        world,
        1,
        FakeTurn(tool_uses=[FakeToolUse(name="nonexistent")]),
        _turn(story="s", sources=["https://x"]),
    )
    await _task(world)
    claim = await _claim(world)
    assert (await world["runner"].run(claim)).status == "completed"
    [result] = world["fake"].requests[1].messages[-1].content
    assert result.is_error and "unknown tool 'nonexistent'" in result.content
    assert [s.kind for s in await _steps(world, claim.run.id)] == ["think", "think", "evaluate"]


async def test_behavior_naming_an_unregistered_tool_is_a_visible_failure(world):
    world["behaviors"]._by_key.clear()
    world["behaviors"].register(
        AgentBehavior(
            role=ROLE, capability="reasoning", system_prompt="x", output_model=Note,
            tools=("web_search", "not_registered"),
        )
    )  # fmt: skip
    task = await _task(world)
    outcome = await world["runner"].run(await _claim(world))
    assert (outcome.status, outcome.error_class) == ("failed", "UnknownTool")
    assert "not_registered" in outcome.message
    assert (await _get(world, Task, task.id)).state == "READY"
    assert world["fake"].requests == []


async def test_policy_denial_aborts_the_run(world):
    _script(world, 1, _use("forbidden", query="x"))
    task = await _task(world)
    claim = await _claim(world)
    outcome = await world["runner"].run(claim)
    assert (outcome.status, outcome.error_class) == ("aborted", "PolicyDenied")
    assert "no rule lets researcher forbidden" in outcome.message
    assert (await _get(world, Task, task.id)).state == "FAILED"
    assert (await _get(world, AgentRun, claim.run.id)).state == "ABORTED"
    activity = await _get(world, AgentActivity, world["agent"].id)
    assert activity.state == ActivityState.FAILED and activity.detail["reason"] == "policy"
    events = [e for e, _ in await _activity_events(world, claim.run.id)]
    assert events[-1] == "AGENT_RUN_ABORTED"
    async with world["committed"]() as session:
        denied = await session.scalar(
            select(EventRecord).where(
                EventRecord.run_id == claim.run.id, EventRecord.event_type == "POLICY_DENIED"
            )
        )
    assert denied.payload["action"] == "forbidden"


# --- approvals: suspend, resume with the same conversation ---------------------------------


async def test_irreversible_tool_waits_for_a_human_then_resumes(world):
    _script(
        world,
        1,
        _use("web_search", query="x"),
        FakeTurn(
            text="Publishing.",
            tool_uses=[
                FakeToolUse(name="web_search", input={"query": "again"}),
                FakeToolUse(name="publish", input={"story": "s"}),
                FakeToolUse(name="web_search", input={"query": "after"}),
            ],
        ),
        _turn(story="s", sources=["https://x"]),
    )
    task = await _task(world)
    claim = await _claim(world)

    outcome = await world["runner"].run(claim)
    assert (outcome.status, outcome.error_class) == ("suspended", "NeedsApproval")
    assert (await _get(world, Task, task.id)).state == "WAITING_APPROVAL"
    assert (await _get(world, AgentRun, claim.run.id)).state == "WAITING_APPROVAL"
    activity = await _get(world, AgentActivity, world["agent"].id)
    assert activity.state == ActivityState.WAITING and activity.detail["reason"] == "approval"
    assert world["calls"]["publish"] == [] and len(world["calls"]["web_search"]) == 2
    async with world["committed"]() as session:
        approval = await session.scalar(select(Approval).where(Approval.run_id == claim.run.id))
    assert approval.state == "PENDING" and approval.action == "publish"
    assert approval.payload["args"] == {"story": "s"}
    assert approval.summary.startswith("Rae wants to publish")
    steps = await _steps(world, claim.run.id)
    assert [s.kind for s in steps] == ["think", "act", "think", "act", "observe"]

    # The agent cannot pick anything up until the decision.
    with pytest.raises(AgentBusy, match="waiting for an approval"):
        await _claim(world, "worker-1")

    async with world["committed"]() as session:
        await world["runner"].approvals.decide(
            session, approval.id, outcome="approve", actor=OPERATOR
        )
        await session.commit()

    claim2 = await _claim(world, "worker-3")
    assert claim2.resumed and claim2.run.id == claim.run.id
    outcome = await world["runner"].run(claim2)
    assert outcome.status == "completed", outcome
    assert world["calls"]["publish"] == [{"story": "s"}], "ran exactly once, after approval"
    assert len(world["calls"]["web_search"]) == 3, "the call after the approved one ran too"

    # The resumed run continued the same conversation: the final call saw all three results.
    final_request = world["fake"].requests[-1]
    assert [m.role for m in final_request.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    results = final_request.messages[-1].content
    assert [r.tool_use_id for r in results] == [
        u.id for u in final_request.messages[-2].content if u.type == "tool_use"
    ]
    assert json.loads(results[1].content) == {"published": True}
    steps = await _steps(world, claim.run.id)
    assert [s.kind for s in steps] == [
        "think", "act", "think", "act", "observe", "act", "act", "think", "evaluate",
    ]  # fmt: skip
    events = [e for e, _ in await _activity_events(world, claim.run.id)]
    assert events.count("AGENT_RUN_STARTED") == 1
    assert events[-4:] == [
        "AGENT_WORKING",
        "AGENT_THINKING",
        "AGENT_REVIEWING",
        "AGENT_RUN_COMPLETED",
    ]


async def test_rejected_approval_cancels_and_nothing_resumes(world):
    _script(world, 1, _use("publish", story="s"))
    task = await _task(world)
    claim = await _claim(world)
    assert (await world["runner"].run(claim)).status == "suspended"
    async with world["committed"]() as session:
        approval = await session.scalar(select(Approval).where(Approval.run_id == claim.run.id))
        await world["runner"].approvals.decide(
            session, approval.id, outcome="reject", actor=OPERATOR, reason="no"
        )
        await session.commit()
    assert (await _get(world, Task, task.id)).state == "CANCELLED"
    assert await _claim(world) is None
    assert world["calls"]["publish"] == []


# --- budget, provider errors, refusal, limits ----------------------------------------------


async def test_budget_exhausted_aborts_and_blocks_the_task(world):
    async with world["committed"]() as session:
        agent = await session.get(Agent, world["agent"].id)
        agent.budget = {"per_run_usd": 0.0001}
        await session.commit()
    _script(world, 1, _turn(story="s", sources=["https://x"]))
    task = await _task(world)
    claim = await _claim(world)

    outcome = await world["runner"].run(claim)
    assert (outcome.status, outcome.error_class) == ("aborted", "BudgetExceeded")
    assert "run budget exceeded" in outcome.message
    assert (await _get(world, Task, task.id)).state == "BLOCKED_BUDGET"
    assert (await _get(world, AgentRun, claim.run.id)).state == "ABORTED"
    activity = await _get(world, AgentActivity, world["agent"].id)
    assert activity.state == ActivityState.WAITING and activity.detail["reason"] == "budget"
    assert world["fake"].requests == [], "the model was never called"
    events = await _activity_events(world, claim.run.id)
    assert [e for e, _ in events] == ["AGENT_RUN_STARTED", "AGENT_THINKING", "AGENT_RUN_ABORTED"]
    assert events[-1][1]["reason"] == "budget"


async def test_provider_failure_fails_the_run_retryably(world):
    _script(world, 1, FakeTurn(error="Overloaded", fallback_allowed=False))
    task = await _task(world)
    claim = await _claim(world)
    outcome = await world["runner"].run(claim)
    assert (outcome.status, outcome.error_class, outcome.will_retry) == (
        "failed", "ProviderError", True,
    )  # fmt: skip
    assert "Overloaded" in outcome.message
    assert (await _get(world, Task, task.id)).state == "READY"


async def test_max_steps_exceeded(world):
    _script(world, 1, *[_use("web_search", query=str(i)) for i in range(5)])
    task = await _task(world)
    claim = await _claim(world)
    outcome = await world["runner"].run(claim)
    assert (outcome.status, outcome.error_class) == ("failed", "MaxStepsExceeded")
    assert len(world["fake"].requests) == 4
    assert (await _get(world, Task, task.id)).state == "READY"


async def test_missing_behavior_fails_the_run(world):
    task = await _task(world)
    claim = await _claim(world)
    world["behaviors"]._by_key.clear()
    outcome = await world["runner"].run(claim)
    assert (outcome.status, outcome.error_class) == ("failed", "UnknownBehavior")
    assert (await _get(world, Task, task.id)).state == "READY"
    with pytest.raises(UnknownBehavior):
        world["behaviors"].resolve(ROLE, "research")


async def test_lease_lost_mid_run_writes_nothing_more(world):
    _script(world, 1, _use("slow", query="x"), _turn(story="s", sources=["https://x"]))
    task = await _task(world, max_attempts=1)
    claim = await _claim(world)
    outcome = await world["runner"].run(claim)
    assert (outcome.status, outcome.error_class) == ("lost", "LeaseLost")
    assert world["calls"]["slow"] == [{"query": "x"}]
    assert len(world["fake"].requests) == 1, "no model call after the lease was reaped"
    run = await _get(world, AgentRun, claim.run.id)
    assert run.state == "ABORTED" and run.error["error_class"] == "Aborted:timeout"
    assert (await _get(world, Task, task.id)).state == "FAILED"
    steps = await _steps(world, claim.run.id)
    assert [s.kind for s in steps] == ["think"], "the act step was not recorded"
    activity = await _get(world, AgentActivity, world["agent"].id)
    assert activity.state == ActivityState.FAILED and activity.detail["reason"] == "timeout"
