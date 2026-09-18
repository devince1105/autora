"""T-105: event envelope, payload registry and catalogs."""

import json
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

import autora.company.events as company_events
from autora.runtime.actor import Actor
from autora.runtime.events import (
    DuplicateEventType,
    EventEnvelope,
    EventPayload,
    Persistence,
    UnknownEventType,
    event,
    new_event,
    parse_event,
    payload_class,
    registered_event_types,
)
from autora.runtime.events import catalog as core
from autora.runtime.events.schema import EventRegistryError

COMPANY_ID = uuid.UUID("01900000-0000-7000-8000-000000000001")
AGENT_ID = uuid.UUID("01900000-0000-7000-8000-000000000002")
RUN_ID = uuid.UUID("01900000-0000-7000-8000-000000000003")
TASK_ID = uuid.UUID("01900000-0000-7000-8000-000000000004")

# logs/3d-office/03_EVENT_MODEL.md §2.1–2.3 + governance/scheduler (runtime-owned).
EXPECTED_RUNTIME = {
    "AGENT_CREATED", "AGENT_PAUSED", "AGENT_RESUMED", "AGENT_IDLE", "AGENT_RUN_STARTED",
    "AGENT_THINKING", "AGENT_WORKING", "AGENT_WAITING", "AGENT_REVIEWING",
    "AGENT_RUN_COMPLETED", "AGENT_RUN_FAILED", "AGENT_RUN_ABORTED",
    "AGENT_STEP_PROGRESS", "AGENT_HEARTBEAT",
    "TOOL_CALLED", "TOOL_COMPLETED", "TOOL_FAILED", "TOOL_DENIED",
    "WORKFLOW_RUN_CREATED", "WORKFLOW_RUN_COMPLETED", "WORKFLOW_RUN_FAILED",
    "WORKFLOW_RUN_CANCELLED",
    "TASK_CREATED", "TASK_READY", "TASK_STARTED", "TASK_WAITING", "TASK_SUCCEEDED",
    "TASK_FAILED", "TASK_CANCELLED", "TASK_BLOCKED",
    "APPROVAL_REQUESTED", "APPROVAL_APPROVED", "APPROVAL_REJECTED", "APPROVAL_EXPIRED",
    "POLICY_DENIED", "SCHEDULE_FIRED", "BUDGET_EXHAUSTED",
}  # fmt: skip

# logs/platform/11_EVENT_CATALOG.md company groups (PAYMENT_RECEIVED belongs to business, P7).
EXPECTED_COMPANY = {
    "COMPANY_CREATED", "GOAL_CREATED", "GOAL_UPDATED", "POLICY_UPDATED", "STRATEGY_UPDATED",
    "PROJECT_PROPOSED", "PROJECT_APPROVED", "PROJECT_REJECTED", "PROJECT_PAUSED",
    "PROJECT_RESUMED", "PROJECT_KILL_PROPOSED", "PROJECT_KILLED", "PROJECT_COMPLETED",
    "CYCLE_STARTED", "CYCLE_STAGE_CHANGED", "CYCLE_STAGE_TIMEOUT", "CYCLE_PLAN_FALLBACK",
    "CYCLE_REVIEWED", "CYCLE_COMPLETED",
    "BUDGET_ALLOCATED", "EXPENSE_RECORDED", "REVENUE_RECORDED",
    "KPI_SNAPSHOT_CREATED",
}  # fmt: skip


def _types_in(module) -> set[str]:
    return {
        cls.event_type
        for cls in registered_event_types().values()
        if cls.__module__ == module.__name__
    }


def test_runtime_catalog_complete():
    assert _types_in(core) == EXPECTED_RUNTIME


def test_company_catalog_complete():
    assert _types_in(company_events) == EXPECTED_COMPANY


def test_runtime_catalog_has_no_domain_events():
    assert not any(
        t.startswith(("ARTICLE_", "STORY_", "CLAIM_", "SOURCE_")) for t in _types_in(core)
    )


def test_only_progress_and_heartbeat_are_ephemeral():
    ephemeral = {
        cls.event_type
        for cls in registered_event_types().values()
        if cls.persistence is Persistence.EPHEMERAL
    }
    assert ephemeral == {"AGENT_STEP_PROGRESS", "AGENT_HEARTBEAT"}


# --- envelope ----------------------------------------------------------------------------


def _working_event(**kw):
    return new_event(
        core.AgentWorking(
            tool="web_search",
            tool_call_id="call_1",
            step_seq=3,
            progress=core.Progress(label="sources", current=12, target=40),
        ),
        company_id=COMPANY_ID,
        actor=Actor.agent(AGENT_ID),
        aggregate_type="agent_run",
        aggregate_id=RUN_ID,
        agent_id=AGENT_ID,
        run_id=RUN_ID,
        task_id=TASK_ID,
        **kw,
    )


def test_new_event_fills_type_version_id_and_time():
    env = _working_event()
    assert env.event_type == "AGENT_WORKING"
    assert env.schema_version == 1
    assert env.event_id.version == 7
    assert env.seq is None
    assert env.occurred_at.tzinfo is not None
    assert env.persistence is Persistence.PERSISTED


def test_json_roundtrip_restores_typed_payload():
    env = _working_event()
    raw = env.model_dump_json()
    data = json.loads(raw)
    assert data["payload"]["progress"] == {"label": "sources", "current": 12, "target": 40}

    parsed = parse_event(raw)
    assert parsed == env
    assert isinstance(parsed.payload, core.AgentWorking)
    assert parsed.payload.progress.current == 12


def test_decimal_money_serializes_as_string():
    env = new_event(
        core.AgentRunCompleted(cost_usd=Decimal("0.184"), steps=19, duration_ms=217_000),
        company_id=COMPANY_ID,
        actor=Actor.agent(AGENT_ID),
        aggregate_type="agent_run",
        aggregate_id=RUN_ID,
    )
    assert json.loads(env.model_dump_json())["payload"]["cost_usd"] == "0.184"


def test_unknown_event_type_rejected():
    data = json.loads(_working_event().model_dump_json())
    data["event_type"] = "AGENT_DANCING"
    with pytest.raises(ValidationError, match="unknown event type"):
        parse_event(data)


def test_unknown_schema_version_rejected():
    data = json.loads(_working_event().model_dump_json())
    data["schema_version"] = 9
    with pytest.raises(ValidationError, match="no schema version 9"):
        parse_event(data)


def test_payload_dict_must_match_event_type():
    data = json.loads(_working_event().model_dump_json())
    data["event_type"] = "AGENT_THINKING"  # payload still has AGENT_WORKING fields
    with pytest.raises(ValidationError):
        parse_event(data)


def test_payload_instance_must_match_event_type():
    fields = {**_working_event().__dict__}
    fields["payload"] = core.AgentThinking(phase="plan", step_seq=0)  # event_type AGENT_WORKING
    with pytest.raises(ValidationError, match="does not match"):
        EventEnvelope(**fields)


def test_extra_payload_fields_forbidden():
    data = json.loads(_working_event().model_dump_json())
    data["payload"]["secret_prompt"] = "..."
    with pytest.raises(ValidationError, match="secret_prompt"):
        parse_event(data)


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError, match="timezone-aware"):
        _working_event(occurred_at=datetime(2026, 9, 16, 12, 0))


def test_ephemeral_event_cannot_have_seq():
    env = new_event(
        core.AgentStepProgress(step_seq=1, tokens_so_far=200),
        company_id=COMPANY_ID,
        actor=Actor.agent(AGENT_ID),
        aggregate_type="agent_run",
        aggregate_id=RUN_ID,
    )
    assert env.persistence is Persistence.EPHEMERAL
    data = json.loads(env.model_dump_json())
    data["seq"] = 10
    with pytest.raises(ValidationError, match="ephemeral"):
        parse_event(data)


def test_task_succeeded_carries_unlocked_roles():
    payload = core.TaskSucceeded(
        run_id=RUN_ID, unlocks=[core.UnlockedTask(task_id=TASK_ID, required_role="analyst")]
    )
    assert payload.unlocks[0].required_role == "analyst"


# --- registry ----------------------------------------------------------------------------


def test_duplicate_registration_rejected():
    with pytest.raises(DuplicateEventType, match="AGENT_WORKING v1"):

        @event("AGENT_WORKING")
        class Impostor(EventPayload):
            pass


def test_new_version_can_coexist():
    @event("TEST_ONLY_VERSIONED", version=1)
    class V1(EventPayload):
        a: int

    @event("TEST_ONLY_VERSIONED", version=2)
    class V2(EventPayload):
        b: int

    assert payload_class("TEST_ONLY_VERSIONED", 1) is V1
    assert payload_class("TEST_ONLY_VERSIONED", 2) is V2


@pytest.mark.parametrize("bad", ["agent_working", "Agent-Working", "_X", "X__Y", ""])
def test_event_type_must_be_upper_snake(bad):
    with pytest.raises(EventRegistryError):
        event(bad)


def test_unregistered_payload_cannot_be_emitted():
    class Loose(EventPayload):
        x: int = 1

    with pytest.raises(UnknownEventType):
        new_event(
            Loose(),
            company_id=COMPANY_ID,
            actor=Actor.system("test"),
            aggregate_type="x",
            aggregate_id=RUN_ID,
        )


def test_payload_class_lookup_errors():
    with pytest.raises(UnknownEventType, match="unknown event type"):
        payload_class("NOPE")
