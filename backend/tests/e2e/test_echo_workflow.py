"""T-213 / Phase 2 acceptance: EchoWorkflow end to end through the real worker composition.

Start EchoWorkflow (A -> B -> C, three roles); the worker built by ``autora.app.build_worker``
runs it with the fake model. Then:
- ``trace(run)`` of each run shows THINKING -> WORKING (TOOL_CALLED / TOOL_COMPLETED) ->
  REVIEWING -> COMPLETED;
- each agent's activity changed in that order;
- B was WAITING{upstream} until A completed;
- every hand-off (TASK_SUCCEEDED.unlocks) names the next desk.
"""

import json

from sqlalchemy import func, select

from autora.app import build_worker
from autora.db.models import (
    ActivityState,
    AgentActivity,
    AgentRun,
    EventRecord,
    ModelCall,
    Task,
    WorkflowRun,
)
from autora.domains.echo.models import EchoNote
from autora.infra.blobstore import LocalFSBlobStore
from autora.runtime.trace.query import get_run_trace
from tests.e2e.conftest import start_echo

ORDER = ("echo_research", "echo_analyze", "echo_write")


async def _events(committed, **where) -> list[EventRecord]:
    async with committed() as session:
        stmt = select(EventRecord).order_by(EventRecord.seq)
        for column, value in where.items():
            stmt = stmt.where(getattr(EventRecord, column) == value)
        return list((await session.scalars(stmt)).all())


async def test_echo_workflow_end_to_end(committed, e2e_settings, echo_company):
    blobs = LocalFSBlobStore(e2e_settings.blob_store_dir)
    worker = build_worker(
        e2e_settings,
        session_factory=committed,
        blobs=blobs,
        company_ids=frozenset({echo_company.company.id}),
    )
    wf, tasks = await start_echo(committed, echo_company)
    agents = echo_company.agents

    # Before any work: A is ready, B and C wait on their upstream desks.
    async with committed() as session:
        states = {
            role: (await session.get(AgentActivity, agent.id)).state
            for role, agent in agents.items()
        }
    assert states == {
        "researcher": ActivityState.IDLE,
        "analyst": ActivityState.WAITING,
        "writer": ActivityState.WAITING,
    }

    await worker.run_until_idle()

    async with committed() as session:
        wf_row = await session.get(WorkflowRun, wf.id)
        rows = {name: await session.get(Task, tasks[name].id) for name in ORDER}
        runs = {
            name: (await session.scalars(select(AgentRun).where(AgentRun.task_id == t.id))).all()
            for name, t in rows.items()
        }
        notes = (
            await session.scalars(
                select(EchoNote).where(EchoNote.company_id == echo_company.company.id)
            )
        ).all()
        calls = await session.scalar(
            select(func.count()).where(ModelCall.company_id == echo_company.company.id)
        )
        traces = {name: await get_run_trace(session, runs[name][0].id) for name in ORDER}

    assert wf_row.state == "SUCCEEDED"
    assert [rows[n].state for n in ORDER] == ["SUCCEEDED"] * 3
    assert all(len(r) == 1 and r[0].state == "COMPLETED" for r in runs.values())
    assert sorted(n.role for n in notes) == ["analyst", "researcher", "writer"]
    assert calls == 6, "two model calls per desk: tool call, then the report"
    for name in ORDER:
        output = rows[name].output
        note = next(n for n in notes if n.task_id == rows[name].id)
        assert output == {"note_id": str(note.id), "message": note.text}

    # trace(run): the acceptance sequence, real events joined with real steps.
    for name in ORDER:
        trace = traces[name]
        kinds = [e.event_type for e in trace.entries]
        assert kinds == [
            "TASK_STARTED",
            "AGENT_RUN_STARTED",
            "AGENT_THINKING",
            "AGENT_WORKING",
            "TOOL_CALLED",
            "TOOL_COMPLETED",
            "AGENT_THINKING",
            "AGENT_REVIEWING",
            "TASK_SUCCEEDED",
            "AGENT_RUN_COMPLETED",
        ], name
        assert [s.kind for s in trace.steps] == ["think", "act", "think", "evaluate"]
        working = trace.entries[3]
        assert working.payload["tool"] == "echo_note" and working.step.kind == "act"
        completed_tool = trace.entries[5].payload
        assert completed_tool["produced"][0]["type"] == "echo_note"

    # Hand-offs: A -> analyst, B -> writer, C -> nobody.
    succeeded = {
        e.task_id: e.payload
        for e in await _events(committed, workflow_run_id=wf.id, event_type="TASK_SUCCEEDED")
    }
    assert succeeded[rows["echo_research"].id]["unlocks"] == [
        {"task_id": str(rows["echo_analyze"].id), "required_role": "analyst"}
    ]
    assert succeeded[rows["echo_analyze"].id]["unlocks"] == [
        {"task_id": str(rows["echo_write"].id), "required_role": "writer"}
    ]
    assert succeeded[rows["echo_write"].id]["unlocks"] == []

    # Activity, per agent, in order; B waits on upstream until A has completed.
    by_agent = {
        role: [
            (e.seq, e.event_type, e.payload)
            for e in await _events(committed, agent_id=agent.id)
            if e.event_type.startswith("AGENT_") and e.event_type != "AGENT_RUN_STARTED"
        ]
        for role, agent in agents.items()
    }
    assert [t for _, t, _ in by_agent["researcher"]] == [
        "AGENT_CREATED", "AGENT_IDLE", "AGENT_THINKING", "AGENT_WORKING", "AGENT_THINKING",
        "AGENT_REVIEWING", "AGENT_RUN_COMPLETED",
    ]  # fmt: skip
    for role in ("analyst", "writer"):
        assert [t for _, t, _ in by_agent[role]] == [
            "AGENT_CREATED", "AGENT_IDLE", "AGENT_WAITING", "AGENT_THINKING", "AGENT_WORKING",
            "AGENT_THINKING", "AGENT_REVIEWING", "AGENT_RUN_COMPLETED",
        ], role  # fmt: skip
    waiting_seq, _, waiting = by_agent["analyst"][2]
    assert waiting["reason"] == "upstream" and waiting["waiting_on_roles"] == ["researcher"]
    a_completed_seq = by_agent["researcher"][-1][0]
    b_thinking_seq = by_agent["analyst"][3][0]
    assert waiting_seq < a_completed_seq < b_thinking_seq
    assert by_agent["researcher"][-1][2]["handoff"] == [
        {"to_role": "analyst", "task_id": str(rows["echo_analyze"].id)}
    ]

    # B's prompt contained A's report (OBSERVE through the domain's context hook).
    analyst_trace = traces["echo_analyze"]
    first_step = await blobs.get(
        f"companies/{echo_company.company.id}/runs/{analyst_trace.run_id}/steps/00000.json"
    )
    prompt = json.loads(first_step)["request"]["messages"][0]["content"][0]["text"]
    assert "Upstream notes:" in prompt and rows["echo_research"].output["message"] in prompt


async def test_worker_is_idle_without_work(committed, e2e_settings, echo_company):
    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({echo_company.company.id})
    )
    assert await worker.tick() == 0
    await worker.run_until_idle()


async def test_policy_override_makes_the_writer_wait_for_a_human(
    committed, e2e_settings, echo_company
):
    """The runbook's approval walk-through: a company override tightens echo_note for writers."""
    from autora.app import build_runtime
    from autora.db.models import Approval
    from autora.db.repositories.companies import upsert_policy
    from tests.e2e.conftest import OPERATOR

    company_id = echo_company.company.id
    async with committed() as session:
        await upsert_policy(
            session,
            company_id,
            "policy.overrides",
            {"echo_note": {"writer": "needs_approval"}},
            updated_by=OPERATOR.as_json(),
        )
        await session.commit()
    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company_id})
    )
    wf, tasks = await start_echo(committed, echo_company)

    await worker.run_until_idle()
    async with committed() as session:
        approval = await session.scalar(select(Approval).where(Approval.company_id == company_id))
        writer = await session.get(AgentActivity, echo_company.agents["writer"].id)
        states = [(await session.get(Task, tasks[n].id)).state for n in ORDER]
    assert states == ["SUCCEEDED", "SUCCEEDED", "WAITING_APPROVAL"]
    assert approval.action == "echo_note" and approval.state == "PENDING"
    assert writer.state == ActivityState.WAITING and writer.detail["reason"] == "approval"

    async with committed() as session:
        await build_runtime().approvals.decide(
            session, approval.id, outcome="approve", actor=OPERATOR
        )
        await session.commit()
    await worker.run_until_idle()
    async with committed() as session:
        assert (await session.get(WorkflowRun, wf.id)).state == "SUCCEEDED"
        runs = (
            await session.scalars(
                select(AgentRun).where(AgentRun.task_id == tasks["echo_write"].id)
            )
        ).all()
    assert [r.state for r in runs] == ["COMPLETED"], "the same run resumed after approval"
