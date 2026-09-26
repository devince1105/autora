"""D-054: the operator's budget and capital controls on the admin dashboard."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from autora.db.models import Budget, Project, ProjectState, Task, TaskState, Transaction
from tests.conftest import unique_company

URL = "/api/companies/{}/finance"


@pytest.fixture
async def company(db_session):
    company = await unique_company(db_session, "fin")
    project = Project(
        company_id=company.id,
        name="持股動態",
        state=ProjectState.ACTIVE.value,
        kill_criteria={"max_cost_usd": 10},
    )
    db_session.add(project)
    await db_session.flush()
    return company, project


async def test_an_empty_company_has_nothing_and_its_projects_to_fund(api, company):
    company, project = company
    response = await api.get(URL.format(company.id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["balance"]) == 0 and body["budgets"] == []
    assert [p["name"] for p in body["projects"]] == ["持股動態"]


async def test_capital_is_recorded_once_per_submission(api, db_session, company):
    company, _ = company
    body = {"amount": "3000", "memo": "第一筆注資", "request_id": str(uuid.uuid4())}
    first = await api.post(URL.format(company.id) + "/capital", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["recorded"] is True and Decimal(first.json()["balance"]) == 3000
    again = await api.post(URL.format(company.id) + "/capital", json=body)
    assert again.json() == {"recorded": False, "balance": first.json()["balance"]}
    [row] = (
        await db_session.scalars(select(Transaction).where(Transaction.company_id == company.id))
    ).all()
    assert row.kind == "capital_in" and row.source == "human" and row.memo == "第一筆注資"
    assert (await api.get(URL.format(company.id))).json()["balance"] == first.json()["balance"]


async def test_capital_must_be_positive(api, company):
    company, _ = company
    body = {"amount": "0", "request_id": str(uuid.uuid4())}
    assert (await api.post(URL.format(company.id) + "/capital", json=body)).status_code == 422


async def test_a_budget_replaces_the_envelope_and_frees_the_work_it_blocked(
    api, db_session, company
):
    """Cycle 5: the CEO put NT$0 behind the project and the newsroom's work stopped."""
    company, project = company
    db_session.add(
        Budget(company_id=company.id, project_id=project.id, period="cycle",
               amount=Decimal("0"), currency="TWD", hard_cap=True)
    )  # fmt: skip
    blocked = Task(
        company_id=company.id, project_id=project.id, name="distribute",
        display_name="Distribute", required_role="marketing",
        state=TaskState.BLOCKED_BUDGET.value, input={},
    )  # fmt: skip
    db_session.add(blocked)
    await db_session.flush()

    response = await api.post(
        URL.format(company.id) + "/budgets",
        json={"amount": "100", "period": "cycle", "project_id": str(project.id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "allow" and response.json()["released_tasks"] == 1
    [line] = (await api.get(URL.format(company.id))).json()["budgets"]
    assert line["scope"] == "project" and line["name"] == "持股動態"
    assert Decimal(line["amount"]) == 100
    task = await db_session.get(Task, blocked.id, populate_existing=True)
    assert task.state == TaskState.READY.value


async def test_the_controls_are_an_operator_s(api, company):
    company, _ = company
    anonymous = {"Authorization": ""}
    assert (await api.get(URL.format(company.id), headers=anonymous)).status_code == 401
    body = {"amount": "1", "request_id": str(uuid.uuid4())}
    response = await api.post(URL.format(company.id) + "/capital", json=body, headers=anonymous)
    assert response.status_code == 401
