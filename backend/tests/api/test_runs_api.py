"""T-210: run trace and step payload endpoints."""

import json
import uuid
from decimal import Decimal

import pytest

from autora.db.models import StepKind
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events import new_event
from autora.runtime.events.outbox import emit
from autora.runtime.trace import record_step
from tests.conftest import running_agent_run


@pytest.fixture
async def run(db_session):
    return await running_agent_run(db_session, "runs-api")


async def test_trace_endpoint_returns_events_with_steps_but_not_payloads(
    api, db_session, blobs, run
):
    await record_step(
        db_session, blobs, run.id, kind=StepKind.THINK, summary="plan",
        payload={"prompt": "secret prompt text"},
    )  # fmt: skip
    await emit(db_session, new_event(
        ev.AgentThinking(phase="plan", step_seq=0), company_id=run.company_id,
        actor=Actor.agent(run.agent_id), aggregate_type="agent_run", aggregate_id=run.id,
        run_id=run.id, agent_id=run.agent_id,
    ))  # fmt: skip

    body = (await api.get(f"/api/runs/{run.id}/trace")).json()
    assert body["run_id"] == str(run.id)
    assert [e["event_type"] for e in body["entries"]] == ["AGENT_THINKING"]
    assert body["entries"][0]["step"]["has_blob"] is True
    assert "secret prompt text" not in json.dumps(body), "payload stays behind the blob endpoint"

    blob = await api.get(f"/api/runs/{run.id}/steps/0/blob")
    assert blob.status_code == 200
    assert blob.headers["content-type"] == "application/json"
    assert blob.json() == {"prompt": "secret prompt text"}


async def test_not_found_cases(api, db_session, blobs, run):
    await record_step(db_session, blobs, run.id, kind=StepKind.ACT)  # step without payload
    assert (await api.get(f"/api/runs/{uuid.uuid4()}/trace")).status_code == 404
    assert (await api.get(f"/api/runs/{run.id}/steps/0/blob")).status_code == 404
    assert (await api.get(f"/api/runs/{run.id}/steps/7/blob")).status_code == 404


async def test_endpoints_require_auth(api, run):
    no_auth = {"Authorization": ""}
    assert (await api.get(f"/api/runs/{run.id}/trace", headers=no_auth)).status_code == 401
    assert (await api.get(f"/api/runs/{run.id}/steps/0/blob", headers=no_auth)).status_code == 401


async def test_run_and_task_detail(api, db_session, run):
    run.cost_usd = 0.0035
    run.tokens_in, run.tokens_out, run.steps_count = 200, 100, 4
    await db_session.flush()

    body = (await api.get(f"/api/runs/{run.id}")).json()
    assert body["id"] == str(run.id) and body["state"] == "RUNNING" and body["attempt"] == 1
    assert (body["tokens_in"], body["tokens_out"], body["steps_count"]) == (200, 100, 4)
    assert isinstance(body["cost_usd"], str), "money stays a decimal string"
    assert Decimal(body["cost_usd"]) == Decimal("0.0035")
    assert body["task_id"] == str(run.task_id) and body["finished_at"] is None

    task = (await api.get(f"/api/tasks/{run.task_id}")).json()
    assert task["name"] == "research" and task["required_role"] == "researcher"
    assert [(r["id"], r["attempt"], r["state"]) for r in task["runs"]] == [
        (str(run.id), 1, "RUNNING")
    ]

    for path in (f"/api/runs/{uuid.uuid4()}", f"/api/tasks/{uuid.uuid4()}"):
        assert (await api.get(path)).status_code == 404
    no_auth = {"Authorization": "Bearer nope"}
    assert (await api.get(f"/api/tasks/{run.task_id}", headers=no_auth)).status_code == 401
