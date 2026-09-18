"""T-104: generic FSM."""

from enum import StrEnum

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from autora.db.models import Company, Project, ProjectState, StateTransition
from autora.runtime.actor import Actor
from autora.runtime.fsm import GuardRejected, IllegalTransition, StateMachine, transitions


class Light(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    OFF = "OFF"


class _Entity:
    def __init__(self, state: str, broken: bool = False):
        self.id = None
        self.company_id = None
        self.state = state
        self.broken = broken


LIGHT = StateMachine(
    entity_type="light",
    states=Light,
    initial=Light.GREEN,
    transitions=transitions(
        {
            Light.GREEN: [Light.YELLOW, Light.OFF],
            Light.YELLOW: [Light.RED],
            Light.RED: [Light.GREEN, Light.OFF],
        }
    ),
    guards={(Light.RED, Light.GREEN): lambda e: "lamp is broken" if e.broken else None},
)


# --- declaration validation --------------------------------------------------------------


def test_rejects_unknown_states():
    class Other(StrEnum):
        X = "X"

    with pytest.raises(ValueError, match="unknown states"):
        StateMachine("bad", Light, {Light.GREEN: frozenset({Other.X})}, Light.GREEN)


def test_rejects_self_transition():
    with pytest.raises(ValueError, match="self-transition"):
        StateMachine("bad", Light, transitions({Light.GREEN: [Light.GREEN]}), Light.GREEN)


def test_rejects_guard_on_undeclared_transition():
    with pytest.raises(ValueError, match="undeclared transition"):
        StateMachine(
            "bad",
            Light,
            transitions({Light.GREEN: [Light.YELLOW]}),
            Light.GREEN,
            guards={(Light.GREEN, Light.RED): lambda e: None},
        )


# --- queries -----------------------------------------------------------------------------


def test_queries():
    assert LIGHT.can(Light.GREEN, Light.YELLOW)
    assert LIGHT.can("GREEN", "OFF")
    assert not LIGHT.can(Light.GREEN, Light.RED)
    assert LIGHT.allowed_from(Light.RED) == {Light.GREEN, Light.OFF}
    assert LIGHT.terminal_states() == {Light.OFF}
    assert LIGHT.is_terminal("OFF")


def test_unknown_state_value_raises():
    with pytest.raises(ValueError):
        LIGHT.can("BLUE", Light.RED)


# --- check -------------------------------------------------------------------------------


def test_illegal_transition_lists_allowed_targets():
    with pytest.raises(IllegalTransition) as exc:
        LIGHT.check(_Entity("GREEN"), Light.RED)
    assert exc.value.allowed == ["OFF", "YELLOW"]
    assert "GREEN -> RED" in str(exc.value)


def test_terminal_state_message():
    with pytest.raises(IllegalTransition, match="terminal state"):
        LIGHT.check(_Entity("OFF"), Light.GREEN)


def test_guard_rejects_with_reason():
    with pytest.raises(GuardRejected, match="lamp is broken"):
        LIGHT.check(_Entity("RED", broken=True), Light.GREEN)
    assert LIGHT.check(_Entity("RED"), Light.GREEN) == (Light.RED, Light.GREEN)


# --- transition (database) ---------------------------------------------------------------

PROJECT_TEST_FSM = StateMachine(
    entity_type="project",
    states=ProjectState,
    initial=ProjectState.PROPOSED,
    transitions=transitions(
        {
            ProjectState.PROPOSED: [ProjectState.APPROVED, ProjectState.REJECTED],
            ProjectState.APPROVED: [ProjectState.ACTIVE],
        }
    ),
    guards={
        (ProjectState.PROPOSED, ProjectState.APPROVED): (
            lambda p: None if p.kill_criteria else "kill criteria required"
        )
    },
)


async def _project(session, **kw) -> Project:
    company = Company(slug="fsm-co", name="FSM Co", type="newsroom")
    session.add(company)
    await session.flush()
    project = Project(company_id=company.id, name="p", state="PROPOSED", **kw)
    session.add(project)
    await session.flush()
    return project


async def test_transition_updates_entity_and_writes_audit_row(db_session):
    project = await _project(db_session, kill_criteria={"evaluate_after_cycles": 7})
    ceo = Actor.agent("00000000-0000-7000-8000-000000000001")

    record = await PROJECT_TEST_FSM.transition(
        db_session, project, ProjectState.APPROVED, actor=ceo, reason="looks promising"
    )
    await PROJECT_TEST_FSM.transition(
        db_session, project, ProjectState.ACTIVE, actor=Actor.system("governance")
    )

    assert project.state == "ACTIVE"
    rows = (
        await db_session.scalars(
            select(StateTransition)
            .where(StateTransition.entity_id == project.id)
            .order_by(StateTransition.id)
        )
    ).all()
    assert [(r.from_state, r.to_state) for r in rows] == [
        ("PROPOSED", "APPROVED"),
        ("APPROVED", "ACTIVE"),
    ]
    assert rows[0].id == record.id
    assert rows[0].entity_type == "project"
    assert rows[0].company_id == project.company_id
    assert rows[0].actor == {"kind": "agent", "id": "00000000-0000-7000-8000-000000000001"}
    assert rows[0].reason == "looks promising"


async def test_failed_transition_writes_nothing(db_session):
    project = await _project(db_session)

    with pytest.raises(GuardRejected):
        await PROJECT_TEST_FSM.transition(
            db_session, project, ProjectState.APPROVED, actor=Actor.human("op")
        )
    with pytest.raises(IllegalTransition):
        await PROJECT_TEST_FSM.transition(
            db_session, project, ProjectState.ACTIVE, actor=Actor.human("op")
        )

    assert project.state == "PROPOSED"
    count = await db_session.scalar(
        select(text("count(*)"))
        .select_from(StateTransition)
        .where(StateTransition.entity_id == project.id)
    )
    assert count == 0


async def test_audit_rows_are_append_only(db_session):
    project = await _project(db_session)
    record = await PROJECT_TEST_FSM.transition(
        db_session, project, ProjectState.REJECTED, actor=Actor.human("op")
    )
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            text("UPDATE state_transitions SET reason = 'edited' WHERE id = :id"),
            {"id": record.id},
        )
    await db_session.rollback()


# --- shortest_path / transition_via ------------------------------------------------------


def test_shortest_path():
    assert LIGHT.shortest_path(Light.GREEN, Light.RED) == [Light.YELLOW, Light.RED]
    assert LIGHT.shortest_path(Light.GREEN, Light.GREEN) == []
    assert LIGHT.shortest_path("RED", "OFF") == [Light.OFF]
    with pytest.raises(IllegalTransition):
        LIGHT.shortest_path(Light.OFF, Light.GREEN)


async def test_transition_via_audits_every_hop(db_session):
    project = await _project(db_session, kill_criteria={"x": 1})
    records = await PROJECT_TEST_FSM.transition_via(
        db_session, project, ProjectState.ACTIVE, actor=Actor.human("op"), reason="fast-track"
    )
    assert project.state == "ACTIVE"
    assert [(r.from_state, r.to_state) for r in records] == [
        ("PROPOSED", "APPROVED"),
        ("APPROVED", "ACTIVE"),
    ]
    assert all(r.reason == "fast-track" for r in records)
