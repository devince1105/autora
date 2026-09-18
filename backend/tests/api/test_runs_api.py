"""T-210: run trace and step payload endpoints."""

import json
import uuid

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
