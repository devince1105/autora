"""T-302: projection contract. snapshot(t0) + events(t0, t1] == snapshot(t1).

Histories are random but real: every event comes from the real task manager, workflow engine,
approval service and activity service, each operation in its own committed transaction, exactly
as workers and the API produce them. Snapshots are taken at random points; the reference reducer
must turn every snapshot plus the events after it into every later snapshot.

``AUTORA_REALTIME_FIXTURE_OUT=<path>`` writes one history (first snapshot, events, last
snapshot) as JSON for the TypeScript reducer's cross-language test (T-305).
"""

import json
import os
import random
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from autora.app import build_runtime
from autora.company.agents import hire_agent
from autora.company.cycle import CycleRunner
from autora.company.workflows import start_workflow
from autora.db.models import Agent, AgentActivity, Approval, ApprovalKind, ApprovalState, Task
from autora.domains import echo
from autora.realtime.projection import begin_consistent_read, load_snapshot
from autora.realtime.reducer import RealtimeState, canonical
from autora.runtime.activity import set_activity
from autora.runtime.actor import Actor
from autora.runtime.dag import NodeSpec, WorkflowTemplate
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import load_events
from autora.runtime.task_manager import AgentBusy, Claim

OPERATOR = Actor.human("operator")
REVIEW = WorkflowTemplate(
    name="contract.review_v1",
    nodes=(
        NodeSpec("draft", "Draft {topic}", "writer"),
        NodeSpec("approve", "Approve {topic}", "human", depends_on=("draft",)),
        NodeSpec("publish", "Publish {topic}", "analyst", depends_on=("approve",)),
    ),
)
SEEDS = range(8)
STEPS = 120
ATTEMPTS = 6
"""Draws per step. An operation that is illegal right now costs a draw, not the step: a
history in which nothing happens for twenty steps exercises nothing."""

DOMAIN_KIND = "shipment"
"""A kind this domain-less layer has never heard of. The runtime stores the token and
asks a person about it; what it means belongs to whoever asked (ARCHITECTURE_V2_1 §9)."""


@dataclass
class History:
    committed: object
    company_id: uuid.UUID
    project_id: uuid.UUID
    rng: random.Random
    runtime: object = field(default_factory=build_runtime)
    claims: dict[uuid.UUID, Claim] = field(default_factory=dict)
    cycles: CycleRunner = field(default_factory=CycleRunner)
    """No hooks and no completion checks: the days open and close on their own, which is all
    this contract needs from them (T-608). What happens inside a cycle is tested elsewhere."""

    def __post_init__(self):
        self.runtime.templates.register(REVIEW)

    async def step(self) -> str:
        weighted = {
            self.start_workflow: 2, self.claim: 4, self.work: 4, self.finish: 5,
            self.decide: 3, self.human_node: 2, self.cancel: 1, self.pause: 1, self.resume: 2,
            self.reap: 0.5, self.release_blocked: 1, self.cycle: 1.5,
        }  # fmt: skip
        ops, weights = list(weighted), list(weighted.values())
        skipped = ""
        for _ in range(ATTEMPTS):
            [op] = self.rng.choices(ops, weights=weights)
            async with self.committed() as session:
                try:
                    outcome = await op(session)
                    await session.commit()
                except Exception as exc:  # noqa: BLE001 - an illegal move is not a bug
                    await session.rollback()
                    skipped = f"{op.__name__}: skipped ({type(exc).__name__})"
                    continue
            return f"{op.__name__}: {outcome}"
        return skipped  # every draw was illegal in this state: a rare, genuinely idle step

    # --- operations ------------------------------------------------------------------------

    async def start_workflow(self, session):
        template = self.rng.choice([echo.TEMPLATE.name, REVIEW.name])
        run, _ = await start_workflow(
            session, policy=self.runtime.policy, workflows=self.runtime.workflows,
            company_id=self.company_id, project_id=self.project_id, template=template,
            params={"topic": f"t{self.rng.randrange(1000)}"}, actor=OPERATOR,
        )  # fmt: skip
        return f"{template} {run.id}"

    async def claim(self, session):
        """Like a worker tick: the first agent (in random order) that finds work takes it."""
        agents = await self._agents(session, paused=False)
        self.rng.shuffle(agents)
        busy = {c.agent.id for c in self.claims.values()}
        for agent in agents:
            if agent.id in busy:
                continue
            async with session.begin_nested():
                try:
                    claim = await self.runtime.task_manager.claim_next(session, agent, "w")
                except AgentBusy:
                    continue
            if claim is not None:
                self.claims[claim.run.id] = claim
                return claim.task.name
        raise LookupError("nothing to claim")

    async def work(self, session):
        claim = self._running()
        payload = self.rng.choice([
            ev.AgentThinking(phase="plan", step_seq=0),
            ev.AgentWorking(tool="echo_note", tool_call_id="c1", step_seq=1),
            ev.AgentReviewing(phase="evaluate", attempt=1, issues_count=0),
        ])  # fmt: skip
        await self.runtime.task_manager.heartbeat(session, claim)
        agent = await session.get(Agent, claim.agent.id)
        await set_activity(
            session, agent, payload, actor=Actor.system("agent_runner"), run_id=claim.run.id,
            task_id=claim.task.id, workflow_run_id=claim.task.workflow_run_id,
            task_name=claim.task.display_name,
        )  # fmt: skip
        return payload.event_type

    async def finish(self, session):
        claim = self._running()
        tm = self.runtime.task_manager
        choice = self.rng.choice(
            ["succeed", "succeed", "fail_retry", "fail_final", "budget", "policy", "human",
             "approval"]
        )  # fmt: skip
        if choice == "succeed":
            await tm.succeed(session, claim, {"ok": True}, output_summary="done")
        elif choice in ("fail_retry", "fail_final"):
            await tm.fail(
                session, claim, error_class="Boom", message="x", retryable=choice == "fail_retry"
            )
        elif choice in ("budget", "policy", "human"):
            await tm.abort(session, claim, reason=choice, message=choice)
        else:
            await self.runtime.approvals.request_for_run(
                session, claim, kind=ApprovalKind.TOOL_CALL, action="echo_note",
                payload={"tool_call_id": "c1"}, summary="needs a human",
            )  # fmt: skip
        self.claims.pop(claim.run.id)
        return choice

    async def decide(self, session):
        pending = (
            await session.scalars(
                select(Approval).where(
                    Approval.company_id == self.company_id,
                    Approval.state == ApprovalState.PENDING,
                )
            )
        ).all()
        approval = self.rng.choice(pending)
        outcome = self.rng.choice(["approve", "approve", "reject"])
        await self.runtime.approvals.decide(session, approval.id, outcome=outcome, actor=OPERATOR)
        return outcome

    async def human_node(self, session):
        task = self.rng.choice(await self._tasks(session, required_role="human", state="READY"))
        await self.runtime.approvals.request_for_task(
            session, task, kind=DOMAIN_KIND, summary="approve?"
        )
        return task.display_name

    async def cancel(self, session):
        states = ["PENDING", "READY", "RUNNING", "WAITING_APPROVAL", "BLOCKED_BUDGET"]
        task = self.rng.choice(await self._tasks(session, state=states))
        await self.runtime.task_manager.cancel(session, task, reason="operator")
        for run_id, claim in list(self.claims.items()):
            if claim.task.id == task.id:
                self.claims.pop(run_id)
        return task.display_name

    async def pause(self, session):
        """Also mid-run: the task manager must still end the run (the agent stays PAUSED)."""
        working = await self._agents(session, paused=False)
        agent = self.rng.choice(working)
        if len(working) == 1:
            # never pause the last one: a company where nobody can work produces no history,
            # and a history of nothing tests nothing
            raise LookupError("the last unpaused agent")
        await set_activity(session, agent, ev.AgentPaused(reason="operator"), actor=OPERATOR)
        return agent.role

    async def resume(self, session):
        agent = self.rng.choice(await self._agents(session, paused=True))
        await set_activity(session, agent, ev.AgentResumed(), actor=OPERATOR)
        return agent.role

    async def reap(self, session):
        tm = self.runtime.task_manager
        tm.clock = lambda: _now() + timedelta(hours=1)  # every open lease has expired
        try:
            reaped = await tm.reap_expired_leases(session)
        finally:
            tm.clock = _now
        for run_id, claim in list(self.claims.items()):
            if claim.task.id in reaped:
                self.claims.pop(run_id)
        if not reaped:
            raise LookupError("no lease to reclaim")
        return len(reaped)

    async def cycle(self, session):
        """The company's day: open one when none is open, otherwise move it along."""
        open_cycle = await self.cycles.open_cycle(session, self.company_id, lock=True)
        if open_cycle is None:
            started = await self.cycles.start(session, self.company_id)
            return f"started {started.seq}"
        moved = await self.cycles.tick(session, self.company_id)
        if moved is None:
            raise LookupError("the cycle has nowhere to go yet")
        return f"{moved.seq} -> {moved.stage}"

    async def release_blocked(self, session):
        released = await self.runtime.task_manager.release_blocked(session, self.company_id)
        if not released:
            raise LookupError("nothing blocked")
        return released

    # --- helpers ---------------------------------------------------------------------------

    def _running(self) -> Claim:
        return self.claims[self.rng.choice(list(self.claims))]

    async def _agents(self, session, paused=None):
        stmt = (
            select(Agent)
            .join(AgentActivity, AgentActivity.agent_id == Agent.id)
            .where(Agent.company_id == self.company_id)
            .order_by(Agent.created_at, Agent.id)
        )
        if paused is not None:
            op = AgentActivity.state.__eq__ if paused else AgentActivity.state.__ne__
            stmt = stmt.where(op("PAUSED"))
        return list((await session.scalars(stmt)).all())

    async def _tasks(self, session, *, state, required_role=None):
        states = [state] if isinstance(state, str) else state
        stmt = select(Task).where(Task.company_id == self.company_id, Task.state.in_(states))
        if required_role:
            stmt = stmt.where(Task.required_role == required_role)
        return list((await session.scalars(stmt.order_by(Task.created_at))).all())


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


async def _snapshot(committed, company_id, now=None):
    async with committed() as session:
        await begin_consistent_read(session)
        return await load_snapshot(session, company_id, now=now)


async def _events(committed, company_id, after, until):
    async with committed() as session:
        return await load_events(
            session, company_id, after_seq=after, until_seq=until, limit=100_000
        )


def _replay(start, events, now):
    state = RealtimeState.from_snapshot(start)
    for event in events:
        state.apply(event)
    return state.view(now)


def _diff(expected, actual) -> str:
    a, b = canonical(expected), canonical(actual)
    lines = []
    for key in a:
        if a[key] != b[key]:
            if isinstance(a[key], dict):
                for item in set(a[key]) | set(b[key]):
                    if a[key].get(item) != b[key].get(item):
                        lines.append(f"{key}[{item}]:\n  snapshot {a[key].get(item)}\n"
                                     f"  reducer  {b[key].get(item)}")  # fmt: skip
            else:
                lines.append(f"{key}: snapshot {a[key]} / reducer {b[key]}")
    return "\n".join(lines[:6])


@pytest.mark.parametrize("seed", SEEDS)
async def test_reducer_reproduces_every_snapshot(committed, echo_company, seed):
    async with committed() as session:  # a second desk per role: runs overlap, claims compete
        for role in ("researcher", "analyst", "writer"):
            await hire_agent(
                session, company_id=echo_company.company.id, role=role,
                display_name=f"{role.title()} 2", actor=OPERATOR, avatar_key=f"{role}_b",
            )  # fmt: skip
        await session.commit()
    history = History(
        committed, echo_company.company.id, echo_company.project.id, random.Random(seed)
    )
    log: list[str] = []
    snapshots = [await _snapshot(committed, history.company_id)]
    for _ in range(STEPS):
        log.append(await history.step())
        if history.rng.random() < 0.25:
            snapshots.append(await _snapshot(committed, history.company_id))
    snapshots.append(await _snapshot(committed, history.company_id))

    applied = [line for line in log if "skipped" not in line]
    kinds = Counter(
        line.split(":")[0] + (":" + line.split(": ")[1] if line.startswith("finish") else "")
        for line in applied
    )
    skipped = Counter(line.split(":")[0] for line in log if "skipped" in line)
    print(f"\nseed {seed}: {len(applied)} applied, {len(snapshots)} snapshots, {dict(kinds)}")
    print(f"        skipped: {dict(skipped)}")
    assert len(applied) >= STEPS // 4, f"history too thin to mean anything:\n{log}"
    assert len(kinds) >= 10, f"history too uniform: {dict(kinds)}"

    for i, start in enumerate(snapshots[:-1]):
        for end in (snapshots[i + 1], snapshots[-1]):
            events = await _events(committed, history.company_id, start.last_seq, end.last_seq)
            replayed = _replay(start, events, end.server_time)
            assert canonical(replayed) == canonical(end), (
                f"seed {seed}, snapshot {i} -> {snapshots.index(end)}:\n{_diff(end, replayed)}\n"
                f"history:\n" + "\n".join(log)
            )

    # The clock rules agree too: eleven minutes later, from the very first snapshot.
    later = snapshots[-1].server_time + timedelta(minutes=11)
    events = await _events(
        committed, history.company_id, snapshots[0].last_seq, snapshots[-1].last_seq
    )
    assert canonical(_replay(snapshots[0], events, later)) == canonical(
        await _snapshot(committed, history.company_id, now=later)
    )

    out = os.environ.get("AUTORA_REALTIME_FIXTURE_OUT")
    if out and seed == 0:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(
            json.dumps(
                {
                    "description": "T-302 contract: hydrate(snapshot_before) + events == "
                    "snapshot_after (views compared at snapshot_after.server_time)",
                    "snapshot_before": snapshots[0].model_dump(mode="json"),
                    "events": [e.model_dump(mode="json") for e in events],
                    "snapshot_after": snapshots[-1].model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=1,
            )
        )


async def test_duplicates_ephemeral_and_foreign_events_are_ignored(committed, echo_company):
    snapshot = await _snapshot(committed, echo_company.company.id)
    state = RealtimeState.from_snapshot(snapshot)
    before = canonical(state.view(snapshot.server_time))
    for event in snapshot.recent_events[-3:]:
        assert state.apply(event) is False, "seq <= last_seq"
    foreign = snapshot.recent_events[-1].model_copy(
        update={"company_id": uuid.uuid4(), "seq": snapshot.last_seq + 1}
    )
    assert state.apply(foreign) is False
    assert canonical(state.view(snapshot.server_time)) == before
