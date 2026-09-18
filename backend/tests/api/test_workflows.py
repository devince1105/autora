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
