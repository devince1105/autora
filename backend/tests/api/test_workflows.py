"""T-214: POST /api/companies/{id}/workflows (operator starts a workflow)."""

import uuid

import pytest
from sqlalchemy import select

from autora.company.workflows import WorkflowNotAllowed, start_workflow
from autora.db.models import (
    ActivityState,
    AgentActivity,
    EventRecord,
    PolicyDecision,
    Project,
    ProjectState,
    Task,
    WorkflowRun,
)
from autora.domains import echo
from autora.runtime.actor import Actor
from tests.conftest import unique_company

URL = "/api/companies/{}/workflows"


@pytest.fixture
async def setup(db_session):
    company = await unique_company(db_session, "wf")
    project = Project(
        company_id=company.id,
        name="Demo",
        state=ProjectState.ACTIVE.value,
        kill_criteria={"max_cost_usd": 10},
    )
    db_session.add(project)
    agents = await echo.staff_company(db_session, company.id, actor=Actor.human("setup"))
    await db_session.flush()
    return company, project, {a.role: a for a in agents}


def _body(project, **kw):
    return {
        "template": echo.TEMPLATE.name,
        "project_id": str(project.id),
        "params": {"topic": "EU AI Act"},
        **kw,
    }


async def test_operator_starts_a_workflow(api, db_session, setup):
    company, project, agents = setup
    response = await api.post(URL.format(company.id), json=_body(project))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["template_name"] == "echo.chain_v1" and body["state"] == "RUNNING"
    assert body["params"] == {"topic": "EU AI Act"}
    tasks = {t["name"]: t for t in body["tasks"]}
    assert list(tasks) == ["echo_research", "echo_analyze", "echo_write"]
    assert tasks["echo_research"]["state"] == "READY"
    assert tasks["echo_analyze"]["state"] == "PENDING"
    assert tasks["echo_analyze"]["depends_on"] == [tasks["echo_research"]["id"]]
    assert tasks["echo_research"]["display_name"] == "Echo: gather (EU AI Act)"

    run = await db_session.get(WorkflowRun, uuid.UUID(body["id"]))
    assert run is not None and run.project_id == project.id
    decision = await db_session.scalar(
        select(PolicyDecision).where(
            PolicyDecision.company_id == company.id,
            PolicyDecision.action == "instantiate_workflow",
        )
    )
    assert decision.outcome == "allow" and decision.actor == {"kind": "human", "id": "operator"}
    created = await db_session.scalar(
        select(EventRecord).where(
            EventRecord.aggregate_id == run.id, EventRecord.event_type == "WORKFLOW_RUN_CREATED"
        )
    )
    assert created.actor == {"kind": "system", "id": "workflow_engine"}
    analyst = await db_session.get(AgentActivity, agents["analyst"].id, populate_existing=True)
    assert analyst.state == ActivityState.WAITING and analyst.detail["reason"] == "upstream"


@pytest.mark.parametrize(
    ("change", "code", "message"),
    [
        ({"template": "nope.v1"}, 422, "unknown workflow template"),
        ({"params": {}}, 422, "needs parameter 'topic'"),
        ({"project_id": str(uuid.uuid4())}, 404, "not found"),
    ],
)
async def test_invalid_requests(api, db_session, setup, change, code, message):
    company, project, _ = setup
    response = await api.post(URL.format(company.id), json=_body(project, **change))
    assert response.status_code == code, response.text
    assert message in response.text
    assert await db_session.scalar(select(Task).where(Task.company_id == company.id)) is None


async def test_project_must_be_active_and_belong_to_the_company(api, db_session, setup):
    company, project, _ = setup
    other = await unique_company(db_session, "wf-other")
    response = await api.post(URL.format(other.id), json=_body(project))
    assert response.status_code == 404

    project.state = ProjectState.PAUSED.value
    await db_session.flush()
    response = await api.post(URL.format(company.id), json=_body(project))
    assert response.status_code == 422 and "PAUSED" in response.text


async def test_unknown_company_and_auth(api, setup):
    _, project, _ = setup
    assert (await api.post(URL.format(uuid.uuid4()), json=_body(project))).status_code == 404
    unauthenticated = await api.post(
        URL.format(uuid.uuid4()), json=_body(project), headers={"Authorization": "Bearer wrong"}
    )
    assert unauthenticated.status_code == 401


async def test_agents_are_held_to_their_policy(db_session, setup, runtime):
    """The same command for an agent actor: a writer may not start workflows (recorded)."""
    company, project, agents = setup
    with pytest.raises(WorkflowNotAllowed, match="deny"):
        await start_workflow(
            db_session,
            policy=runtime.policy,
            workflows=runtime.workflows,
            company_id=company.id,
            project_id=project.id,
            template=echo.TEMPLATE.name,
            params={"topic": "x"},
            actor=Actor.agent(agents["writer"].id),
            role="writer",
        )
    decision = await db_session.scalar(
        select(PolicyDecision).where(PolicyDecision.company_id == company.id)
    )
    assert decision.outcome == "deny" and decision.role == "writer"
    assert (
        await db_session.scalar(select(WorkflowRun).where(WorkflowRun.company_id == company.id))
        is None
    )


# --- starting a failed one again (AC-9) -------------------------------------------------------


async def _failed_run(db_session, company, project, *, topic="EU AI Act"):
    """A run that ended badly, the way one does: its first task failed for good."""
    from autora.app import build_runtime
    from autora.db.models import TaskState, WorkflowRunState

    runtime = build_runtime()
    run, tasks = await start_workflow(
        db_session,
        policy=runtime.policy,
        workflows=runtime.workflows,
        company_id=company.id,
        project_id=project.id,
        template=echo.TEMPLATE.name,
        params={"topic": topic},
        actor=Actor.human("setup"),
    )
    first = tasks["echo_research"]
    first.state = TaskState.FAILED.value
    first.output = {"error_class": "Boom", "message": "it broke"}
    run.state = WorkflowRunState.FAILED.value
    await db_session.flush()
    return run


async def test_the_inbox_lists_what_could_be_started_again(api, db_session, setup):
    company, project, agents = setup
    failed = await _failed_run(db_session, company, project)
    await db_session.commit()

    response = await api.get(URL.format(company.id) + "/failed")

    assert response.status_code == 200, response.text
    [row] = response.json()
    assert row["id"] == str(failed.id)
    assert row["template_name"] == "echo.chain_v1"
    assert row["failed_tasks"] == ["Echo: gather (EU AI Act)"]
    assert row["restarted"] is False
    assert row["params"] == {"topic": "EU AI Act"}


async def test_a_person_starts_it_again_through_the_pipeline(api, db_session, setup):
    """Not a back door: the restart is a command, decided and recorded like any other."""
    from autora.db.models import CommandRecord

    company, project, agents = setup
    failed = await _failed_run(db_session, company, project)
    await db_session.commit()

    response = await api.post(URL.format(company.id) + f"/{failed.id}/restart")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "done" and body["workflow_run_id"]
    fresh = await db_session.get(WorkflowRun, uuid.UUID(body["workflow_run_id"]))
    assert fresh.id != failed.id
    assert fresh.template_name == failed.template_name and fresh.params == failed.params
    # the old run keeps its history: what failed and why is the reason to keep it
    assert (await db_session.get(WorkflowRun, failed.id)).state == "FAILED"
    record = await db_session.scalar(
        select(CommandRecord).where(
            CommandRecord.company_id == company.id, CommandRecord.command == "RestartWorkflow"
        )
    )
    assert record.actor == {"kind": "human", "id": "operator"}
    assert record.outcome == "done"

    # and the list now says somebody already did
    again = await api.get(URL.format(company.id) + "/failed")
    assert [row["restarted"] for row in again.json()] == [True]


async def test_a_run_that_is_still_going_is_not_restarted(api, db_session, setup):
    """Restarting a live run would leave two of the same work going at once."""
    company, project, agents = setup
    started = await api.post(URL.format(company.id), json=_body(project))
    running = started.json()["id"]

    response = await api.post(URL.format(company.id) + f"/{running}/restart")

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "refused" and "still RUNNING" in body["reason"]
    assert body["workflow_run_id"] is None


async def test_restarting_something_that_is_not_this_company_s_is_refused(api, db_session, setup):
    company, project, agents = setup
    response = await api.post(URL.format(company.id) + f"/{uuid.uuid4()}/restart")
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "refused" and "no workflow run" in body["reason"]


async def test_the_same_work_is_not_restarted_twice(api, db_session, setup):
    """D-044: a published story was restarted three times from this list. Once one restart is
    going (or the work has since succeeded), the list says so and a second restart is refused."""
    company, project, agents = setup
    failed = await _failed_run(db_session, company, project)
    await db_session.commit()

    first = await api.post(URL.format(company.id) + f"/{failed.id}/restart")
    assert first.json()["outcome"] == "done"
    fresh = first.json()["workflow_run_id"]

    [row] = (await api.get(URL.format(company.id) + "/failed")).json()
    assert row["superseded_by"] == fresh
    again = await api.post(URL.format(company.id) + f"/{failed.id}/restart")
    assert again.json()["outcome"] == "refused"
    assert "would do it twice" in again.json()["reason"]


async def test_other_work_does_not_supersede_a_failed_run(api, db_session, setup):
    company, project, agents = setup
    failed = await _failed_run(db_session, company, project, topic="EU AI Act")
    await _failed_run(db_session, company, project, topic="something else")
    await db_session.commit()
    rows = (await api.get(URL.format(company.id) + "/failed")).json()
    assert {r["id"]: r["superseded_by"] for r in rows}[str(failed.id)] is None
