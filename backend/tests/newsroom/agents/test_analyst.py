"""T-507: the analyst — claims the fact-check can pass, checked before they reach the writer."""

import uuid

from sqlalchemy import select

from autora.app import build_behaviors, build_worker
from autora.company.agents import hire_agent
from autora.db.models import Agent, EventRecord, Project, Task
from autora.domains.newsroom.agents import analyst
from autora.domains.newsroom.agents.analyst import (
    AnalysisNote,
    KeyNumber,
    analysis_context,
    claims_made_here,
    claims_would_pass,
)
from autora.domains.newsroom.factcheck import check_claim
from autora.domains.newsroom.models import Claim, Story
from autora.domains.newsroom.policy import trust_policy
from autora.domains.newsroom.tools.factcheck import claim_quotes
from autora.runtime.actor import Actor
from autora.runtime.behaviors import RunContext
from autora.runtime.task_manager import TaskManager
from tests.conftest import unique_company

SETUP = Actor.system("test")


async def research_then_analysis(committed, e2e_settings):
    async with committed() as session:
        company = await unique_company(session, "analysis")
        project = Project(
            company_id=company.id,
            name="newsroom",
            state="ACTIVE",
            kill_criteria={"max_cost_usd": 10},
        )
        session.add(project)
        await session.flush()
        for role, name in (("researcher", "Rae"), ("analyst", "Ana")):
            await hire_agent(
                session, company_id=company.id, role=role, display_name=name, actor=SETUP
            )
        story = Story(company_id=company.id, title="Lumen City microgrid", state="SELECTED")
        session.add(story)
        await session.flush()
        params = {"story_id": str(story.id)}
        research = await TaskManager().add_task(
            session, company_id=company.id, project_id=project.id, name="research",
            display_name="Research", required_role="researcher", input={"params": params},
        )  # fmt: skip
        await session.commit()
    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company.id})
    )
    await worker.run_until_idle()
    async with committed() as session:
        analysis = await TaskManager().add_task(
            session, company_id=company.id, project_id=project.id, name="analysis",
            display_name="Analysis", required_role="analyst", input={"params": params},
            depends_on=[research.id], ready=True,
        )  # fmt: skip
        await session.commit()
    await worker.run_until_idle()
    return company, story, research, analysis


async def test_an_analyst_run_makes_claims_that_would_pass(committed, e2e_settings):
    company, story, research, analysis = await research_then_analysis(committed, e2e_settings)
    async with committed() as session:
        done = await session.get(Task, analysis.id)
        assert done.state == "SUCCEEDED", done.last_error
        note = AnalysisNote.model_validate(done.output)
        claims = (await session.scalars(select(Claim).where(Claim.id.in_(note.claim_ids)))).all()
        min_trust, default = trust_policy({})
        quotes = await claim_quotes(session, [c.id for c in claims], default)
        created = (
            await session.scalars(
                select(EventRecord).where(
                    EventRecord.task_id == analysis.id, EventRecord.event_type == "CLAIM_CREATED"
                )
            )
        ).all()
        research_evidence = set((await session.get(Task, research.id)).output["evidence_ids"])
    assert note.story_id == story.id and len(claims) >= 3
    assert all(c.task_id == analysis.id and c.story_id == story.id for c in claims)
    for claim in claims:
        verdict = check_claim(
            str(claim.id), claim.claim_type, claim.text, quotes[claim.id], min_trust=min_trust
        )
        assert verdict.passed, (claim.text, verdict.problems)
    assert {e.payload["claim_id"] for e in created} >= {str(c.id) for c in claims}
    # the claims quote the researcher's evidence
    assert {str(q.evidence_id) for c in claims for q in quotes[c.id]} <= research_evidence


async def test_the_context_passes_the_research_on(committed, e2e_settings):
    company, story, research, analysis = await research_then_analysis(committed, e2e_settings)
    async with committed() as session:
        task = await session.get(Task, analysis.id)
        ctx = RunContext(
            company_id=company.id,
            project_id=task.project_id,
            task=task,
            agent=Agent(role="analyst"),
            run_id=uuid.uuid4(),
        )
        text = await analysis_context(session, ctx)
        research_note = (await session.get(Task, research.id)).output
    assert f"Story id: {story.id}" in text and "Evidence (from the researcher):" in text
    for evidence_id in research_note["evidence_ids"]:
        assert evidence_id in text
    assert "researcher's summary: 來源" in text and "Suggested angles:" in text
    assert "Record at least 3 claims." in text


async def test_the_validators(newsroom_room):
    """newsroom_room made two supported number claims in its run's task."""
    room = newsroom_room
    async with room.committed() as session:
        claims = (await session.scalars(select(Claim).where(Claim.story_id == room.story.id))).all()
        task = await session.get(Task, claims[0].task_id)
        task.input = {"params": {"story_id": str(room.story.id), "min_claims": 2}}
        ctx = RunContext(
            company_id=room.company.id,
            project_id=task.project_id,
            task=task,
            agent=Agent(role="analyst"),
            run_id=uuid.uuid4(),
        )
        ids = [c.id for c in claims]

        def note(claim_ids, **extra):
            return AnalysisNote(
                story_id=extra.pop("story", room.story.id),
                claim_ids=claim_ids,
                angle="以數據看成本",
                **extra,
            )

        assert await claims_made_here(session, ctx, note(ids)) == []
        assert (
            await claims_would_pass(
                session, ctx, note(ids, key_numbers=[KeyNumber(claim_id=ids[0], value="1,200")])
            )
            == []
        )

        invented = uuid.uuid4()
        assert any(
            "does not exist" in i
            for i in await claims_made_here(session, ctx, note([*ids, invented]))
        )
        assert any(
            "this task's story" in i
            for i in await claims_made_here(session, ctx, note(ids, story=uuid.uuid4()))
        )
        task.input = {"params": {"story_id": str(room.story.id), "min_claims": 3}}
        assert "at least 3 claims" in (await claims_made_here(session, ctx, note(ids)))[0]

        other_task = Task(id=uuid.uuid4(), company_id=room.company.id, input=task.input)
        elsewhere = RunContext(
            company_id=room.company.id,
            project_id=task.project_id,
            task=other_task,
            agent=Agent(role="analyst"),
            run_id=uuid.uuid4(),
        )
        assert all(
            "not made in this task" in i
            for i in await claims_made_here(session, elsewhere, note(ids))
        )

        # a claim the fact-check would reject: an unsupported number
        bare = Claim(
            company_id=room.company.id,
            story_id=room.story.id,
            text="It cost NT$999 million.",
            claim_type="number",
            task_id=task.id,
        )
        session.add(bare)
        await session.flush()
        [problem] = await claims_would_pass(session, ctx, note([*ids, bare.id]))
        assert f"claim {bare.id} would fail fact-check: no supporting quote" in problem
        wrong_kind = await claims_would_pass(
            session, ctx, note(ids, key_numbers=[KeyNumber(claim_id=invented, value="x")])
        )
        assert "is not listed" in wrong_kind[0]


def test_the_worker_knows_the_analyst():
    behavior = build_behaviors().resolve("analyst", "analysis")
    assert behavior is analyst.BEHAVIOR and behavior.output_model is AnalysisNote
    assert set(behavior.tools) == {
        "read_evidence",
        "search_evidence",
        "create_claim",
        "link_evidence",
        "list_claims",
    }
    assert (
        "who says what" in behavior.system_prompt and "never Simplified" in behavior.system_prompt
    )
