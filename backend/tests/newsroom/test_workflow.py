"""T-514: the newsroom workflow — a story to a published article, run by a real worker (simulated
model, offline tools), with a person's approval, automatic approval, revisions and rejection."""

import uuid

import pytest
from sqlalchemy import delete, select

from autora.app import build_runtime, build_worker
from autora.company.agents import hire_agent
from autora.db.models import (
    AgentActivity,
    Approval,
    EventRecord,
    Project,
    Task,
    WorkflowRun,
)
from autora.db.repositories.companies import upsert_policy
from autora.domains.newsroom.models import (
    Article,
    ArticleVersion,
    ClaimEvidence,
    Distribution,
    Story,
)
from autora.domains.newsroom.policy import AUTO_APPROVE_KEY
from autora.domains.newsroom.workflow import TEMPLATE_NAME, start_story
from autora.runtime.actor import Actor
from tests.conftest import unique_company

OPERATOR = Actor.human("operator")
ROLES = ("researcher", "analyst", "writer", "editor", "marketing")


class Newsroom:
    """A staffed company with a selected story, its workflow started, and a worker."""

    async def start(self, committed, settings, *, auto_approve=False):
        self.committed = committed
        self.runtime = build_runtime()
        async with committed() as session:
            self.company = await unique_company(session, "workflow")
            project = Project(
                company_id=self.company.id,
                name="newsroom",
                state="ACTIVE",
                kill_criteria={"max_cost_usd": 10},
            )
            session.add(project)
            await session.flush()
            self.agents = {}
            for role in ROLES:
                agent = await hire_agent(
                    session,
                    company_id=self.company.id,
                    role=role,
                    display_name=role,
                    actor=OPERATOR,
                )
                self.agents[role] = agent
            if auto_approve:
                await upsert_policy(
                    session, self.company.id, AUTO_APPROVE_KEY, True, updated_by=OPERATOR.as_json()
                )
            self.story = Story(
                company_id=self.company.id, title="Lumen City microgrid", state="SELECTED"
            )
            session.add(self.story)
            await session.flush()
            self.run = await start_story(
                session,
                policy=self.runtime.policy,
                workflows=self.runtime.workflows,
                story=self.story,
                project_id=project.id,
                actor=OPERATOR,
            )
            await session.commit()
        self.worker = build_worker(
            settings, session_factory=committed, company_ids=frozenset({self.company.id})
        )
        return self

    async def tasks(self) -> dict[str, list[Task]]:
        async with self.committed() as session:
            rows = (
                await session.scalars(
                    select(Task)
                    .where(Task.workflow_run_id == self.run.id)
                    .order_by(Task.created_at, Task.id)
                )
            ).all()
        out: dict[str, list[Task]] = {}
        for row in rows:
            out.setdefault(row.name, []).append(row)
        return out

    async def get(self, model, key):
        async with self.committed() as session:
            return await session.get(model, key)

    async def article(self) -> Article:
        async with self.committed() as session:
            return await session.scalar(select(Article).where(Article.story_id == self.story.id))

    async def approval(self) -> Approval:
        approve = (await self.tasks())["approve"][0]
        async with self.committed() as session:
            return await session.scalar(select(Approval).where(Approval.task_id == approve.id))

    async def decide(self, outcome, reason=None):
        approval = await self.approval()
        async with self.committed() as session:
            await self.runtime.approvals.decide(
                session, approval.id, outcome=outcome, actor=OPERATOR, reason=reason
            )
            await session.commit()

    async def events(self, event_type):
        async with self.committed() as session:
            return (
                await session.scalars(
                    select(EventRecord)
                    .where(
                        EventRecord.company_id == self.company.id,
                        EventRecord.event_type == event_type,
                    )
                    .order_by(EventRecord.id)
                )
            ).all()


async def test_a_story_goes_to_a_person_then_is_published_and_distributed(committed, e2e_settings):
    room = await Newsroom().start(committed, e2e_settings)
    assert (await room.get(Story, room.story.id)).state == "IN_PRODUCTION"
    await room.worker.run_until_idle()

    tasks = await room.tasks()
    for name in ("research", "analysis", "draft", "review"):
        assert tasks[name][0].state == "SUCCEEDED", name
    assert tasks["approve"][0].state == "WAITING_APPROVAL"
    assert tasks["approve"][0].required_role == "human"
    assert tasks["publish"][0].state == tasks["distribute"][0].state == "PENDING"
    assert tasks["draft"][0].display_name == "撰稿：Lumen City microgrid"
    approval = await room.approval()
    assert approval.kind == "article" and approval.action == "approve_article"
    assert approval.state == "PENDING" and approval.summary.startswith("核准發布：")
    article = await room.article()
    assert article.state == "IN_REVIEW" and approval.payload["article_id"] == str(article.id)

    # a person approves: the article is approved, then published, then marketing distributes
    await room.decide("approve", reason="looks good")
    await room.worker.run_until_idle()
    tasks = await room.tasks()
    assert all(t.state == "SUCCEEDED" for ts in tasks.values() for t in ts)
    assert (await room.get(WorkflowRun, room.run.id)).state == "SUCCEEDED"
    article = await room.article()
    assert article.state == "PUBLISHED" and article.published_langs == ["zh-TW", "en"]
    assert (await room.get(Story, room.story.id)).state == "PUBLISHED"
    [approved] = await room.events("ARTICLE_APPROVED")
    assert approved.payload["by"] == "human"
    publish = tasks["publish"][0]
    assert publish.output["urls"]["zh-TW"] == f"/news/zh-TW/articles/{article.slug}"
    async with committed() as session:
        channels = {
            d.channel: d.status
            for d in await session.scalars(
                select(Distribution).where(Distribution.article_id == article.id)
            )
        }
    assert channels == {"site": "published", "social_draft": "draft"}

    # the hand-offs are the template's: review unlocked approve, publish unlocked distribute
    succeeded = {e.task_id: e.payload["unlocks"] for e in await room.events("TASK_SUCCEEDED")}
    assert [u["task_id"] for u in succeeded[tasks["review"][0].id]] == [str(tasks["approve"][0].id)]
    assert succeeded[tasks["publish"][0].id][0]["required_role"] == "marketing"

    # every agent's activity links to its work (3d-office/06 §4)
    async with committed() as session:
        links = {
            role: (await session.get(AgentActivity, agent.id)).detail.get("links")
            for role, agent in room.agents.items()
        }
    href = f"/admin/newsroom/articles/{article.id}"
    assert links["researcher"] == [
        {"label": "題材與來源", "href": f"/admin/newsroom/stories/{room.story.id}"}
    ]
    assert links["analyst"][0]["href"] == f"/admin/newsroom/stories/{room.story.id}#claims"
    assert links["writer"] == [{"label": "文章草稿 v1", "href": f"{href}?version=1"}]
    assert links["editor"][1] == {"label": "事實查核", "href": f"{href}?version=1#fact-check"}
    assert links["marketing"] == [{"label": "發布紀錄", "href": f"{href}#distribution"}]


async def test_with_automatic_approval_nobody_is_asked(committed, e2e_settings):
    room = await Newsroom().start(committed, e2e_settings, auto_approve=True)
    await room.worker.run_until_idle()
    tasks = await room.tasks()
    assert all(t.state == "SUCCEEDED" for ts in tasks.values() for t in ts)
    assert tasks["approve"][0].output["approved_by"] == "system"
    assert await room.approval() is None
    [approved] = await room.events("ARTICLE_APPROVED")
    assert approved.payload["by"] == "system"
    assert (await room.article()).state == "PUBLISHED"


def _break_each_draft(room, times):
    """After each of the first ``times`` drafts, one cited claim loses its evidence: the
    fact-check rejects it and the editor sends the draft back."""
    broken = []

    async def on_outcome(outcome):
        async with room.committed() as session:
            run_task = await session.scalar(
                select(Task).join(EventRecord, EventRecord.task_id == Task.id).where(
                    EventRecord.run_id == outcome.run_id
                ).limit(1)
            )  # fmt: skip
            if run_task is None or run_task.name != "draft" or len(broken) >= times:
                return
            version = await session.get(
                ArticleVersion, uuid.UUID(run_task.output["versions"]["zh-TW"])
            )
            claim = version.claim_ids[0]
            await session.execute(delete(ClaimEvidence).where(ClaimEvidence.claim_id == claim))
            await session.commit()
            broken.append(claim)

    room.worker.on_outcome = on_outcome
    return broken


async def test_a_revision_round_then_approval(committed, e2e_settings):
    room = await Newsroom().start(committed, e2e_settings)
    broken = _break_each_draft(room, times=1)
    await room.worker.run_until_idle()

    tasks = await room.tasks()
    assert [t.state for t in tasks["draft"]] == ["SUCCEEDED", "SUCCEEDED"]
    assert [t.state for t in tasks["review"]] == ["SUCCEEDED", "SUCCEEDED"]
    first, second = tasks["review"]
    assert first.output["verdict"] == "revise" and second.output["verdict"] == "accept"
    redraft = tasks["draft"][1]
    assert redraft.display_name == "撰稿：Lumen City microgrid（第 2 輪）"
    assert redraft.input["params"]["issues"] == first.output["issues"]
    assert redraft.depends_on == [tasks["analysis"][0].id]
    assert tasks["approve"][0].depends_on == [second.id]
    assert tasks["approve"][0].state == "WAITING_APPROVAL"
    article = await room.article()
    assert article.revision_count == 1 and article.state == "IN_REVIEW"
    v2 = await room.get(ArticleVersion, uuid.UUID(redraft.output["versions"]["zh-TW"]))
    assert v2.version == 2 and broken[0] not in v2.claim_ids
    [extended] = await room.events("WORKFLOW_RUN_EXTENDED")
    assert extended.payload["round"] == 2
    assert extended.payload["task_ids"] == [str(redraft.id), str(second.id)]


async def test_too_many_revisions_drop_the_story(committed, e2e_settings):
    room = await Newsroom().start(committed, e2e_settings)
    _break_each_draft(room, times=3)
    await room.worker.run_until_idle()

    tasks = await room.tasks()
    assert len(tasks["draft"]) == len(tasks["review"]) == 3
    assert all(t.output["verdict"] == "revise" for t in tasks["review"])
    for name in ("approve", "publish", "distribute"):
        assert tasks[name][0].state == "CANCELLED", name
    assert (await room.get(WorkflowRun, room.run.id)).state == "CANCELLED"
    assert (await room.article()).state == "REJECTED"
    assert (await room.get(Story, room.story.id)).state == "DROPPED"


async def test_a_person_rejects_the_article(committed, e2e_settings):
    room = await Newsroom().start(committed, e2e_settings)
    await room.worker.run_until_idle()
    await room.decide("reject", reason="not for us")
    await room.worker.run_until_idle()
    tasks = await room.tasks()
    assert tasks["approve"][0].state == "CANCELLED"
    assert tasks["publish"][0].state == tasks["distribute"][0].state == "CANCELLED"
    assert (await room.get(WorkflowRun, room.run.id)).state == "CANCELLED"
    assert (await room.article()).state == "REJECTED"
    story = await room.get(Story, room.story.id)
    assert story.state == "DROPPED"
    [rejected] = await room.events("ARTICLE_REJECTED")
    assert rejected.payload == {"article_id": str(story_article_id(tasks)), "by": "human",
                                "reason": "not for us"}  # fmt: skip


def story_article_id(tasks):
    return uuid.UUID(tasks["draft"][-1].output["article_id"])


async def test_only_a_selected_story_starts(committed, e2e_settings):
    room = await Newsroom().start(committed, e2e_settings)
    runtime = build_runtime()
    async with committed() as session:
        story = await session.get(Story, room.story.id)  # IN_PRODUCTION now
        run = await session.get(WorkflowRun, room.run.id)
        assert run.template_name == TEMPLATE_NAME
        assert run.params == {"story_id": str(story.id), "title": "Lumen City microgrid"}
        try:
            await start_story(
                session,
                policy=runtime.policy,
                workflows=runtime.workflows,
                story=story,
                project_id=run.project_id,
                actor=OPERATOR,
            )
        except Exception as error:  # noqa: BLE001
            assert "only a selected story is started" in str(error)
        else:
            raise AssertionError("a story in production was started again")


async def test_a_person_sends_it_back_and_it_comes_back_changed(committed, e2e_settings):
    """D-044: at approval a person asks for changes. The writer drafts again with their reason,
    the editor reviews again, and a new approval waits; approving that one publishes."""
    room = await Newsroom().start(committed, e2e_settings)
    await room.worker.run_until_idle()
    await room.decide("revise", reason="標題不要用「狂加」")
    await room.worker.run_until_idle()

    tasks = await room.tasks()
    assert [len(tasks[n]) for n in ("draft", "review", "approve")] == [2, 2, 2]
    first_approve, second_approve = tasks["approve"]
    assert first_approve.output["decision"] == "revise"
    redraft = tasks["draft"][1]
    assert redraft.display_name == "撰稿：Lumen City microgrid（退回後第 2 輪）"
    assert redraft.input["params"]["issues"] == [
        {"message": "審批退回（人工）：標題不要用「狂加」"}
    ]
    assert second_approve.state == "WAITING_APPROVAL"
    assert tasks["publish"][0].depends_on == [second_approve.id], "publish waits for the new one"
    assert tasks["publish"][0].state == "PENDING"
    article = await room.article()
    assert article.state == "IN_REVIEW" and article.revision_count == 0, "not the editor's round"
    [returned] = await room.events("ARTICLE_RETURNED")
    assert returned.payload["reason"] == "標題不要用「狂加」"

    async with committed() as session:
        second = await session.scalar(select(Approval).where(Approval.task_id == second_approve.id))
        await room.runtime.approvals.decide(session, second.id, outcome="approve", actor=OPERATOR)
        await session.commit()
    await room.worker.run_until_idle()
    assert (await room.article()).state == "PUBLISHED"
    assert (await room.get(WorkflowRun, room.run.id)).state == "SUCCEEDED"


async def test_a_published_article_can_be_taken_down_and_put_back(committed, e2e_settings):
    """D-044: the site shows only PUBLISHED; taking it down keeps it, with its history."""
    from autora.domains.newsroom.publisher import (
        NotAllowed,
        republish_article,
        unpublish_article,
    )
    from autora.domains.newsroom.site import published_articles

    room = await Newsroom().start(committed, e2e_settings)
    await room.worker.run_until_idle()
    await room.decide("approve")
    await room.worker.run_until_idle()
    article = await room.article()
    slug = room.company.slug

    async with committed() as session:
        assert len(await published_articles(session, "zh-TW", company_slug=slug)) == 1
        with pytest.raises(NotAllowed):
            await unpublish_article(
                session, company_id=room.company.id, article_id=article.id,
                actor=Actor.system("x"), reason="x",
            )  # fmt: skip
        await unpublish_article(
            session, company_id=room.company.id, article_id=article.id, actor=OPERATOR,
            reason="用字需要修改",
        )  # fmt: skip
        await session.commit()
    async with committed() as session:
        assert await published_articles(session, "zh-TW", company_slug=slug) == []
        await republish_article(
            session, company_id=room.company.id, article_id=article.id, actor=OPERATOR
        )
        await session.commit()
    async with committed() as session:
        assert len(await published_articles(session, "zh-TW", company_slug=slug)) == 1
    assert [e.payload["reason"] for e in await room.events("ARTICLE_UNPUBLISHED")] == [
        "用字需要修改"
    ]
    assert len(await room.events("ARTICLE_REPUBLISHED")) == 1


# --- changing a published article (D-045) ---------------------------------------------------


async def _published(room, committed):
    await room.worker.run_until_idle()
    await room.decide("approve")
    await room.worker.run_until_idle()
    return await room.article()


async def _revise(room, committed, article, reason="把「狂加」改成「大幅加碼」"):
    from autora.domains.newsroom.workflow import start_article_revision

    async with committed() as session:
        run = await start_article_revision(
            session, policy=room.runtime.policy, workflows=room.runtime.workflows,
            company_id=room.company.id, article_id=article.id, actor=OPERATOR, reason=reason,
        )  # fmt: skip
        await session.commit()
    return run


async def _revision_approval(committed, run):
    async with committed() as session:
        approves = (
            await session.scalars(
                select(Task)
                .where(Task.workflow_run_id == run.id, Task.name == "approve")
                .order_by(Task.id)
            )
        ).all()
        return await session.scalar(select(Approval).where(Approval.task_id == approves[-1].id))


async def _site(committed, room):
    from autora.domains.newsroom.site import published_articles

    async with committed() as session:
        return await published_articles(session, "zh-TW", company_slug=room.company.slug)


async def test_a_published_article_is_changed_while_the_site_keeps_the_old_one(
    committed, e2e_settings
):
    room = await Newsroom().start(committed, e2e_settings)
    before = await _published(room, committed)
    [live] = await _site(committed, room)

    run = await _revise(room, committed, before)
    assert (await room.article()).state == "DRAFT"
    assert [a.title for a in await _site(committed, room)] == [live.title], "still up meanwhile"

    await room.worker.run_until_idle()
    async with committed() as session:
        tasks = (await session.scalars(select(Task).where(Task.workflow_run_id == run.id))).all()
    draft = next(t for t in tasks if t.name == "draft")
    assert draft.input["params"]["issues"] == [
        {"message": "發布後修改（人工）：把「狂加」改成「大幅加碼」"}
    ]
    approval = await _revision_approval(committed, run)
    assert approval is not None and approval.state == "PENDING"

    async with committed() as session:
        await room.runtime.approvals.decide(session, approval.id, outcome="approve", actor=OPERATOR)
        await session.commit()
    await room.worker.run_until_idle()

    after = await room.article()
    assert after.state == "PUBLISHED" and after.listed
    assert after.published_group_id != before.published_group_id, "the new version is up"
    assert after.published_at == before.published_at, "a correction keeps its first date"
    assert after.revised_at is not None and after.slug == before.slug
    [page] = await _site(committed, room)
    assert page.revised_at == after.revised_at
    assert [e.payload["revision"] for e in await room.events("ARTICLE_PUBLISHED")] == [False, True]


async def test_a_revision_turned_down_leaves_the_article_as_it_was(committed, e2e_settings):
    """Rejecting a revision must not drop the story, which would take the published one along."""
    room = await Newsroom().start(committed, e2e_settings)
    before = await _published(room, committed)
    run = await _revise(room, committed, before)
    await room.worker.run_until_idle()
    approval = await _revision_approval(committed, run)
    async with committed() as session:
        await room.runtime.approvals.decide(
            session, approval.id, outcome="reject", actor=OPERATOR, reason="原本的比較好"
        )
        await session.commit()
    await room.worker.run_until_idle()

    after = await room.article()
    assert after.state == "PUBLISHED" and after.listed
    assert after.current_draft_group_id == after.published_group_id == before.published_group_id
    assert (await room.get(Story, room.story.id)).state == "PUBLISHED"
    assert len(await _site(committed, room)) == 1
    [dropped] = await room.events("ARTICLE_REVISION_DROPPED")
    assert dropped.payload["reason"] == "原本的比較好"


async def test_one_taken_down_stays_down_while_changed_and_goes_up_changed(committed, e2e_settings):
    from autora.domains.newsroom.publisher import unpublish_article

    room = await Newsroom().start(committed, e2e_settings)
    before = await _published(room, committed)
    async with committed() as session:
        await unpublish_article(
            session, company_id=room.company.id, article_id=before.id, actor=OPERATOR,
            reason="用字要改",
        )  # fmt: skip
        await session.commit()
    run = await _revise(room, committed, before)
    await room.worker.run_until_idle()
    assert await _site(committed, room) == [], "still down while it is changed"

    approval = await _revision_approval(committed, run)
    async with committed() as session:
        await room.runtime.approvals.decide(session, approval.id, outcome="approve", actor=OPERATOR)
        await session.commit()
    await room.worker.run_until_idle()
    assert (await room.article()).listed and len(await _site(committed, room)) == 1


async def test_only_a_published_article_is_revised(committed, e2e_settings):
    from autora.domains.newsroom.publisher import PublishError

    room = await Newsroom().start(committed, e2e_settings)
    await room.worker.run_until_idle()  # waiting at approval: IN_REVIEW, never published
    with pytest.raises(PublishError, match="only a published"):
        await _revise(room, committed, await room.article())
