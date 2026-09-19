"""T-514: service nodes (steps no agent runs) and workflow loops (another round of tasks)."""

import pytest
from sqlalchemy import select

from autora.db.models import (
    ApprovalKind,
    EventRecord,
    Project,
    Task,
    WorkflowRun,
)
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from autora.runtime.dag import (
    InvalidTemplate,
    Loop,
    NodeSpec,
    TemplateRegistry,
    WorkflowEngine,
    WorkflowTemplate,
)
from autora.runtime.policy import PolicyEngine
from autora.runtime.services import ServiceDispatcher, ServiceRegistry
from autora.runtime.task_manager import TaskManager
from tests.conftest import unique_company

HUMAN = Actor.human("operator")


def _loop_template(max_rounds=2):
    return WorkflowTemplate(
        name="test.loop",
        nodes=(
            NodeSpec("gather", "Gather", "system", service="t.done"),
            NodeSpec("write", "Write", "system", depends_on=("gather",), service="t.write"),
            NodeSpec("check", "Check", "system", depends_on=("write",), service="t.check"),
            NodeSpec("ship", "Ship", "system", depends_on=("check",), service="t.done"),
        ),
        loops=(
            Loop(
                check="check",
                back_to="write",
                again=lambda out: out.get("verdict") == "again",
                carry=lambda out: {"notes": out.get("notes")},
                max_rounds=max_rounds,
                round_label="{name} #{round}",
            ),
        ),
    )


class World:
    def __init__(self, committed, template, handlers):
        self.committed = committed
        self.task_manager = TaskManager()
        templates = TemplateRegistry()
        templates.register(template)
        self.engine = WorkflowEngine(self.task_manager, templates)
        self.approvals = ApprovalService(self.task_manager)
        registry = ServiceRegistry()
        for name, handler in handlers.items():
            registry.register(name, handler)
        self.company = None
        self.registry = registry

    async def start(self, template_name, params=None):
        async with self.committed() as session:
            self.company = await unique_company(session, "services")
            project = Project(
                company_id=self.company.id,
                name="p",
                state="ACTIVE",
                kill_criteria={"max_cost_usd": 1},
            )
            session.add(project)
            await session.flush()
            run, _ = await self.engine.instantiate(
                session,
                template_name,
                company_id=self.company.id,
                project_id=project.id,
                params=params or {},
            )
            await session.commit()
        self.dispatcher = ServiceDispatcher(
            session_factory=self.committed,
            registry=self.registry,
            task_manager=self.task_manager,
            approvals=self.approvals,
            policy=PolicyEngine(),
            company_ids=frozenset({self.company.id}),
        )
        return run

    async def drain(self):
        total = 0
        while handled := await self.dispatcher.dispatch():
            total += handled
        return total

    async def tasks(self, run):
        async with self.committed() as session:
            return list(
                (
                    await session.scalars(
                        select(Task)
                        .where(Task.workflow_run_id == run.id)
                        .order_by(Task.created_at, Task.id)
                    )
                ).all()
            )

    async def run_state(self, run):
        async with self.committed() as session:
            return (await session.get(WorkflowRun, run.id)).state


async def done(ctx):
    await ctx.complete({"ok": True})


async def write(ctx):
    await ctx.complete({"wrote_with": ctx.params.get("notes")})


def checker(verdicts):
    """A check that answers the given verdicts in turn."""
    answers = iter(verdicts)

    async def check(ctx):
        verdict = next(answers)
        await ctx.complete({"verdict": verdict, "notes": f"fix {verdict}"})

    return check


async def test_service_steps_run_the_workflow_to_the_end(committed):
    world = World(
        committed, _loop_template(), {"t.done": done, "t.write": write, "t.check": checker(["ok"])}
    )
    run = await world.start("test.loop")
    assert await world.drain() == 4
    tasks = await world.tasks(run)
    assert [t.state for t in tasks] == ["SUCCEEDED"] * 4
    assert tasks[0].input["service"] == "t.done" and tasks[0].output == {"ok": True}
    assert await world.run_state(run) == "SUCCEEDED"


async def test_a_loop_adds_rounds_and_the_downstream_waits_for_them(committed):
    world = World(
        committed,
        _loop_template(),
        {"t.done": done, "t.write": write, "t.check": checker(["again", "again", "ok"])},
    )
    run = await world.start("test.loop")
    await world.drain()
    tasks = await world.tasks(run)
    names = [t.name for t in tasks]
    assert names == ["gather", "write", "check", "ship", "write", "check", "write", "check"]
    assert all(t.state == "SUCCEEDED" for t in tasks)
    by_round = [t for t in tasks if t.name in ("write", "check")]
    assert [t.display_name for t in by_round] == [
        "Write", "Check", "Write #2", "Check #2", "Write #3", "Check #3",
    ]  # fmt: skip
    # each new write depends on gather, and carries the check's notes
    gather = tasks[0]
    assert by_round[2].depends_on == [gather.id] and by_round[4].depends_on == [gather.id]
    assert by_round[2].input["params"] == {"notes": "fix again"}
    assert by_round[2].output == {"wrote_with": "fix again"}
    assert by_round[3].depends_on == [by_round[2].id]
    # ship waited for the last check
    ship = tasks[3]
    assert ship.depends_on == [by_round[5].id]
    async with committed() as session:
        extended = (
            await session.scalars(
                select(EventRecord.payload).where(
                    EventRecord.workflow_run_id == run.id,
                    EventRecord.event_type == "WORKFLOW_RUN_EXTENDED",
                )
            )
        ).all()
    assert [e["round"] for e in extended] == [2, 3]
    assert extended[0]["task_ids"] == [str(by_round[2].id), str(by_round[3].id)]
    assert await world.run_state(run) == "SUCCEEDED"


async def test_a_loop_past_its_rounds_cancels_what_waited(committed):
    world = World(
        committed,
        _loop_template(max_rounds=1),
        {"t.done": done, "t.write": write, "t.check": checker(["again", "again"])},
    )
    run = await world.start("test.loop")
    await world.drain()
    tasks = await world.tasks(run)
    ship = next(t for t in tasks if t.name == "ship")
    assert [t.name for t in tasks].count("check") == 2
    assert ship.state == "CANCELLED"
    assert await world.run_state(run) == "CANCELLED"
    async with committed() as session:
        cancelled = await session.scalar(
            select(EventRecord.payload).where(
                EventRecord.task_id == ship.id, EventRecord.event_type == "TASK_CANCELLED"
            )
        )
    assert "still asks for another round after 1 rounds" in cancelled["reason"]


async def test_a_failing_or_undecided_handler_fails_its_step(committed):
    async def broken(ctx):
        raise ValueError("no printer")

    async def idle(ctx):
        return None

    async def refuses(ctx):
        await ctx.fail("NotReady", "the thing is not ready")

    template = WorkflowTemplate(
        name="test.fail",
        nodes=(
            NodeSpec("a", "A", "system", service="t.broken"),
            NodeSpec("b", "B", "system", service="t.idle"),
            NodeSpec("c", "C", "system", service="t.refuses"),
            NodeSpec("after", "After", "system", depends_on=("a",), service="t.idle"),
        ),
    )
    world = World(committed, template, {"t.broken": broken, "t.idle": idle, "t.refuses": refuses})
    run = await world.start("test.fail")
    await world.drain()
    a, b, c, after = await world.tasks(run)
    assert a.state == b.state == c.state == "FAILED"
    assert a.output == {"error_class": "ValueError", "message": "no printer"}
    assert b.output["error_class"] == "ServiceError" and "did not complete" in b.output["message"]
    assert c.output == {"error_class": "NotReady", "message": "the thing is not ready"}
    assert after.state == "CANCELLED"  # it depended on a failed step
    assert await world.run_state(run) == "FAILED"


async def test_a_step_can_wait_for_a_person(committed):
    decided = []

    async def ask(ctx):
        await ctx.request_approval(
            kind=ApprovalKind.ARTICLE, summary="ok?", action="t.ship", payload={"x": 1}
        )

    async def hook(session, approval, outcome, actor, reason):
        decided.append((approval.payload, outcome, actor.id))

    template = WorkflowTemplate(
        name="test.ask",
        nodes=(
            NodeSpec("ask", "Ask", "human", service="t.ask"),
            NodeSpec("then", "Then", "system", depends_on=("ask",), service="t.done"),
        ),
    )
    world = World(committed, template, {"t.ask": ask, "t.done": done})
    world.approvals.on_decided("t.ship", hook)
    run = await world.start("test.ask")
    await world.drain()
    ask_task, then = await world.tasks(run)
    assert ask_task.state == "WAITING_APPROVAL" and then.state == "PENDING"
    async with committed() as session:
        from autora.db.models import Approval

        approval = await session.scalar(select(Approval).where(Approval.task_id == ask_task.id))
        assert approval.requested_by["id"] == "service:t.ask"
        await world.approvals.decide(session, approval.id, outcome="approve", actor=HUMAN)
        await session.commit()
    assert decided == [({"x": 1}, "approve", "operator")]
    await world.drain()
    assert [t.state for t in await world.tasks(run)] == ["SUCCEEDED", "SUCCEEDED"]
    with pytest.raises(Exception, match="already registered"):
        world.approvals.on_decided("t.ship", hook)


def test_loops_are_checked_with_the_template():
    nodes = (NodeSpec("a", "A", "r"), NodeSpec("b", "B", "r", depends_on=("a",)))
    with pytest.raises(InvalidTemplate, match="unknown nodes"):
        WorkflowTemplate("t.x", nodes, loops=(Loop("b", "ghost", again=bool),))
    with pytest.raises(InvalidTemplate, match="does not lead to"):
        WorkflowTemplate("t.y", nodes, loops=(Loop("a", "b", again=bool),))
    ok = WorkflowTemplate("t.z", nodes, loops=(Loop("b", "a", again=bool),))
    assert [n.name for n in ok.loop_body(ok.loops[0])] == ["a", "b"]
