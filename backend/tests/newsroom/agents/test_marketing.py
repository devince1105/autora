"""T-513: marketing — social copy for a published article, saved as a draft, reported as saved."""

import uuid

from sqlalchemy import select

from autora.app import build_behaviors
from autora.db.models import Agent, EventRecord, PolicyDecision, Task
from autora.domains.newsroom.agents import marketing
from autora.domains.newsroom.agents.marketing import (
    DistributionPlan,
    PlannedChannel,
    distribute_context,
    marketing_facts,
    plan_matches_the_records,
    site_and_social,
)
from autora.domains.newsroom.models import Article, Distribution
from autora.runtime.behaviors import RunContext
from autora.runtime.models.providers.fake import FakeToolUse, FakeTurn
from tests.newsroom.agents.conftest import add_task, run_line
from tests.newsroom.conftest import approve_and_publish


async def _article(committed, story_id) -> Article:
    async with committed() as session:
        return await session.scalar(select(Article).where(Article.story_id == story_id))


async def test_a_marketing_run_drafts_the_social_posts(committed, e2e_settings):
    line = await run_line(committed, e2e_settings, until="review")
    article = await _article(committed, line.story.id)
    published = await approve_and_publish(committed, line.company.id, article.id)
    task = await add_task(committed, line, "distribute", depends_on=[line.tasks["review"].id])
    await line.worker.run_until_idle()
    async with committed() as session:
        done = await session.get(Task, task.id)
        assert done.state == "SUCCEEDED", done.state
        plan = DistributionPlan.model_validate(done.output)
        rows = {
            r.channel: r
            for r in await session.scalars(
                select(Distribution).where(Distribution.article_id == article.id)
            )
        }
        created = await session.scalar(
            select(EventRecord).where(
                EventRecord.task_id == task.id, EventRecord.event_type == "DISTRIBUTION_CREATED"
            )
        )
    assert plan.article_id == article.id
    site, social = plan.channels
    assert site.distribution_id == published.distribution_id == rows["site"].id
    assert social.distribution_id == rows["social_draft"].id
    assert rows["social_draft"].status == "draft"
    assert set(social.posts) == {"zh-TW", "en"} and social.posts["zh-TW"].startswith("新報導")
    assert rows["social_draft"].copy["en"]["url"] == published.urls["en"]
    assert created.payload["channel"] == "social_draft"


async def test_an_unpublished_article_is_refused_by_the_policy(committed, e2e_settings):
    line = await run_line(committed, e2e_settings, until="review")  # accepted, not published
    article = await _article(committed, line.story.id)
    fake = line.worker.runner.gateway.providers["fake"]
    posts = {"zh-TW": "搶先看。", "en": "Early look."}
    use = FakeToolUse(
        name="create_distribution", input={"article_id": str(article.id), "posts": posts}
    )
    fake.script("marketing", "distribute", 1, FakeTurn(text="Posting.", tool_uses=[use]))
    task = await add_task(committed, line, "distribute", depends_on=[line.tasks["review"].id])
    await line.worker.run_until_idle()
    async with committed() as session:
        decision = await session.scalar(
            select(PolicyDecision).where(
                PolicyDecision.task_id == task.id, PolicyDecision.action == "create_distribution"
            )
        )
        done = await session.get(Task, task.id)
        social = await session.scalar(
            select(Distribution).where(
                Distribution.article_id == article.id, Distribution.channel == "social_draft"
            )
        )
    assert decision.outcome == "deny" and "article is IN_REVIEW, not PUBLISHED" in decision.reason
    assert decision.role == "marketing"
    assert done.state not in ("SUCCEEDED", "RUNNING")
    assert social is None  # the tool never ran


def _ctx(company_id, story_id, task=None) -> RunContext:
    task = task or Task(
        id=uuid.uuid4(), company_id=company_id, input={"params": {"story_id": str(story_id)}}
    )
    return RunContext(
        company_id=company_id,
        project_id=task.project_id,
        task=task,
        agent=Agent(role="marketing"),
        run_id=uuid.uuid4(),
    )


async def test_the_context_and_the_policy_facts(newsroom_room):
    room = newsroom_room
    ctx = _ctx(room.company.id, room.story.id)
    async with room.committed() as session:
        assert "not written, not published" in await distribute_context(session, ctx)
    article_id = await room.publish()
    async with room.committed() as session:
        article = await session.get(Article, uuid.UUID(article_id))
        site = await session.scalar(
            select(Distribution).where(Distribution.article_id == article.id)
        )
        text = await distribute_context(session, ctx)
        facts = await marketing_facts(
            session, ctx, "create_distribution", {"article_id": article_id}
        )
        assert await marketing_facts(session, ctx, "read_draft", {}) == {}
        assert await marketing_facts(session, ctx, "create_distribution", {"article_id": "x"}) == {}
    for expected in (
        f"Article id: {article.id}",
        "Published in: zh-TW, en",
        f"Site distribution id: {site.id}",
        "[zh-TW] Title: 流明市首座社區微電網啟用",
        f"  Page: /news/en/articles/{article.slug}",
        "  Opening: zh-TW paragraph 0",
    ):
        assert expected in text
    assert facts == {"article_state": "PUBLISHED"}


async def test_the_validators_hold_the_plan_to_the_records(newsroom_room):
    room = newsroom_room
    article_id = await room.publish()
    posts = {"zh-TW": "流明市微電網啟用。", "en": "Lumen City's microgrid is on."}
    made = await room.call("create_distribution", {"article_id": article_id, "posts": posts})
    assert made.ok, made.message
    async with room.committed() as session:
        site = await session.scalar(
            select(Distribution).where(
                Distribution.article_id == uuid.UUID(article_id), Distribution.channel == "site"
            )
        )
        run_task = await session.scalar(
            select(Task).join(EventRecord, EventRecord.task_id == Task.id).where(
                EventRecord.company_id == room.company.id
            ).limit(1)
        )  # fmt: skip
        run_task.input = {"params": {"story_id": str(room.story.id)}}
        ctx = _ctx(room.company.id, room.story.id, task=run_task)
        social_id = uuid.UUID(made.output["distribution_id"])

        def plan(*channels, article=article_id):
            return DistributionPlan(article_id=article, channels=list(channels))

        good = plan(
            PlannedChannel(channel="site", distribution_id=site.id),
            PlannedChannel(channel="social_draft", distribution_id=social_id, posts=posts),
        )
        for check in (plan_matches_the_records, site_and_social):
            assert await check(session, ctx, good) == []

        assert (
            "this task's story's article"
            in (
                await plan_matches_the_records(
                    session, ctx, plan(*good.channels, article=uuid.uuid4())
                )
            )[0]
        )
        swapped = plan(
            PlannedChannel(channel="social_draft", distribution_id=site.id),
            PlannedChannel(channel="site", distribution_id=social_id),
        )
        assert any(
            "is the site one, not social_draft" in i
            for i in await plan_matches_the_records(session, ctx, swapped)
        )
        invented = plan(PlannedChannel(channel="social_draft", distribution_id=uuid.uuid4()))
        assert (
            "is not one of this article's"
            in (await plan_matches_the_records(session, ctx, invented))[0]
        )
        reworded = plan(
            PlannedChannel(
                channel="social_draft", distribution_id=social_id, posts=posts | {"en": "Other"}
            )
        )
        assert (
            "report the posts you saved"
            in (await plan_matches_the_records(session, ctx, reworded))[0]
        )
        elsewhere = _ctx(
            room.company.id,
            room.story.id,
            task=Task(id=uuid.uuid4(), company_id=room.company.id, input=run_task.input),
        )
        assert (
            "was not created in this task"
            in (await plan_matches_the_records(session, elsewhere, good))[0]
        )
        only_site = plan(PlannedChannel(channel="site", distribution_id=site.id))
        assert "write the social post once" in (await site_and_social(session, ctx, only_site))[0]


def test_the_worker_knows_marketing():
    behavior = build_behaviors().resolve("marketing", "distribute")
    assert behavior is marketing.BEHAVIOR and behavior.output_model is DistributionPlan
    assert behavior.tools == ("create_distribution", "read_draft")
    assert behavior.policy_facts is marketing_facts
    assert "saved as a draft" in behavior.system_prompt
    assert "never Simplified" in behavior.system_prompt
