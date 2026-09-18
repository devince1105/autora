"""T-206: approval inbox API."""

import uuid

import pytest

from autora.db.models import Agent, ApprovalKind, Project, Task
from autora.runtime.activity import initialize_activity
from autora.runtime.actor import Actor
from tests.conftest import unique_company


@pytest.fixture
async def waiting(db_session, runtime):
    """A writer run suspended for approval."""
    company = await unique_company(db_session, "inbox")
    project = Project(company_id=company.id, name="p")
    agent = Agent(company_id=company.id, role="writer", display_name="Writer")
    db_session.add_all([project, agent])
    await db_session.flush()
    await initialize_activity(db_session, agent, actor=Actor.system("setup"))
    task = await runtime.task_manager.add_task(
        db_session,
        company_id=company.id,
        project_id=project.id,
        name="draft",
        display_name="Draft",
        required_role="writer",
    )
    claim = await runtime.task_manager.claim_next(db_session, agent, "w1")
    approval = await runtime.approvals.request_for_run(
        db_session,
        claim,
        kind=ApprovalKind.TOOL_CALL,
        action="publish_article",
        payload={"args": {"article_id": "a1"}},
        summary="Publish a1?",
    )
    return {"company": company, "task": task, "approval": approval}


async def test_inbox_lists_pending_approvals(api, waiting):
    response = await api.get("/api/approvals", params={"company_id": str(waiting["company"].id)})
    assert response.status_code == 200
    [item] = response.json()
    assert item["id"] == str(waiting["approval"].id)
    assert (item["state"], item["kind"], item["action"]) == (
        "PENDING",
        "tool_call",
        "publish_article",
    )
    assert item["payload"] == {"args": {"article_id": "a1"}}

    decided = await api.get(
        "/api/approvals", params={"company_id": str(waiting["company"].id), "state": "APPROVED"}
    )
    assert decided.json() == []


async def test_approve_releases_the_task(api, db_session, waiting):
    response = await api.post(
        f"/api/approvals/{waiting['approval'].id}/decide",
        json={"decision": "approve", "reason": "looks good"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "APPROVED" and body["decided_by"] == {"kind": "human", "id": "operator"}
    task = await db_session.get(Task, waiting["task"].id, populate_existing=True)
    assert task.state == "READY"

    again = await api.post(
        f"/api/approvals/{waiting['approval'].id}/decide", json={"decision": "reject"}
    )
    assert again.status_code == 409


async def test_reject_cancels_the_task(api, db_session, waiting):
    response = await api.post(
        f"/api/approvals/{waiting['approval'].id}/decide", json={"decision": "reject"}
    )
    assert response.json()["state"] == "REJECTED"
    task = await db_session.get(Task, waiting["task"].id, populate_existing=True)
    assert task.state == "CANCELLED"


async def test_errors(api, waiting):
    missing = await api.post(f"/api/approvals/{uuid.uuid4()}/decide", json={"decision": "approve"})
    assert missing.status_code == 404
    invalid = await api.post(
        f"/api/approvals/{waiting['approval'].id}/decide", json={"decision": "maybe"}
    )
    assert invalid.status_code == 422
    unauthorised = await api.post(
        f"/api/approvals/{waiting['approval'].id}/decide",
        json={"decision": "approve"},
        headers={"Authorization": ""},
    )
    assert unauthorised.status_code == 401
