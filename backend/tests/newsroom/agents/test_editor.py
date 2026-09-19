"""T-511: the editor — fact-checks the draft, decides, and reports what it really decided."""

import uuid

from sqlalchemy import delete, select

from autora.app import build_behaviors
from autora.db.models import Agent, EventRecord, Task
from autora.domains.newsroom.agents import editor
from autora.domains.newsroom.agents.editor import (
    EditorReview,
    decided_here,
    rests_on_its_fact_check,
    review_context,
)
from autora.domains.newsroom.models import Article, ArticleVersion, ClaimEvidence, FactCheckReport
from autora.domains.newsroom.tools.review import Issue
from autora.runtime.behaviors import RunContext
from tests.newsroom.agents.conftest import add_task, run_line


async def _done(committed, task_id) -> Task:
    async with committed() as session:
        task = await session.get(Task, task_id)
        assert task.state == "SUCCEEDED", task.state
        return task


async def test_an_editor_run_accepts_a_draft_that_passes(committed, e2e_settings):
    line = await run_line(committed, e2e_settings, until="review")
    review = EditorReview.model_validate((await _done(committed, line.tasks["review"].id)).output)
    async with committed() as session:
        article = await session.get(Article, review.article_id)
        report = await session.get(FactCheckReport, review.fact_check_report_id)
        reviewed = await session.scalar(
            select(EventRecord).where(
                EventRecord.task_id == line.tasks["review"].id,
                EventRecord.event_type == "ARTICLE_REVIEWED",
            )
        )
    assert review.verdict == "accept" and review.issues == []
    assert article.story_id == line.story.id and article.state == "IN_REVIEW"
    assert report.passed and report.task_id == line.tasks["review"].id
    assert reviewed.payload["verdict"] == "accept" and reviewed.payload["by_role"] == "editor"


async def test_a_failing_draft_goes_back_and_the_revision_is_accepted(committed, e2e_settings):
    line = await run_line(committed, e2e_settings, until="draft")
    # one cited claim loses its evidence: the fact-check will reject it
    async with committed() as session:
        draft = (await session.get(Task, line.tasks["draft"].id)).output
        cited = (await session.get(ArticleVersion, uuid.UUID(draft["versions"]["zh-TW"]))).claim_ids
        await session.execute(delete(ClaimEvidence).where(ClaimEvidence.claim_id == cited[0]))
        await session.commit()

    first = await add_task(committed, line, "review", depends_on=[line.tasks["draft"].id])
    await line.worker.run_until_idle()
    review = EditorReview.model_validate((await _done(committed, first.id)).output)
    assert review.verdict == "revise" and review.issues
    assert review.issues[0].kind == "unsupported" and "沒有通過事實查核" in review.issues[0].message
    async with committed() as session:
        article = await session.get(Article, review.article_id)
        assert article.state == "DRAFT" and article.revision_count == 1

    # the workflow (T-514) hands the issues to the writer's revision task
    issues = [i.model_dump(exclude_none=True) for i in review.issues]
    revision = await add_task(
        committed,
        line,
        "draft",
        params={"issues": issues},
        depends_on=[line.tasks["analysis"].id],
    )
    await line.worker.run_until_idle()
    revised = (await _done(committed, revision.id)).output
    second = await add_task(committed, line, "review", depends_on=[revision.id])
    await line.worker.run_until_idle()
    again = EditorReview.model_validate((await _done(committed, second.id)).output)
    async with committed() as session:
        article = await session.get(Article, again.article_id)
        v2 = await session.get(ArticleVersion, uuid.UUID(revised["versions"]["zh-TW"]))
    assert again.verdict == "accept" and article.state == "IN_REVIEW"
    assert v2.version == 2 and cited[0] not in v2.claim_ids  # the rejected claim is gone
    assert article.current_draft_group_id == uuid.UUID(revised["draft_group_id"])


def _ctx(company_id, task) -> RunContext:
    return RunContext(
        company_id=company_id,
        project_id=task.project_id,
        task=task,
        agent=Agent(role="editor"),
        run_id=uuid.uuid4(),
    )


async def test_the_context_gives_the_draft_and_the_revisions_left(newsroom_room):
    room = newsroom_room
    task = Task(
        id=uuid.uuid4(),
        company_id=room.company.id,
        input={"params": {"story_id": str(room.story.id)}},
    )
    async with room.committed() as session:
        text = await review_context(session, _ctx(room.company.id, task))
    assert "has not drafted this story's article yet" in text

    drafted = await room.call("write_draft", room.draft(list(room.claims.values())))
    async with room.committed() as session:
        article = await session.get(Article, uuid.UUID(drafted.output["article_id"]))
        article.revision_count = 2
        await session.flush()
        text = await review_context(session, _ctx(room.company.id, task))
    for expected in (
        f"Story id: {room.story.id}",
        f"Article id: {article.id}",
        "Draft: version 1 (zh-TW, en); state DRAFT",
        "Languages required: zh-TW, en",
        "Revisions so far: 2 of 2",
        "No revisions left: asking for another one drops the story.",
    ):
        assert expected in text


async def test_the_validators_hold_the_review_to_the_run(newsroom_room):
    room = newsroom_room
    drafted = await room.call("write_draft", room.draft(list(room.claims.values())))
    article_id = drafted.output["article_id"]
    checked = await room.call("run_fact_check", {"article_id": article_id})
    report_id = checked.output["report_id"]
    async with room.committed() as session:
        run_task = await session.scalar(
            select(Task).join(EventRecord, EventRecord.task_id == Task.id).where(
                EventRecord.company_id == room.company.id
            ).limit(1)
        )  # fmt: skip
        run_task.input = {"params": {"story_id": str(room.story.id)}}
        ctx = _ctx(room.company.id, run_task)

        def note(**kw):
            return EditorReview(
                **{
                    "article_id": article_id,
                    "verdict": "accept",
                    "fact_check_report_id": report_id,
                }
                | kw
            )

        # nothing decided yet
        assert "nothing was decided" in (await decided_here(session, ctx, note()))[0]
        await session.commit()

    accepted = await room.call(
        "accept_draft", {"article_id": article_id, "fact_check_report_id": report_id}
    )
    assert accepted.ok, accepted.message
    async with room.committed() as session:
        ctx = _ctx(room.company.id, await session.get(Task, run_task.id))
        for check in (decided_here, rests_on_its_fact_check):
            assert await check(session, ctx, note()) == []
        assert (
            "the verdict taken was 'accept'"
            in (
                await decided_here(
                    session, ctx, note(verdict="revise", issues=[Issue(message="x" * 5)])
                )
            )[0]
        )
        assert (
            "does not exist" in (await decided_here(session, ctx, note(article_id=uuid.uuid4())))[0]
        )
        elsewhere = Task(
            id=uuid.uuid4(),
            company_id=room.company.id,
            input={"params": {"story_id": str(uuid.uuid4())}},
        )
        assert (
            "not this task's story's article"
            in (await decided_here(session, _ctx(room.company.id, elsewhere), note()))[0]
        )
        assert (
            "nothing was decided"
            in (
                await decided_here(
                    session,
                    _ctx(
                        room.company.id, Task(id=uuid.uuid4(), company_id=room.company.id, input={})
                    ),
                    note(),
                )
            )[0]
        )

        # the report must be this review's own, for this article
        assert (
            "does not exist"
            in (
                await rests_on_its_fact_check(session, ctx, note(fact_check_report_id=uuid.uuid4()))
            )[0]
        )
        other_report = FactCheckReport(
            company_id=room.company.id,
            article_id=uuid.UUID(article_id),
            article_version_id=uuid.UUID(drafted.output["versions"]["zh-TW"]),
            draft_group_id=uuid.UUID(drafted.output["draft_group_id"]),
            passed=True,
            results=[],
            semantic_review=[],
        )
        session.add(other_report)
        await session.flush()
        assert (
            "the run_fact_check you ran in this review"
            in (
                await rests_on_its_fact_check(
                    session, ctx, note(fact_check_report_id=other_report.id)
                )
            )[0]
        )
        # accept with issues; revise without issues
        with_issues = note(issues=[Issue(message="小問題一個")])
        assert "has no issues" in (await rests_on_its_fact_check(session, ctx, with_issues))[0]
        assert (
            "at least one issue"
            in (await rests_on_its_fact_check(session, ctx, note(verdict="revise")))[0]
        )
        # accept on a failed report
        report = await session.get(FactCheckReport, uuid.UUID(report_id))
        report.passed = False
        await session.flush()
        assert "did not pass" in (await rests_on_its_fact_check(session, ctx, note()))[0]
        await session.rollback()


async def test_a_revision_reports_the_issues_it_sent(newsroom_room):
    room = newsroom_room
    drafted = await room.call("write_draft", room.draft(list(room.claims.values())))
    article_id = drafted.output["article_id"]
    checked = await room.call("run_fact_check", {"article_id": article_id})
    sent = [{"message": "第一個問題"}, {"message": "第二個問題"}]
    asked = await room.call("request_revision", {"article_id": article_id, "issues": sent})
    assert asked.ok, asked.message
    async with room.committed() as session:
        run_task = await session.scalar(
            select(Task).join(EventRecord, EventRecord.task_id == Task.id).where(
                EventRecord.company_id == room.company.id
            ).limit(1)
        )  # fmt: skip
        run_task.input = {"params": {"story_id": str(room.story.id)}}
        ctx = _ctx(room.company.id, run_task)
        review = EditorReview(
            article_id=article_id,
            verdict="revise",
            fact_check_report_id=checked.output["report_id"],
            issues=[Issue(message="第一個問題")],
        )
        assert await decided_here(session, ctx, review) == []
        [problem] = await rests_on_its_fact_check(session, ctx, review)
        assert "report the 2 issues you sent with request_revision (you listed 1)" in problem
        both = review.model_copy(update={"issues": [Issue(**i) for i in sent]})
        assert await rests_on_its_fact_check(session, ctx, both) == []


def test_the_worker_knows_the_editor():
    behavior = build_behaviors().resolve("editor", "review")
    assert behavior is editor.BEHAVIOR and behavior.output_model is EditorReview
    assert behavior.capability == "editing"
    assert {"read_draft", "run_fact_check", "accept_draft", "request_revision"} <= set(
        behavior.tools
    )
    assert "never Simplified" in behavior.system_prompt
