"""T-110: minimal REST API (companies, agents, events)."""

import uuid

import pytest

from autora.db.models import Agent
from autora.runtime.activity import initialize_activity, set_activity
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev

NEWSROOM = {"slug": "ai-bilingual-newsroom", "name": "AI Bilingual Newsroom"}


async def _create(api, **overrides):
    return await api.post("/api/companies", json={**NEWSROOM, **overrides})


# --- auth & errors -----------------------------------------------------------------------


async def test_health_is_public(api):
    response = await api.get("/health", headers={"Authorization": ""})
    assert response.status_code == 200 and response.json()["status"] == "ok"


@pytest.mark.parametrize("header", [None, "Bearer wrong-token", "Basic abc"])
async def test_admin_endpoints_require_bearer_token(api, header):
    headers = {"Authorization": header} if header else {"Authorization": ""}
    response = await api.get("/api/companies", headers=headers)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["www-authenticate"] == "Bearer"


async def test_validation_errors_are_problem_json(api):
    response = await _create(api, slug="Bad Slug", name="")
    assert response.status_code == 422
    body = response.json()
    assert body["title"] == "Unprocessable Entity"
    assert {tuple(e["loc"]) for e in body["errors"]} >= {("body", "name"), ("body", "slug")}


# --- companies ---------------------------------------------------------------------------


async def test_create_get_and_list_company(api):
    created = await _create(api, mission="Explain AI news in zh-TW and English")
    assert created.status_code == 201
    company = created.json()
    assert company["slug"] == "ai-bilingual-newsroom" and company["status"] == "active"
    assert uuid.UUID(company["id"]).version == 7

    fetched = await api.get(f"/api/companies/{company['id']}")
    assert fetched.json() == company
    listed = await api.get("/api/companies")
    assert company in listed.json()


async def test_create_company_emits_company_created(api):
    company = (await _create(api)).json()
    page = (await api.get("/api/events", params={"company_id": company["id"]})).json()
    assert [e["event_type"] for e in page["items"]] == ["COMPANY_CREATED"]
    event = page["items"][0]
    assert event["payload"] == {
        "slug": NEWSROOM["slug"],
        "name": NEWSROOM["name"],
        # a company is a portfolio; which industry it is in belongs to its businesses (D-019)
        "type": None,
    }
    assert event["actor"] == {"kind": "human", "id": "operator"}
    assert event["seq"] >= 1


async def test_duplicate_slug_is_conflict(api):
    assert (await _create(api)).status_code == 201
    response = await _create(api, name="Another")
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


async def test_unknown_company_is_404(api):
    missing = uuid.uuid4()
    assert (await api.get(f"/api/companies/{missing}")).status_code == 404
    assert (await api.get(f"/api/companies/{missing}/agents")).status_code == 404
    assert (await api.get("/api/events", params={"company_id": str(missing)})).status_code == 404


# --- agents ------------------------------------------------------------------------------


async def test_agents_include_current_activity(api, db_session):
    company_id = uuid.UUID((await _create(api)).json()["id"])
    researcher = Agent(company_id=company_id, role="researcher", display_name="Researcher")
    writer = Agent(company_id=company_id, role="writer", display_name="Writer")
    db_session.add_all([researcher, writer])
    await db_session.flush()
    await initialize_activity(db_session, researcher, actor=Actor.system("setup"))
    await set_activity(
        db_session,
        researcher,
        ev.AgentWorking(tool="web_search", tool_call_id="c1", step_seq=1),
        actor=Actor.agent(researcher.id),
        run_id=uuid.uuid4(),
        task_name="Find today's AI stories",
    )

    agents = {a["role"]: a for a in (await api.get(f"/api/companies/{company_id}/agents")).json()}
    assert agents["researcher"]["activity"]["state"] == "WORKING"
    assert agents["researcher"]["activity"]["detail"]["tool"] == "web_search"
    assert agents["researcher"]["activity"]["detail"]["task_name"] == "Find today's AI stories"
    assert agents["writer"]["activity"] is None


# --- events ------------------------------------------------------------------------------


async def test_events_pagination_filters_and_scope(api, db_session):
    company_id = uuid.UUID((await _create(api)).json()["id"])
    other_id = uuid.UUID((await _create(api, slug="other-company")).json()["id"])
    agent = Agent(company_id=company_id, role="writer", display_name="Writer")
    db_session.add(agent)
    await db_session.flush()
    await initialize_activity(db_session, agent, actor=Actor.system("setup"))
    for step in range(3):
        await set_activity(
            db_session,
            agent,
            ev.AgentThinking(phase="reason", step_seq=step),
            actor=Actor.agent(agent.id),
            run_id=uuid.uuid4(),
        )

    params = {"company_id": str(company_id), "limit": 2}
    first = (await api.get("/api/events", params=params)).json()
    assert [e["event_type"] for e in first["items"]] == ["COMPANY_CREATED", "AGENT_IDLE"]
    assert first["has_more"] is True

    second = (await api.get("/api/events", params={**params, "after": first["next_after"]})).json()
    assert [e["payload"].get("step_seq") for e in second["items"]] == [0, 1]
    seqs = [e["seq"] for e in first["items"] + second["items"]]
    assert seqs == sorted(seqs)

    thinking = (
        await api.get(
            "/api/events",
            params={"company_id": str(company_id), "type": ["AGENT_THINKING", "AGENT_IDLE"]},
        )
    ).json()
    assert [e["event_type"] for e in thinking["items"]] == ["AGENT_IDLE"] + ["AGENT_THINKING"] * 3

    until = (
        await api.get(
            "/api/events", params={"company_id": str(company_id), "until": first["next_after"]}
        )
    ).json()
    assert len(until["items"]) == 2 and until["has_more"] is False

    other = (await api.get("/api/events", params={"company_id": str(other_id)})).json()
    assert [e["company_id"] for e in other["items"]] == [str(other_id)]

    empty = (
        await api.get("/api/events", params={"company_id": str(company_id), "after": 10**12})
    ).json()
    assert empty == {"items": [], "next_after": 10**12, "has_more": False}


async def test_events_limit_is_bounded(api):
    company_id = (await _create(api)).json()["id"]
    response = await api.get("/api/events", params={"company_id": company_id, "limit": 501})
    assert response.status_code == 422


async def test_the_company_list_counts_agents_and_hides_archived(api, db_session):
    """A page with no company asked for picks one that has agents; archived ones are out of the
    way but keep their events (T-517 follow-up)."""
    from autora.company.agents import hire_agent
    from autora.db.models import Company
    from autora.runtime.actor import Actor

    staffed = (await _create(api, slug="staffed", name="有人的公司")).json()
    empty = (await _create(api, slug="empty-one", name="空的公司")).json()
    await hire_agent(
        db_session,
        company_id=uuid.UUID(staffed["id"]),
        role="researcher",
        display_name="Rae",
        actor=Actor.human("operator"),
    )
    await db_session.flush()

    listed = {c["slug"]: c for c in (await api.get("/api/companies")).json()}
    assert listed["staffed"]["agents"] == 1 and listed["empty-one"]["agents"] == 0

    (await db_session.get(Company, uuid.UUID(empty["id"]))).status = "archived"
    await db_session.flush()
    assert "empty-one" not in {c["slug"] for c in (await api.get("/api/companies")).json()}
    with_archived = await api.get("/api/companies", params={"include_archived": True})
    assert "empty-one" in {c["slug"] for c in with_archived.json()}


async def test_hiring_an_agent(api):
    """The office's own way in: hire an agent for a role the runtime can run (T-517 follow-up)."""
    company = (await _create(api, slug="hiring-co", name="要雇人的公司")).json()
    roles = (await api.get("/api/roles")).json()["roles"]
    assert {"researcher", "analyst", "writer", "editor", "marketing"} <= set(roles)

    hired = await api.post(
        f"/api/companies/{company['id']}/agents",
        json={
            "role": "editor",
            "display_name": "Eli",
            "description": "審稿",
            "per_run_usd": "0.5",
            "tools": ["read_draft", "run_fact_check"],
        },
    )
    assert hired.status_code == 201, hired.text
    body = hired.json()
    assert body["role"] == "editor" and body["display_name"] == "Eli"
    assert body["activity"]["state"] == "IDLE"  # ready for work, shown in the office

    listed = (await api.get(f"/api/companies/{company['id']}/agents")).json()
    assert [a["display_name"] for a in listed] == ["Eli"]

    unknown = await api.post(
        f"/api/companies/{company['id']}/agents",
        json={"role": "chef", "display_name": "Nobody"},
    )
    assert unknown.status_code == 422 and "no behavior for role 'chef'" in unknown.text
    bad = await api.post(
        f"/api/companies/{company['id']}/agents", json={"role": "Editor!", "display_name": ""}
    )
    assert bad.status_code == 422
    missing = await api.post(
        f"/api/companies/{uuid.uuid4()}/agents", json={"role": "editor", "display_name": "E"}
    )
    assert missing.status_code == 404


async def test_pausing_resuming_and_retiring_an_agent(api, db_session):
    """An operator can stop an agent taking work, put it back, or let it go (T-517 follow-up)."""
    from datetime import UTC, datetime

    from autora.db.models import AgentRun, Project, Task

    company = (await _create(api, slug="roster-co", name="有人事的公司")).json()
    hired = (
        await api.post(
            f"/api/companies/{company['id']}/agents",
            json={"role": "researcher", "display_name": "Rae"},
        )
    ).json()
    path = f"/api/companies/{company['id']}/agents/{hired['id']}"

    paused = await api.post(f"{path}/pause", json={"reason": "太吵"})
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    assert paused.json()["activity"]["state"] == "PAUSED"
    assert (await api.post(f"{path}/pause", json={})).json()["status"] == "paused"  # again: same

    resumed = await api.post(f"{path}/resume", json={})
    assert resumed.json()["status"] == "active" and resumed.json()["activity"]["state"] == "IDLE"

    # while it is working, it cannot be let go
    project = Project(company_id=uuid.UUID(company["id"]), name="p")
    db_session.add(project)
    await db_session.flush()
    task = Task(
        company_id=uuid.UUID(company["id"]),
        project_id=project.id,
        name="research",
        display_name="Research",
        required_role="researcher",
        state="RUNNING",
        lease_owner="w",
        lease_token=uuid.uuid4(),
        lease_until=datetime.now(UTC),
    )
    db_session.add(task)
    await db_session.flush()
    run = AgentRun(
        company_id=uuid.UUID(company["id"]),
        task_id=task.id,
        agent_id=uuid.UUID(hired["id"]),
        attempt=1,
        state="RUNNING",
    )
    db_session.add(run)
    await db_session.flush()
    busy = await api.post(f"{path}/retire", json={})
    assert busy.status_code == 409 and "is working on run" in busy.text

    run.state = "COMPLETED"
    run.finished_at = datetime.now(UTC)  # the table insists a finished run has a time
    await db_session.flush()
    retired = await api.post(f"{path}/retire", json={"reason": "示範結束"})
    assert retired.status_code == 200 and retired.json()["status"] == "retired"
    assert (await api.get(f"/api/companies/{company['id']}/agents")).json() == []  # off the roster
    assert (await api.post(f"{path}/resume", json={})).status_code == 409  # gone for good
    events = [
        e["event_type"]
        for e in (await api.get("/api/events", params={"company_id": company["id"]})).json()[
            "items"
        ]
    ]
    assert events.count("AGENT_PAUSED") == 1 and events.count("AGENT_RESUMED") == 1
    assert events.count("AGENT_RETIRED") == 1
