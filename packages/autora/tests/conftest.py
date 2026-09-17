"""Shared fixtures.

``db_engine`` / ``db_session`` need a reachable Postgres (``make dev``). Tests that use
them are skipped, not failed, when the database is unavailable, so the unit suite stays
runnable without Docker.

Tests never touch the dev database: the URL's database name gets a ``_test`` suffix, the
test database is created on demand, its schema is reset and migrations are applied once
per session (so tests always run against ``alembic upgrade head``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from autora.db.session import build_engine, dispose_engine
from autora.infra.settings import SettingsError, get_settings, load_settings

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REQUIRE_DB = os.environ.get("AUTORA_REQUIRE_DB") == "1"


def _unavailable(reason: str):
    """Skip locally; fail in CI (AUTORA_REQUIRE_DB=1) so DB tests can't silently vanish."""
    if REQUIRE_DB:
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


async def _ensure_database(admin_url: str, name: str) -> None:
    engine = build_engine(admin_url)
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            exists = (
                await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
                )
            ).scalar()
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def db_settings():
    """Settings pointing at the dedicated test database (<dev db name>_test)."""
    try:
        base = load_settings()
    except SettingsError as exc:
        _unavailable(f"settings unavailable: {exc}")
    url = make_url(base.database_url)
    test_url = url.set(database=f"{url.database}_test")
    return load_settings(database_url=test_url.render_as_string(hide_password=False))


@pytest.fixture(scope="session")
async def db_engine(db_settings) -> AsyncIterator[AsyncEngine]:
    url = make_url(db_settings.database_url)
    admin_url = url.set(database="postgres").render_as_string(hide_password=False)
    try:
        await _ensure_database(admin_url, url.database)
    except Exception as exc:  # noqa: BLE001 - any connection failure means "no db here"
        _unavailable(f"database unavailable at {admin_url}: {exc}")

    engine = build_engine(db_settings)
    # Clean schema + migrations, so tests always run against `alembic upgrade head`.
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PACKAGE_ROOT,
        check=True,
        capture_output=True,
        env={**os.environ, "DATABASE_URL": db_settings.database_url},
    )
    yield engine
    await engine.dispose()
    await dispose_engine()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session inside an outer transaction that is rolled back after each test.

    ``create_savepoint`` makes ``session.commit()`` / ``session.rollback()`` inside a test act
    on a SAVEPOINT, so a test can assert an IntegrityError, roll back, and keep going.
    """
    async with db_engine.connect() as conn:
        trans = await conn.begin()
        session = async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )()
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest.fixture
def committed(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A session factory whose commits are real (for NOTIFY, locks and multi-session races).

    Data persists in the test database for the rest of the session, so tests using this must
    create uniquely named rows (see ``unique_company``).
    """
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def unique_company(session: AsyncSession, prefix: str = "co"):
    import uuid

    from autora.db.models import Company

    company = Company(slug=f"{prefix}-{uuid.uuid4().hex[:12]}", name=prefix, type="newsroom")
    session.add(company)
    await session.flush()
    return company


async def running_agent_run(session: AsyncSession, prefix: str = "run"):
    """A company + project + agent + task + agent run in state RUNNING (flushed, not committed)."""
    from autora.db.models import Agent, AgentRun, Project, Task

    company = await unique_company(session, prefix)
    project = Project(company_id=company.id, name="p")
    agent = Agent(company_id=company.id, role="researcher", display_name="Researcher")
    session.add_all([project, agent])
    await session.flush()
    task = Task(
        company_id=company.id,
        project_id=project.id,
        name="research",
        display_name="Find stories",
        required_role="researcher",
    )
    session.add(task)
    await session.flush()
    run = AgentRun(
        company_id=company.id, task_id=task.id, agent_id=agent.id, attempt=1, state="RUNNING"
    )
    session.add(run)
    await session.flush()
    return run
