"""T-519: the newsroom line with a real model (whichever MODEL_PROVIDER is configured).

Run it **alone and with its own database**, because a pytest session recreates the test schema
and would wipe this one's data mid-run::

    DATABASE_URL=<dev url, database autora_smoke> \
        pytest backend -m integration -k newsroom_real

(the fixtures add the ``_test`` suffix themselves). The model is real: it reads the same
prompts, calls the same tools and must satisfy the same validators as in production. Everything
else stays offline and free — the tools use the fixture corpus (``TOOLS_PROFILE=fixture``), so no
search credits are spent and the pages are the fictional Lumen City ones.

What it proves, with the model deciding the content: the prompts and schemas work with a real
model, the validators pass on its output, the fact-check passes on its claims, and the line runs
from a selected story to a published, distributed article (a person approves, D-001).
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select

from autora.app import (
    build_embedder,
    build_page_fetcher,
    build_runtime,
    build_search_provider,
    build_worker,
)
from autora.db.models import Approval, ModelCall, Task, WorkflowRun
from autora.domains.newsroom.demo import gather_stories, pick, seed_demo, start_demo_story
from autora.domains.newsroom.factcheck import check_claim
from autora.domains.newsroom.models import Article, ArticleVersion, Claim, Distribution, Story
from autora.domains.newsroom.policy import trust_policy
from autora.domains.newsroom.site import published_article
from autora.domains.newsroom.sources import SourcePoller
from autora.domains.newsroom.stories import StoryDesk
from autora.domains.newsroom.tools.factcheck import claim_quotes
from autora.infra.settings import SettingsError, load_settings
from autora.runtime.actor import Actor

OPERATOR = Actor.human("smoke-operator")
MINUTES = float(os.environ.get("NEWSROOM_SMOKE_MINUTES", "45"))
"""The whole line, model calls included. A free endpoint can take minutes over one call (D-006),
and a step that fails on a timeout waits for its retry: give it hours with
``NEWSROOM_SMOKE_MINUTES``."""


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


async def test_the_newsroom_line_with_a_real_model(committed, db_settings, tmp_path, capsys):
    settings = load_settings().model_copy(
        update={
            "database_url": db_settings.database_url,
            "blob_store_dir": tmp_path / "blobs",
            "worker_id": "live-newsroom",
            "tools_profile": "fixture",  # the fictional corpus: no search credits, no real sites
            # the analyst's and the writer's prompts carry the evidence: a free endpoint can take
            # minutes over them (D-006). A timeout here is the provider's, not the newsroom's.
            "nvidia_timeout_seconds": 420.0,
        }
    )
    runtime = build_runtime(settings)

    # a newsroom of its own, its fixture sources read and clustered
    async with committed() as session:
        demo = await seed_demo(
            session, actor=OPERATOR, slug=f"newsroom-real-{uuid.uuid4().hex[:8]}"
        )
        stories = await gather_stories(
            session,
            demo.company.id,
            poller=SourcePoller(
                fetcher=build_page_fetcher(settings), search=build_search_provider(settings)
            ),
            desk=StoryDesk(build_embedder(settings), threshold=settings.story_match_threshold),
        )
        story = pick(stories, "microgrid")
        assert story is not None, [s.title for s in stories]
        run = await start_demo_story(
            session,
            policy=runtime.policy,
            workflows=runtime.workflows,
            desk=StoryDesk(build_embedder(settings)),
            story=story,
            project_id=demo.project.id,
            actor=OPERATOR,
        )
        await session.commit()
        company_id, story_id, title = demo.company.id, story.id, story.title

    worker = build_worker(settings, session_factory=committed, company_ids=frozenset({company_id}))

    async def work_until(done, what: str) -> None:
        """Keep the worker going until ``done``. A failed attempt waits for its retry (a slow
        provider times out now and then), so being idle is not the end of the line."""
        while not await done():
            await worker.run_until_idle()
            if await done():
                return
            async with committed() as session:
                tasks = (
                    await session.scalars(select(Task).where(Task.workflow_run_id == run.id))
                ).all()
            if not tasks:
                raise AssertionError(
                    "the workflow's tasks are gone: another pytest session reset this database "
                    "while the smoke was running (give it its own DATABASE_URL)"
                )
            if all(t.state in ("SUCCEEDED", "FAILED", "CANCELLED") for t in tasks):
                raise AssertionError(f"the line stopped before {what}: {_states(tasks)}")
            await asyncio.sleep(5)  # a retry's backoff

    async def waiting_for_a_person() -> Approval | None:
        async with committed() as session:
            return await session.scalar(
                select(Approval).where(
                    Approval.company_id == company_id, Approval.state == "PENDING"
                )
            )

    async def published() -> bool:
        async with committed() as session:
            article = await session.scalar(select(Article).where(Article.story_id == story_id))
        return article is not None and article.state == "PUBLISHED"

    async with asyncio.timeout(MINUTES * 60):
        await work_until(waiting_for_a_person, "the approval")  # research, analysis, draft, review
        approval = await waiting_for_a_person()
        async with committed() as session:
            await runtime.approvals.decide(
                session, approval.id, outcome="approve", actor=OPERATOR, reason="smoke"
            )
            await session.commit()
        await work_until(published, "publication")  # publish, distribute

    async with committed() as session:
        workflow = await session.get(WorkflowRun, run.id)
        tasks = (
            await session.scalars(
                select(Task).where(Task.workflow_run_id == run.id).order_by(Task.created_at)
            )
        ).all()
        article = await session.scalar(select(Article).where(Article.story_id == story_id))
        versions = (
            await session.scalars(
                select(ArticleVersion).where(
                    ArticleVersion.draft_group_id == article.published_group_id
                )
            )
        ).all()
        claims = (await session.scalars(select(Claim).where(Claim.story_id == story_id))).all()
        min_trust, default_trust = trust_policy({})
        quotes = await claim_quotes(session, [c.id for c in claims], default_trust)
        channels = sorted(
            (
                await session.scalars(
                    select(Distribution.channel).where(Distribution.article_id == article.id)
                )
            ).all()
        )
        calls = (
            await session.scalars(select(ModelCall).where(ModelCall.company_id == company_id))
        ).all()
        public = await published_article(session, "zh-TW", article.slug)
        story_row = await session.get(Story, story_id)

    with capsys.disabled():
        print(f"\nprovider={PROVIDER} story={title!r} workflow={workflow.state}")
        for task in tasks:
            print(f"  {task.name:10} {task.state:9} attempt={task.attempt} {_short(task.output)}")
        spend = sum((c.cost_usd or 0) for c in calls)
        tokens = sum((c.tokens_in or 0) + (c.tokens_out or 0) for c in calls)
        print(f"  {len(calls)} model calls, {tokens} tokens, US${spend:.4f}")
        print(f"  published: {article.slug} {article.published_langs}")
        for version in versions:
            print(f"  [{version.lang}] {version.title}")
            for block in version.body[:3]:
                print(f"      {block['type']}: {block['text'][:100]}")

    assert workflow.state == "SUCCEEDED", [(t.name, t.state) for t in tasks]
    assert all(c.provider == PROVIDER and c.status == "ok" for c in calls)
    # the writer's draft: both languages, the same claims, every claim cited is fact-checkable
    assert article.state == "PUBLISHED" and sorted(article.published_langs) == ["en", "zh-TW"]
    assert story_row.state == "PUBLISHED" and channels == ["site", "social_draft"]
    cited = {c for v in versions for c in v.claim_ids}
    assert len({frozenset(v.claim_ids) for v in versions}) == 1 and len(cited) >= 2
    for claim in claims:
        if claim.id in cited:
            verdict = check_claim(
                str(claim.id), claim.claim_type, claim.text, quotes[claim.id], min_trust=min_trust
            )
            assert verdict.passed, (claim.text, verdict.problems)
    # the public page: the text and the sources behind it
    assert public is not None and public.blocks and public.sources
    zh = next(v for v in versions if v.lang == "zh-TW")
    assert any("一" <= ch <= "鿿" for ch in zh.title + " ".join(b["text"] for b in zh.body))


def _states(tasks) -> list[tuple[str, str]]:
    return [(t.name, t.state) for t in tasks]


def _short(output: dict | None) -> str:
    if not output:
        return ""
    text = str(output)
    return text if len(text) <= 120 else f"{text[:117]}..."
