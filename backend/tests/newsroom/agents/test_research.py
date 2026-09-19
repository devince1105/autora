"""T-506: the researcher — its behavior, validators and simulated model, run by a real worker."""

import uuid

from sqlalchemy import select

from autora.app import build_behaviors, build_worker
from autora.company.agents import hire_agent
from autora.db.models import Agent, EventRecord, Project, Task
from autora.domains.newsroom.agents.researcher import (
    ROLE,
    TASK,
    ResearchNote,
    SourceSummary,
    about_the_task_story,
    evidence_captured_here,
    one_summary_per_source,
    research_context,
)
from autora.domains.newsroom.models import Evidence, SourceItem, Story, StoryItem
from autora.runtime.actor import Actor
from autora.runtime.behaviors import RunContext
from autora.runtime.task_manager import TaskManager
from tests.conftest import unique_company

SETUP = Actor.system("test")


async def research_task(committed, *, story_title="Lumen City microgrid", params=None):
    async with committed() as session:
        company = await unique_company(session, "research")
        project = Project(
            company_id=company.id,
            name="newsroom",
            state="ACTIVE",
            kill_criteria={"max_cost_usd": 10},
        )
        session.add(project)
        await session.flush()
        agent = await hire_agent(
            session, company_id=company.id, role=ROLE, display_name="Rae", actor=SETUP
        )
        story = Story(company_id=company.id, title=story_title, state="SELECTED")
        session.add(story)
        await session.flush()
        task = await TaskManager().add_task(
            session,
            company_id=company.id,
            project_id=project.id,
            name=TASK,
            display_name=f"Research: {story_title}",
            required_role=ROLE,
            input={"params": {"story_id": str(story.id), **(params or {})}},
        )
        await session.commit()
    return company, agent, story, task


async def test_a_researcher_run_captures_real_evidence(committed, e2e_settings):
    company, agent, story, task = await research_task(committed)
    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company.id})
    )
    await worker.run_until_idle()

    async with committed() as session:
        done = await session.get(Task, task.id)
        assert done.state == "SUCCEEDED", done.state
        note = ResearchNote.model_validate(done.output)
        evidence = (
            await session.scalars(select(Evidence).where(Evidence.id.in_(note.evidence_ids)))
        ).all()
        fetched = (
            await session.scalars(
                select(EventRecord).where(
                    EventRecord.task_id == task.id, EventRecord.event_type == "EVIDENCE_CAPTURED"
                )
            )
        ).all()
    assert note.story_id == story.id
    assert len(evidence) >= 2 and len({e.url.split("/")[2] for e in evidence}) >= 2  # several sites
    assert {str(e.id) for e in evidence} <= {f.payload["evidence_id"] for f in fetched}
    assert all(
        "microgrid" in e.extracted_text.lower() or "微電網" in e.extracted_text for e in evidence
    )
    assert {s.evidence_id for s in note.source_summaries} == set(note.evidence_ids)
    assert all(s.summary.startswith("來源") for s in note.source_summaries)  # zh-TW


async def test_the_context_gives_the_story_and_its_leads(committed, db_session):
    company = await unique_company(db_session, "leads")
    story = Story(
        company_id=company.id,
        title="Lumen City microgrid",
        summary="A pilot.",
        seed={"query": "microgrid pilot"},
    )
    db_session.add(story)
    await db_session.flush()
    from autora.domains.newsroom.models import Source

    source = Source(
        company_id=company.id, name="news", kind="rss", url="https://x.test/feed", status="paused"
    )
    db_session.add(source)
    await db_session.flush()
    item = SourceItem(
        company_id=company.id,
        source_id=source.id,
        external_id="1",
        url="https://x.test/a",
        title="A lead",
        content_hash="h",
    )
    db_session.add(item)
    await db_session.flush()
    db_session.add(StoryItem(story_id=story.id, source_item_id=item.id, company_id=company.id))
    await db_session.flush()
    ctx = _ctx(company.id, story.id)
    text = await research_context(db_session, ctx)
    for expected in (
        "Story: Lumen City microgrid",
        f"Story id: {story.id}",
        "Summary: A pilot.",
        "Suggested search: microgrid pilot",
        "- A lead — https://x.test/a",
        "at least 2 sources",
    ):
        assert expected in text
    missing = await research_context(db_session, _ctx(company.id, uuid.uuid4()))
    assert "No story found" in missing


def _ctx(company_id, story_id, task=None) -> RunContext:
    task = task or Task(
        id=uuid.uuid4(), company_id=company_id, input={"params": {"story_id": str(story_id)}}
    )
    return RunContext(
        company_id=company_id,
        project_id=uuid.uuid4(),
        task=task,
        agent=Agent(role=ROLE),
        run_id=uuid.uuid4(),
    )


async def test_the_validators_hold_the_note_to_the_run(newsroom_room):
    """newsroom_room captured two pages from two sites in its run's task."""
    room = newsroom_room
    async with room.committed() as session:
        run_task = await session.scalar(
            select(Task)
            .join(EventRecord, EventRecord.task_id == Task.id)
            .where(EventRecord.company_id == room.company.id)
            .limit(1)
        )
        run_task.input = {"params": {"story_id": str(room.story.id)}}
        ctx = _ctx(room.company.id, room.story.id, task=run_task)
        pilot, press = (uuid.UUID(e) for e in room.evidence.values())

        def note(evidence, summaries=None, story=room.story.id):
            return ResearchNote(
                story_id=story,
                evidence_ids=evidence,
                source_summaries=[
                    SourceSummary(evidence_id=e, summary="一段夠長的來源摘要。")
                    for e in (summaries or evidence)
                ],
                suggested_angles=["角度"],
            )

        good = note([pilot, press])
        for check in (about_the_task_story, evidence_captured_here, one_summary_per_source):
            assert await check(session, ctx, good) == []

        assert (
            "must be this task's story"
            in (await about_the_task_story(session, ctx, note([pilot, press], story=uuid.uuid4())))[
                0
            ]
        )
        invented = uuid.uuid4()
        [problem] = await evidence_captured_here(session, ctx, note([pilot, invented]))
        assert f"evidence {invented} was not captured in this task" in problem
        assert (
            "at least 2 sources" in (await evidence_captured_here(session, ctx, note([pilot])))[0]
        )
        issues = await one_summary_per_source(
            session, ctx, note([pilot, press], summaries=[pilot, pilot, invented])
        )
        assert any("not in evidence_ids" in i for i in issues)
        assert any(f"no summary for evidence {press}" in i for i in issues)
        assert any("more than one summary" in i for i in issues)

        one_site = ctx.task.input | {"params": {"story_id": str(room.story.id), "min_sources": 1}}
        ctx.task.input = one_site
        assert await evidence_captured_here(session, ctx, note([pilot])) == []


def test_the_worker_knows_the_researcher():
    behavior = build_behaviors().resolve(ROLE, TASK)
    assert behavior.output_model is ResearchNote
    assert set(behavior.tools) == {"web_search", "fetch_url", "read_evidence", "search_evidence"}
    assert (
        "Traditional Chinese" in behavior.system_prompt
        and "never Simplified" in behavior.system_prompt
    )
    # echo's researcher still works its own task
    assert build_behaviors().resolve(ROLE, "echo_research").task_name == "echo_research"


async def test_a_model_that_invents_sources_fails_its_attempt(committed, e2e_settings):
    from autora.runtime.models.providers.fake import FakeTurn

    company, agent, story, task = await research_task(committed)
    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company.id})
    )
    fake = worker.runner.gateway.providers["fake"]
    invented = {
        "story_id": str(story.id),
        "evidence_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
        "source_summaries": [],
        "suggested_angles": ["角度"],
    }
    invented["source_summaries"] = [
        {"evidence_id": e, "summary": "捏造的來源摘要內容。"} for e in invented["evidence_ids"]
    ]
    # three attempts at inventing: the first answer and both repairs
    fake.script(ROLE, TASK, 1, *[FakeTurn(structured=invented)] * 3)
    await worker.run_until_idle()
    async with committed() as session:
        steps = (
            await session.scalars(
                select(EventRecord).where(
                    EventRecord.task_id == task.id, EventRecord.event_type == "AGENT_RUN_FAILED"
                )
            )
        ).all()
        done = await session.get(Task, task.id)
    # the first attempt fails on the validator and the task waits for its retry
    [failed] = steps
    assert failed.payload["error_class"] == "EvaluationFailed" and not failed.payload["final"]
    assert "was not captured in this task" in failed.payload["message"]
    assert done.state == "READY" and done.attempt == 1  # the retry backoff has not passed yet
