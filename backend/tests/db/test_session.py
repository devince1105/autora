from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from tests.conftest import PACKAGE_ROOT


async def test_migrations_applied_and_pgvector_available(db_session):
    head = ScriptDirectory.from_config(Config(str(PACKAGE_ROOT / "alembic.ini"))).get_current_head()
    version = (await db_session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    assert version == head
    ext = (
        await db_session.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'"))
    ).scalar()
    assert ext == "vector"


async def test_session_rollback_isolation(db_session):
    await db_session.execute(text("CREATE TEMP TABLE t102 (x int)"))
    await db_session.execute(text("INSERT INTO t102 VALUES (1)"))
    count = (await db_session.execute(text("SELECT count(*) FROM t102"))).scalar()
    assert count == 1
    # the fixture rolls back; nothing to assert across tests, but the round-trip must work
