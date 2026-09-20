"""T-518: the newsroom in simulation mode (MODEL_PROVIDER=fake, TOOLS_PROFILE=fixture), end to end.

From the fixture feeds to a published, distributed article, through the real worker composition:
sources polled, items clustered into stories, a story selected and its workflow started, the five
agents working through the real tools, the editor's revision branch, a person approving in the
inbox, the publisher. Nothing leaves the machine; nothing but the model's decisions is scripted.
"""

import asyncio
import uuid

from sqlalchemy import func, select

from autora.app import (
    build_embedder,
    build_page_fetcher,
    build_runtime,
    build_search_provider,
    build_worker,
)
from autora.db.models import (
    Agent,
    AgentActivity,
    AgentRun,
    Approval,
    Budget,
    CompanyGoal,
    Cycle,
    EventRecord,
    ModelCall,
    Task,
    WorkflowRun,
)
from autora.domains.newsroom.demo import gather_stories, pick, seed_demo, start_demo_story
from autora.domains.newsroom.factcheck import check_claim
from autora.domains.newsroom.models import (
    Article,
    ArticleVersion,
    Claim,
    Distribution,
    Evidence,
    SourceItem,
    Story,
)
from autora.domains.newsroom.policy import trust_policy
from autora.domains.newsroom.settings import get_newsroom_settings
from autora.domains.newsroom.simulation import DEMO_ISSUE
from autora.domains.newsroom.sources import SourcePoller
from autora.domains.newsroom.stories import StoryDesk
from autora.domains.newsroom.tools.factcheck import claim_quotes
from autora.domains.newsroom.workflow import DISPLAY_NAMES
from autora.runtime.actor import Actor

OPERATOR = Actor.human("demo-operator")


async def _count(session, model, *where):
    return await session.scalar(select(func.count()).select_from(model).where(*where))


async def test_the_demo_newsroom_publishes_a_story_from_its_feeds(committed, e2e_settings):
    runtime = build_runtime(e2e_settings)
    poller = SourcePoller(
        fetcher=build_page_fetcher(e2e_settings), search=build_search_provider(e2e_settings)
    )
    desk = StoryDesk(
        build_embedder(e2e_settings), threshold=get_newsroom_settings().story_match_threshold
    )

    # seed (idempotent), poll and cluster, pick the microgrid story, start it with the revise branch
    slug = f"newsroom-demo-{uuid.uuid4().hex[:8]}"
    async with committed() as session:
        demo = await seed_demo(session, actor=OPERATOR, slug=slug)
        again = await seed_demo(session, actor=OPERATOR, slug=slug)
        assert again.company.id == demo.company.id and len(again.sources) == 2
        stories = await gather_stories(session, demo.company.id, poller=poller, desk=desk)
        story = pick(stories, "microgrid")
        assert story is not None
        run = await start_demo_story(
            session,
            policy=runtime.policy,
            workflows=runtime.workflows,
            desk=desk,
            story=story,
            project_id=demo.project.id,
            actor=OPERATOR,
            revise_first_review=True,
        )
        await session.commit()
        company_id = demo.company.id
        items = await _count(session, SourceItem, SourceItem.company_id == company_id)
    assert items >= 5 and len(stories) >= 2  # both feeds read; more than one story

    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company_id})
    )
    await worker.run_until_idle()

    # the line stops at the approval inbox, after one revision round
    async with committed() as session:
        tasks = (
            await session.scalars(
                select(Task).where(Task.workflow_run_id == run.id).order_by(Task.created_at)
            )
        ).all()
        names = [t.name for t in tasks]
        approval = await session.scalar(
            select(Approval).where(Approval.company_id == company_id, Approval.state == "PENDING")
        )
    assert names.count("draft") == 2 and names.count("review") == 2
    reviews = [t for t in tasks if t.name == "review"]
    assert reviews[0].output["verdict"] == "revise"
    assert reviews[0].output["issues"] == [DEMO_ISSUE]
    assert reviews[1].output["verdict"] == "accept"
    assert approval is not None and approval.action == "approve_article"

    async with committed() as session:
        await runtime.approvals.decide(
            session, approval.id, outcome="approve", actor=OPERATOR, reason="demo"
        )
        await session.commit()
    await worker.run_until_idle()

    async with committed() as session:
        workflow = await session.get(WorkflowRun, run.id)
        story = await session.get(Story, story.id)
        article = await session.scalar(select(Article).where(Article.story_id == story.id))
        v2 = (
            await session.scalars(
                select(ArticleVersion).where(
                    ArticleVersion.draft_group_id == article.published_group_id
                )
            )
        ).all()
        claims = (await session.scalars(select(Claim).where(Claim.story_id == story.id))).all()
        min_trust, default = trust_policy({})
        quotes = await claim_quotes(session, [c.id for c in claims], default)
        evidence = await _count(session, Evidence, Evidence.company_id == company_id)
        channels = (
            await session.scalars(
                select(Distribution.channel).where(Distribution.article_id == article.id)
            )
        ).all()
        runs = (
            await session.scalars(select(AgentRun).where(AgentRun.company_id == company_id))
        ).all()
        model_providers = set(
            (
                await session.scalars(
                    select(ModelCall.provider).where(ModelCall.company_id == company_id)
                )
            ).all()
        )
        tool_events = (
            await session.scalars(
                select(EventRecord).where(
                    EventRecord.company_id == company_id,
                    EventRecord.event_type.in_(["TOOL_CALLED", "TOOL_COMPLETED", "TOOL_FAILED"]),
                )
            )
        ).all()
        activities = {
            a.agent_id: a
            for a in await session.scalars(
                select(AgentActivity).where(AgentActivity.company_id == company_id)
            )
        }

    # published in both languages, from the revised draft, and distributed
    assert workflow.state == "SUCCEEDED"
    assert story.state == "PUBLISHED" and article.state == "PUBLISHED"
    assert {v.lang for v in v2} == {"zh-TW", "en"} and {v.version for v in v2} == {2}
    zh = next(v for v in v2 if v.lang == "zh-TW")
    assert zh.body[1]["text"].startswith("對居民來說最重要的是：")  # the editor's issue, fixed
    assert DEMO_ISSUE["message"] in zh.change_summary
    assert sorted(channels) == ["site", "social_draft"]
    # the evidence chain: every claim cited passes the fact-check rules
    cited = {c for v in v2 for c in v.claim_ids}
    assert evidence >= 2 and len(cited) >= 3
    for claim in claims:
        if claim.id in cited:
            verdict = check_claim(
                str(claim.id), claim.claim_type, claim.text, quotes[claim.id], min_trust=min_trust
            )
            assert verdict.passed, (claim.text, verdict.problems)
    # every agent ran, only the simulated model answered, every tool call finished
    assert {r.state for r in runs} == {"COMPLETED"}
    assert len(runs) == 7  # research, analysis, 2 drafts, 2 reviews, distribute
    assert model_providers == {"fake"}
    called = {e.payload["tool_call_id"] for e in tool_events if e.event_type == "TOOL_CALLED"}
    finished = {e.payload["tool_call_id"] for e in tool_events if e.event_type != "TOOL_CALLED"}
    assert called and called == finished
    # the desk's agents, plus the company's CEO: this demo is a company with a newsroom in it,
    # not a newsroom pretending to be a company (T-605a/b)
    assert len(activities) == len(DISPLAY_NAMES) + 1
    # the ones that did the line's work carry their links; the planners' runs are their own
    working = [a for a in activities.values() if a.detail.get("links")]
    assert len(working) >= len(DISPLAY_NAMES) - 1


async def test_an_editor_that_never_decides_fails_visibly(committed, e2e_settings):
    """AC-9: when a step cannot be done, a person sees it — the agent shows FAILED with the
    reason, its task fails for good, and everything downstream is cancelled."""
    settings = e2e_settings.model_copy(update={"task_retry_base_seconds": 0.1})  # no long waits
    runtime = build_runtime(settings)
    poller = SourcePoller(
        fetcher=build_page_fetcher(settings), search=build_search_provider(settings)
    )
    desk = StoryDesk(
        build_embedder(settings), threshold=get_newsroom_settings().story_match_threshold
    )
    async with committed() as session:
        demo = await seed_demo(
            session, actor=OPERATOR, slug=f"newsroom-fail-{uuid.uuid4().hex[:8]}"
        )
        stories = await gather_stories(session, demo.company.id, poller=poller, desk=desk)
        story = pick(stories, "microgrid")
        run = await start_demo_story(
            session,
            policy=runtime.policy,
            workflows=runtime.workflows,
            desk=desk,
            story=story,
            project_id=demo.project.id,
            actor=OPERATOR,
            editor_fails=True,
        )
        await session.commit()
        company_id = demo.company.id

    worker = build_worker(settings, session_factory=committed, company_ids=frozenset({company_id}))

    async def tasks_of_the_run() -> dict[str, Task]:
        async with committed() as session:
            return {
                t.name: t
                for t in await session.scalars(select(Task).where(Task.workflow_run_id == run.id))
            }

    # a failed attempt waits for its retry, so being idle once is not the end of the line
    async with asyncio.timeout(120):
        while True:
            await worker.run_until_idle()
            tasks = await tasks_of_the_run()
            if all(t.state in ("SUCCEEDED", "FAILED", "CANCELLED") for t in tasks.values()):
                break
            await asyncio.sleep(0.2)

    async with committed() as session:
        workflow = await session.get(WorkflowRun, run.id)
        editor = await session.scalar(
            select(Agent).where(Agent.company_id == company_id, Agent.role == "editor")
        )
        activity = await session.get(AgentActivity, editor.id)
        failed = (
            await session.scalars(
                select(EventRecord)
                .where(
                    EventRecord.task_id == tasks["review"].id,
                    EventRecord.event_type == "AGENT_RUN_FAILED",
                )
                .order_by(EventRecord.seq)  # "the last one is final" needs an order to be last
            )
        ).all()

    assert tasks["review"].state == "FAILED" and tasks["review"].attempt == 3  # every retry tried
    for name in ("approve", "publish", "distribute"):
        assert tasks[name].state == "CANCELLED", name
    assert workflow.state == "FAILED"
    # the office: the editor's lamp is red and the panel can say why (AC-9)
    assert activity.state == "FAILED"
    assert activity.detail["error_class"] == "EvaluationFailed"
    assert "nothing was decided" in activity.detail["message"]
    assert [e.payload["final"] for e in failed] == [False, False, True]


async def test_a_cycle_plans_and_commissions_without_anyone_pressing_anything(
    committed, e2e_settings
):
    """AC-11, in simulation: the company decides its own day.

    Nobody starts a workflow here. The cycle opens, the CEO sets a goal and a budget, the
    editor-in-chief picks stories off the desk and commissions them, and the line starts — each
    of them an ordinary agent run, claimed and worked by the real worker.
    """
    runtime = build_runtime(e2e_settings)
    poller = SourcePoller(
        fetcher=build_page_fetcher(e2e_settings), search=build_search_provider(e2e_settings)
    )
    desk = StoryDesk(
        build_embedder(e2e_settings), threshold=get_newsroom_settings().story_match_threshold
    )
    slug = f"newsroom-cycle-{uuid.uuid4().hex[:8]}"

    async with committed() as session:
        demo = await seed_demo(session, actor=OPERATOR, slug=slug)
        stories = await gather_stories(session, demo.company.id, poller=poller, desk=desk)
        assert stories, "the desk needs candidates to choose from"
        await session.commit()
        company_id = demo.company.id

    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company_id})
    )
    async with committed() as session:
        cycle = await runtime.cycles.start(session, company_id)
        await session.commit()
        cycle_id = cycle.id

    # the two planners' runs, then the work they commissioned
    async with asyncio.timeout(180):
        for _ in range(4):
            await worker.run_until_idle()
            async with committed() as session:
                await runtime.cycles.tick(session, company_id)
                await session.commit()

    async with committed() as session:
        planning = (
            await session.scalars(
                select(WorkflowRun)
                .where(WorkflowRun.cycle_id == cycle_id)
                .order_by(WorkflowRun.created_at)
            )
        ).all()
        templates = [run.template_name for run in planning]
        commissioned = (
            await session.scalars(
                select(Story).where(
                    Story.company_id == company_id,
                    Story.state.in_(["IN_PRODUCTION", "PUBLISHED"]),
                )
            )
        ).all()
        goals = await _count(session, CompanyGoal, CompanyGoal.company_id == company_id)
        budgets = await _count(session, Budget, Budget.company_id == company_id)
        cycle_now = await session.get(Cycle, cycle_id)

    # the company planned its own day: the CEO in PLANNING, then the desk once the budget was
    # set (the desk plans at the start of EXECUTING, which is the first moment it can know it)
    assert templates[0] == "company.cycle_plan_v1"
    assert "newsroom.editorial_plan_v1" in templates
    # and the desk put the line to work on what it chose
    assert commissioned, "the editor-in-chief commissioned nothing"
    assert "newsroom.story_to_article_v2" in templates
    assert goals >= 1 and budgets >= 1  # the CEO's own decisions, through the pipeline
    assert cycle_now.stage != "PLANNING"  # both planners answered; the stage moved on
