"""T-509: the writer — one bilingual draft from the analyst's claims, held to what the run wrote."""

import uuid

from sqlalchemy import select

from autora.app import build_behaviors
from autora.db.models import Agent, EventRecord, Task
from autora.domains.newsroom.agents import writer
from autora.domains.newsroom.agents.writer import (
    ArticleDraft,
    cites_key_numbers,
    draft_context,
    draft_written_here,
    revision_says_what_changed,
)
from autora.domains.newsroom.models import Article, ArticleVersion, Claim, ClaimStatus
from autora.runtime.behaviors import RunContext
from autora.runtime.task_manager import TaskManager
from tests.newsroom.agents.conftest import run_line


async def _versions(session, group_id):
    rows = await session.scalars(
        select(ArticleVersion).where(ArticleVersion.draft_group_id == group_id)
    )
    return {row.lang: row for row in rows}


async def test_a_writer_run_drafts_both_languages_from_the_claims(committed, e2e_settings):
    line = await run_line(committed, e2e_settings, until="draft")
    async with committed() as session:
        done = await session.get(Task, line.tasks["draft"].id)
        assert done.state == "SUCCEEDED", done.last_error
        note = ArticleDraft.model_validate(done.output)
        analysis = (await session.get(Task, line.tasks["analysis"].id)).output
        article = await session.get(Article, note.article_id)
        versions = await _versions(session, note.draft_group_id)
        created = await session.scalar(
            select(EventRecord).where(
                EventRecord.task_id == done.id, EventRecord.event_type == "ARTICLE_CREATED"
            )
        )
    assert article.story_id == line.story.id and article.state == "DRAFT"
    assert article.current_draft_group_id == note.draft_group_id
    assert set(versions) == {"zh-TW", "en"} == set(note.versions)
    assert {lang: row.id for lang, row in versions.items()} == note.versions
    zh, en = versions["zh-TW"], versions["en"]
    # the same claims in both languages: the analyst's claims
    assert set(zh.claim_ids) == set(en.claim_ids) == {uuid.UUID(c) for c in analysis["claim_ids"]}
    assert all(b["claim_ids"] for b in zh.body if b["type"] != "heading")
    assert "數據一覽" in zh.title and en.translation_of_version_id == zh.id
    assert created is not None and created.payload["article_id"] == str(article.id)


async def test_a_revision_writes_the_next_version(committed, e2e_settings):
    line = await run_line(committed, e2e_settings, until="draft")
    async with committed() as session:
        first = ArticleDraft.model_validate(
            (await session.get(Task, line.tasks["draft"].id)).output
        )
        revise = await TaskManager().add_task(
            session,
            company_id=line.company.id,
            project_id=line.project.id,
            name="draft",
            display_name="Revise",
            required_role="writer",
            input={
                "params": {
                    "story_id": str(line.story.id),
                    "issues": [{"lang": "en", "block_ref": "2", "message": "Say who counted."}],
                }
            },
            depends_on=[line.tasks["analysis"].id],
            ready=True,
        )
        await session.commit()
    await line.worker.run_until_idle()
    async with committed() as session:
        done = await session.get(Task, revise.id)
        assert done.state == "SUCCEEDED", done.last_error
        note = ArticleDraft.model_validate(done.output)
        article = await session.get(Article, note.article_id)
        versions = await _versions(session, note.draft_group_id)
        read = await session.scalar(
            select(EventRecord).where(
                EventRecord.task_id == revise.id,
                EventRecord.event_type == "TOOL_COMPLETED",
                EventRecord.payload["tool"].astext == "read_draft",
            )
        )
    assert note.article_id == first.article_id and note.draft_group_id != first.draft_group_id
    assert article.current_draft_group_id == note.draft_group_id
    assert {row.version for row in versions.values()} == {2}
    assert all(row.change_summary for row in versions.values())
    assert read is not None  # it read the draft it was revising


async def _upstream(session, room, run_task, **output):
    """An analysis task (done) the writer's task depends on."""
    upstream = await TaskManager().add_task(
        session,
        company_id=room.company.id,
        project_id=run_task.project_id,
        name="analysis",
        display_name="Analysis",
        required_role="analyst",
    )
    upstream.output = {
        "story_id": str(room.story.id),
        "claim_ids": list(room.claims.values()),
        "angle": "以數據看成本",
        "key_numbers": [],
        "contradictions": [],
        **output,
    }
    await session.flush()
    return upstream


def _ctx(room, task) -> RunContext:
    return RunContext(
        company_id=room.company.id,
        project_id=task.project_id,
        task=task,
        agent=Agent(role="writer"),
        run_id=uuid.uuid4(),
    )


async def test_the_context_gives_the_claims_and_the_editors_issues(newsroom_room):
    room = newsroom_room
    panels, cost = room.claims["panels"], room.claims["cost"]
    async with room.committed() as session:
        run_task = await session.scalar(
            select(Task).join(EventRecord, EventRecord.task_id == Task.id).where(
                EventRecord.company_id == room.company.id
            ).limit(1)
        )  # fmt: skip
        upstream = await _upstream(
            session,
            room,
            run_task,
            key_numbers=[{"claim_id": panels, "value": "1,200 片"}],
            contradictions=[{"note": "市府與報導的經費不同", "evidence_ids": [], "claim_ids": []}],
        )
        task = Task(
            id=uuid.uuid4(),
            company_id=room.company.id,
            project_id=run_task.project_id,
            input={"params": {"story_id": str(room.story.id)}},
            depends_on=[upstream.id],
        )
        text = await draft_context(session, _ctx(room, task))
        for expected in (
            f"Story id: {room.story.id}",
            "Languages: zh-TW (primary), then en; all are required.",
            "Angle (from the analyst): 以數據看成本",
            f"- {panels} | number [key number: 1,200 片] | The microgrid links 1,200",
            f"- {cost} | number | The city spent NT$420 million",
            "Contested (write as who says what):\n- 市府與報導的經費不同",
        ):
            assert expected in text
        assert "revision" not in text

        task.input = {
            "params": {
                "story_id": str(room.story.id),
                "issues": [
                    {"lang": "en", "block_ref": "3", "message": "Cite the city."},
                    "Shorter.",
                ],
            }
        }
        text = await draft_context(session, _ctx(room, task))
        assert "The editor's issues:\n- [en 3] Cite the city.\n- Shorter." in text
        assert "read_draft" in text

        task.input = {"params": {"story_id": str(uuid.uuid4())}}
        assert "No story found" in await draft_context(session, _ctx(room, task))


async def test_the_validators_hold_the_draft_to_the_run(newsroom_room):
    """The draft is written by the room's run task, as write_draft calls from a writer run."""
    room = newsroom_room
    panels, cost = room.claims["panels"], room.claims["cost"]
    first = await room.call("write_draft", room.draft([panels, cost]))
    assert first.ok, first.message
    async with room.committed() as session:
        article = await session.scalar(select(Article).where(Article.story_id == room.story.id))
        run_task = await session.scalar(
            select(Task).join(EventRecord, EventRecord.task_id == Task.id).where(
                EventRecord.company_id == room.company.id
            ).limit(1)
        )  # fmt: skip
        run_task.input = {"params": {"story_id": str(room.story.id)}}
        ctx = _ctx(room, run_task)
        out = first.output
        good = ArticleDraft(
            article_id=out["article_id"],
            draft_group_id=out["draft_group_id"],
            versions=out["versions"],
        )
        for check in (draft_written_here, cites_key_numbers, revision_says_what_changed):
            assert await check(session, ctx, good) == []

        invented = good.model_copy(update={"draft_group_id": uuid.uuid4()})
        assert "does not exist" in (await draft_written_here(session, ctx, invented))[0]
        wrong_ids = good.model_copy(
            update={"article_id": uuid.uuid4(), "versions": {"zh-TW": uuid.uuid4()}}
        )
        issues = await draft_written_here(session, ctx, wrong_ids)
        assert any(f"article_id must be {article.id}" in i for i in issues)
        assert any("versions must be the draft's own" in i for i in issues)

        elsewhere = _ctx(room, Task(id=uuid.uuid4(), company_id=room.company.id, input={}))
        assert (
            "was not written in this task"
            in (await draft_written_here(session, elsewhere, good))[0]
        )

        # the analyst marked panels as a key number: a draft without it is sent back
        upstream = await _upstream(
            session, room, run_task, key_numbers=[{"claim_id": panels, "value": "1,200"}]
        )
        run_task.depends_on = [upstream.id]
        await session.commit()

    second = await room.call("write_draft", room.draft([cost]))
    assert second.ok, second.message
    async with room.committed() as session:
        run_task = await session.get(Task, run_task.id)
        ctx = _ctx(room, run_task)
        out = second.output
        latest = ArticleDraft(
            article_id=out["article_id"],
            draft_group_id=out["draft_group_id"],
            versions=out["versions"],
        )
        assert "report the latest draft" in (await draft_written_here(session, ctx, good))[0]
        assert await draft_written_here(session, ctx, latest) == []
        [left_out] = await cites_key_numbers(session, ctx, latest)
        assert f"leaves out the key number '1,200' (claim {panels})" in left_out
        assert await cites_key_numbers(session, ctx, good) == []

        # a revision must say what changed
        run_task.input = {"params": {"story_id": str(room.story.id), "issues": ["Shorter."]}}
        [unsaid] = await revision_says_what_changed(session, ctx, latest)
        assert "change_summary" in unsaid
        await session.commit()

    third = await room.call(
        "write_draft", room.draft([panels, cost]) | {"change_summary": "縮短並補上面板數。"}
    )
    assert third.ok, third.message
    async with room.committed() as session:
        ctx = _ctx(room, await session.get(Task, run_task.id))
        revised = ArticleDraft(
            article_id=third.output["article_id"],
            draft_group_id=third.output["draft_group_id"],
            versions=third.output["versions"],
        )
        assert await revision_says_what_changed(session, ctx, revised) == []

        # a key number the fact-check rejected cannot be cited, so it is not asked for
        rejected = await session.get(Claim, uuid.UUID(panels))
        rejected.status = ClaimStatus.REJECTED.value
        await session.flush()
        assert await cites_key_numbers(session, ctx, latest) == []


def test_the_worker_knows_the_writer():
    behavior = build_behaviors().resolve("writer", "draft")
    assert behavior is writer.BEHAVIOR and behavior.output_model is ArticleDraft
    assert behavior.capability == "drafting"
    assert set(behavior.tools) == {"write_draft", "read_draft", "list_claims", "read_evidence"}
    assert "never Simplified" in behavior.system_prompt
    assert "exactly the same claims" in behavior.system_prompt
    # echo's writer still works its own task
    assert build_behaviors().resolve("writer", "echo_write").task_name == "echo_write"
