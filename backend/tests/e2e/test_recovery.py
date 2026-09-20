"""T-215: a worker is killed with SIGKILL mid-run; a new worker finishes the workflow.

Real processes (``backend/worker/main.py``), real leases, real clock:

1. worker 1 runs the researcher's task; its note is committed, then the (simulated) model is
   slow and the process is killed while waiting for it;
2. worker 2 starts, reclaims the expired lease, re-runs the task as attempt 2 and finishes
   the whole chain;
3. nothing was produced twice: one note per task (attempt 2 got the note attempt 1 wrote, through
   the tool call's idempotency key), one TASK_SUCCEEDED per task, and the crashed run is
   recorded as ABORTED (timeout), not lost.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import func, select

from autora.db.models import ActivityState, AgentActivity, AgentRun, EventRecord, Task, WorkflowRun
from autora.domains.echo.models import EchoNote
from autora.runtime.actor import Actor
from tests.echo_fixtures import start_echo

WORKER = Path(__file__).resolve().parents[2] / "worker" / "main.py"
ORDER = ("echo_research", "echo_analyze", "echo_write")


def _spawn(name: str, env: dict[str, str], log_dir: Path) -> tuple[subprocess.Popen, Path]:
    log = log_dir / f"{name}.log"
    proc = subprocess.Popen(
        [sys.executable, str(WORKER)],
        env={**env, "WORKER_ID": name},
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
    )
    return proc, log


async def _wait_for(check, timeout: float, what: str, logs: list[Path]):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await check()
        if result:
            return result
        await asyncio.sleep(0.1)
    dumps = "\n".join(f"--- {p.name}\n{p.read_text()[-4000:]}" for p in logs if p.exists())
    raise AssertionError(f"timed out after {timeout}s waiting for {what}\n{dumps}")


async def test_killed_worker_is_recovered_without_duplicates(
    committed, e2e_settings, echo_company, tmp_path
):
    company_id = echo_company.company.id
    wf, tasks = await start_echo(
        committed,
        echo_company,
        pause={"task": "echo_research", "attempt": 1, "seconds": 120},
    )
    research_id = tasks["echo_research"].id
    env = {
        **os.environ,
        "DATABASE_URL": e2e_settings.database_url,
        "AUTORA_ENV": "test",
        "MODEL_PROVIDER": "fake",
        "BLOB_STORE_DIR": str(e2e_settings.blob_store_dir),
        "WORKER_COMPANY_IDS": json.dumps([str(company_id)]),
        "WORKER_POLL_SECONDS": "0.1",
        "WORKER_MAINTENANCE_SECONDS": "0.3",
        "TASK_LEASE_SECONDS": "2",
        "TASK_RETRY_BASE_SECONDS": "0.2",
    }

    # 1. Worker 1 writes the researcher's note, then hangs on the slow model reply: kill it.
    first, first_log = _spawn("worker-1", env, tmp_path)
    logs = [first_log]
    try:

        async def note_written():
            async with committed() as session:
                return await session.scalar(select(EchoNote).where(EchoNote.task_id == research_id))

        note = await _wait_for(note_written, 30, "worker 1 to write the note", logs)
        await asyncio.sleep(0.3)  # TOOL_COMPLETED and the act step commit right after the note
    finally:
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
    assert first.returncode == -signal.SIGKILL

    async with committed() as session:
        task = await session.get(Task, research_id)
        [crashed] = (
            await session.scalars(select(AgentRun).where(AgentRun.task_id == research_id))
        ).all()
    assert task.state == "RUNNING" and task.lease_owner == "worker-1", "the crash left the lease"
    assert crashed.state == "RUNNING" and note.run_id == crashed.id

    # 2. Worker 2 reclaims the lease once it expires and finishes the chain.
    second, second_log = _spawn("worker-2", env, tmp_path)
    logs.append(second_log)
    try:

        async def workflow_done():
            async with committed() as session:
                run = await session.get(WorkflowRun, wf.id, populate_existing=True)
                return run.state if run.state != "RUNNING" else None

        final = await _wait_for(workflow_done, 60, "worker 2 to finish the workflow", logs)
    finally:
        second.send_signal(signal.SIGTERM)
        second.wait(timeout=30)
    assert final == "SUCCEEDED", second_log.read_text()[-4000:]
    assert second.returncode == 0, "SIGTERM is a clean shutdown"
    assert "reclaimed 1 expired lease" in second_log.read_text()

    # 3. Nothing twice.
    async with committed() as session:
        rows = {name: await session.get(Task, tasks[name].id) for name in ORDER}
        notes = (
            await session.scalars(select(EchoNote).where(EchoNote.company_id == company_id))
        ).all()
        research_runs = (
            await session.scalars(
                select(AgentRun).where(AgentRun.task_id == research_id).order_by(AgentRun.attempt)
            )
        ).all()
        succeeded = await session.scalar(
            select(func.count()).where(
                EventRecord.workflow_run_id == wf.id, EventRecord.event_type == "TASK_SUCCEEDED"
            )
        )
        rerun_tool = await session.scalar(
            select(EventRecord).where(
                EventRecord.run_id == research_runs[-1].id,
                EventRecord.event_type == "TOOL_COMPLETED",
            )
        )
        researcher = await session.get(AgentActivity, echo_company.agents["researcher"].id)

    assert [r.state for r in rows.values()] == ["SUCCEEDED"] * 3
    assert len(notes) == 3 and {n.task_id for n in notes} == {t.id for t in rows.values()}
    assert rows["echo_research"].attempt == 2
    assert [(r.attempt, r.state) for r in research_runs] == [(1, "ABORTED"), (2, "COMPLETED")]
    assert research_runs[0].error["error_class"] == "Aborted:timeout"
    assert succeeded == 3
    assert rerun_tool.payload["produced"] == [{"type": "echo_note", "id": str(note.id)}]
    assert rerun_tool.payload["result_summary"] == f"reused note {note.id}"
    assert rows["echo_research"].output["note_id"] == str(note.id)
    assert researcher.state == ActivityState.COMPLETED


async def test_killed_worker_loses_no_draft(committed, e2e_settings, tmp_path):
    """AC-S4 with the writer: the draft is written, the worker dies, and there is one draft.

    The echo test above proves the mechanism on a three-task chain. This one proves it where
    the acceptance criteria ask for it — on the newsroom's writer, whose tool writes rows a
    person would notice twice: an article and its language versions.
    """
    from autora.app import build_embedder, build_page_fetcher, build_runtime, build_search_provider
    from autora.domains.newsroom.demo import gather_stories, pick, seed_demo, start_demo_story
    from autora.domains.newsroom.models import Article, ArticleVersion
    from autora.domains.newsroom.sources import SourcePoller
    from autora.domains.newsroom.stories import StoryDesk

    runtime = build_runtime(e2e_settings)
    poller = SourcePoller(
        fetcher=build_page_fetcher(e2e_settings), search=build_search_provider(e2e_settings)
    )
    desk = StoryDesk(build_embedder(e2e_settings), threshold=e2e_settings.story_match_threshold)
    slug = f"recovery-{uuid.uuid4().hex[:8]}"

    async with committed() as session:
        demo = await seed_demo(session, actor=Actor.human("recovery-operator"), slug=slug)
        stories = await gather_stories(session, demo.company.id, poller=poller, desk=desk)
        story = pick(stories, "microgrid") or stories[0]
        run = await start_demo_story(
            session,
            policy=runtime.policy,
            workflows=runtime.workflows,
            desk=desk,
            story=story,
            project_id=demo.project.id,
            actor=Actor.human("recovery-operator"),
            pause={"task": "draft", "attempt": 1, "seconds": 300},
        )
        await session.commit()
        company_id, run_id = demo.company.id, run.id

    env = {
        **os.environ,
        "DATABASE_URL": e2e_settings.database_url,
        "AUTORA_ENV": "test",
        "MODEL_PROVIDER": "fake",
        "EMBED_PROVIDER": "fake",
        "TOOLS_PROFILE": "fixture",
        "BLOB_STORE_DIR": str(e2e_settings.blob_store_dir),
        "WORKER_COMPANY_IDS": json.dumps([str(company_id)]),
        "WORKER_POLL_SECONDS": "0.1",
        "WORKER_MAINTENANCE_SECONDS": "0.3",
        "TASK_LEASE_SECONDS": "2",
        "TASK_RETRY_BASE_SECONDS": "0.2",
    }

    async def draft_task(session):
        return await session.scalar(
            select(Task).where(Task.workflow_run_id == run_id, Task.name == "draft")
        )

    # 1. Worker 1 gets as far as the writer's draft, then hangs on the reply: kill it.
    first, first_log = _spawn("writer-worker-1", env, tmp_path)
    logs = [first_log]
    try:

        async def draft_written():
            async with committed() as session:
                return await session.scalar(
                    select(ArticleVersion).where(ArticleVersion.company_id == company_id)
                )

        version = await _wait_for(draft_written, 120, "the writer to save a draft", logs)
        await asyncio.sleep(0.5)  # TOOL_COMPLETED commits right after the draft
    finally:
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
    assert first.returncode == -signal.SIGKILL

    async with committed() as session:
        task = await draft_task(session)
    assert task.state == "RUNNING" and task.lease_owner == "writer-worker-1"

    # 2. Worker 2 reclaims the lease and finishes the writer's task.
    second, second_log = _spawn("writer-worker-2", env, tmp_path)
    logs.append(second_log)
    try:

        async def draft_done():
            async with committed() as session:
                task = await draft_task(session)
                return task.state if task.state in ("SUCCEEDED", "FAILED", "CANCELLED") else None

        final = await _wait_for(draft_done, 180, "worker 2 to finish the draft", logs)
    finally:
        second.send_signal(signal.SIGTERM)
        second.wait(timeout=30)

    # 3. One draft, written once, finished on the second attempt.
    async with committed() as session:
        task = await draft_task(session)
        runs = (
            await session.scalars(
                select(AgentRun).where(AgentRun.task_id == task.id).order_by(AgentRun.attempt)
            )
        ).all()
        articles = await session.scalar(
            select(func.count()).select_from(Article).where(Article.company_id == company_id)
        )
        versions = (
            await session.scalars(
                select(ArticleVersion).where(ArticleVersion.company_id == company_id)
            )
        ).all()
        succeeded = await session.scalar(
            select(func.count()).where(
                EventRecord.task_id == task.id, EventRecord.event_type == "TASK_SUCCEEDED"
            )
        )

    assert final == "SUCCEEDED", second_log.read_text()[-4000:]
    assert task.attempt == 2
    assert [(r.attempt, r.state) for r in runs] == [(1, "ABORTED"), (2, "COMPLETED")]
    assert articles == 1, "the crash left a second article behind"
    assert {v.version for v in versions} == {1}, "the draft was written twice"
    assert version.id in {v.id for v in versions}, "the saved draft was replaced, not reused"
    assert succeeded == 1
    assert "reclaimed 1 expired lease" in second_log.read_text()
