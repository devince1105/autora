"""T-106: event log / outbox."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.db.models import Company, EventRecord
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events import new_event
from autora.runtime.events.outbox import EVENTS_CHANNEL, EventEmitError, emit, load_events

SYSTEM = Actor.system("test")


def _envelope(company_id, payload=None, **kw):
    return new_event(
        payload or ev.ScheduleFired(schedule_name="daily", scheduled_for="2026-09-17T06:00:00Z"),
        company_id=company_id,
        actor=SYSTEM,
        aggregate_type="schedule",
        aggregate_id=uuid.uuid4(),
        **kw,
    )


async def _company(session: AsyncSession) -> Company:
    company = Company(slug=f"evt-{uuid.uuid4().hex[:12]}", name="Events Co", type="newsroom")
    session.add(company)
    await session.flush()
    return company


# --- within one transaction (rolled back by the fixture) ---------------------------------


async def test_emit_assigns_increasing_seq_and_roundtrips(db_session):
    company = await _company(db_session)
    agent_id = uuid.uuid4()
    first = await emit(db_session, _envelope(company.id, agent_id=agent_id))
    second = await emit(
        db_session,
        _envelope(company.id, ev.AgentThinking(phase="plan", step_seq=0), agent_id=agent_id),
    )
    assert first.seq is not None and second.seq > first.seq

    loaded = await load_events(db_session, company.id)
    assert loaded == [first, second]
    assert isinstance(loaded[1].payload, ev.AgentThinking)

    assert await load_events(db_session, company.id, after_seq=first.seq) == [second]
    assert await load_events(db_session, company.id, until_seq=first.seq) == [first]
    assert await load_events(db_session, company.id, event_types=["AGENT_THINKING"]) == [second]


async def test_load_events_is_company_scoped(db_session):
    a = await _company(db_session)
    b = await _company(db_session)
    await emit(db_session, _envelope(a.id))
    await emit(db_session, _envelope(b.id))
    assert [e.company_id for e in await load_events(db_session, a.id)] == [a.id]


async def test_rolled_back_transaction_leaves_no_event(db_session):
    company = await _company(db_session)
    async with db_session.begin_nested() as nested:
        await emit(db_session, _envelope(company.id))
        await nested.rollback()
    count = await db_session.scalar(
        select(func.count()).select_from(EventRecord).where(EventRecord.company_id == company.id)
    )
    assert count == 0


async def test_ephemeral_and_already_persisted_events_rejected(db_session):
    company = await _company(db_session)
    with pytest.raises(EventEmitError, match="ephemeral"):
        await emit(
            db_session, _envelope(company.id, ev.AgentStepProgress(step_seq=0, tokens_so_far=1))
        )
    persisted = await emit(db_session, _envelope(company.id))
    with pytest.raises(EventEmitError, match="already persisted"):
        await emit(db_session, persisted)


async def test_only_processed_at_may_be_set_once(db_session):
    company = await _company(db_session)
    event = await emit(db_session, _envelope(company.id))
    params = {"id": event.event_id}

    await db_session.execute(text("UPDATE events SET processed_at = now() WHERE id = :id"), params)
    for statement in (
        "UPDATE events SET processed_at = now() + interval '1 second' WHERE id = :id",
        "UPDATE events SET payload = '{}'::jsonb WHERE id = :id",
        "DELETE FROM events WHERE id = :id",
    ):
        async with db_session.begin_nested() as nested:
            with pytest.raises(DBAPIError, match="append-only"):
                await db_session.execute(text(statement), params)
            await nested.rollback()


# --- across real commits -----------------------------------------------------------------


@pytest.fixture
async def committed_company(db_engine):
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with maker() as session:
        company = await _company(session)
        await session.commit()
    return maker, company


async def test_notify_is_delivered_only_after_commit(db_engine, committed_company):
    maker, company = committed_company
    received: asyncio.Queue[str] = asyncio.Queue()

    async with db_engine.connect() as listen_conn:
        raw = (await listen_conn.get_raw_connection()).driver_connection

        def on_notify(_conn, _pid, _channel, payload):
            received.put_nowait(payload)

        await raw.add_listener(EVENTS_CHANNEL, on_notify)

        async with maker() as session:
            event = await emit(session, _envelope(company.id))
            await asyncio.sleep(0.3)
            assert received.empty(), "NOTIFY must not be delivered before commit"
            await session.commit()

        payload = await asyncio.wait_for(received.get(), timeout=3)
        assert payload == f"{company.id}:{event.seq}"
        await raw.remove_listener(EVENTS_CHANNEL, on_notify)


async def test_commit_order_matches_seq_order_within_company(committed_company):
    maker, company = committed_company

    async with maker() as first_session, maker() as second_session:
        first = await emit(first_session, _envelope(company.id))  # holds the company lock

        second_task = asyncio.create_task(emit(second_session, _envelope(company.id)))
        await asyncio.sleep(0.3)
        assert not second_task.done(), "second emitter must wait for the first to commit"

        await first_session.commit()
        second = await asyncio.wait_for(second_task, timeout=3)
        await second_session.commit()

    assert second.seq > first.seq
