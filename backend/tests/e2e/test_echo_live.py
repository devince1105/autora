"""EchoWorkflow with a real model (whichever MODEL_PROVIDER is configured: anthropic or nvidia).

Run with ``pytest backend -m integration``. This is the "real model call passes once" check
before Phase 3 (09_DEVELOPMENT_ROADMAP, adjusted by D-005). The model decides what to write, so
only the structure is asserted: every desk wrote one note and reported it back as valid JSON.
"""

import pytest
from sqlalchemy import select

from autora.app import build_worker
from autora.db.models import ModelCall, Task, WorkflowRun
from autora.domains.echo.models import EchoNote
from autora.infra.settings import SettingsError, load_settings
from tests.echo_fixtures import start_echo


def _real_provider() -> str | None:
    try:
        settings = load_settings()
    except SettingsError:
        return None
    return None if settings.model_provider == "fake" else settings.model_provider


PROVIDER = _real_provider()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        PROVIDER is None, reason="needs MODEL_PROVIDER=anthropic|nvidia and its key"
    ),
]


async def test_echo_workflow_with_a_real_model(committed, db_settings, tmp_path, echo_company):
    real = load_settings().model_copy(
        update={
            "database_url": db_settings.database_url,
            "blob_store_dir": tmp_path / "blobs",
            "worker_id": "live-echo",
        }
    )
    company_id = echo_company.company.id
    worker = build_worker(real, session_factory=committed, company_ids=frozenset({company_id}))
    wf, tasks = await start_echo(committed, echo_company)

    await worker.run_until_idle()

    async with committed() as session:
        run = await session.get(WorkflowRun, wf.id)
        rows = [await session.get(Task, t.id) for t in tasks.values()]
        notes = (
            await session.scalars(select(EchoNote).where(EchoNote.company_id == company_id))
        ).all()
        calls = (
            await session.scalars(select(ModelCall).where(ModelCall.company_id == company_id))
        ).all()

    summary = "\n".join(
        f"  {t.name}: {t.state} attempt={t.attempt} output={t.output}" for t in rows
    )
    print(f"\nprovider={PROVIDER} workflow={run.state}\n{summary}")
    for call in calls:
        print(
            f"  {call.role} {call.model_id} {call.status} in={call.tokens_in} out={call.tokens_out}"
        )
    assert run.state == "SUCCEEDED", summary
    assert len(notes) == 3 and {n.task_id for n in notes} == {t.id for t in rows}
    assert all(c.provider == PROVIDER for c in calls)
