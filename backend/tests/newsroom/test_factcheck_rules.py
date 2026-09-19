"""T-510: the deterministic fact-check rules and run_fact_check."""

import itertools
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

import autora.domains.newsroom as newsroom
from autora.app import build_embedder, build_policy_engine
from autora.db.models import Company, EventRecord
from autora.db.repositories.companies import upsert_policy
from autora.domains.newsroom.articles import ARTICLE_FSM
from autora.domains.newsroom.factcheck import QuoteFacts, check_claim, numbers
from autora.domains.newsroom.models import Article, Claim, FactCheckReport, Story
from autora.domains.newsroom.tools import register_tools
from autora.infra.blobstore import LocalFSBlobStore
from autora.infra.http import FixtureFetcher
from autora.infra.search.fixture import FixtureSearchProvider
from autora.runtime.actor import Actor
from autora.runtime.tools import ToolRegistry
from tests.conftest import running_agent_run, unique_company

FIXTURES = Path(newsroom.__file__).parent / "fixtures"
PILOT = "https://news.fixtures.autora.test/lumen-city-microgrid-pilot"
PRESS = "https://city.fixtures.autora.test/press/2026-09-14-microgrid"
COSTS = "https://analysis.fixtures.autora.test/microgrid-costs"
MIN = Decimal("0.4")

# --- numbers ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("NT$420 million", [420e6]),
        ("總經費新台幣 4.2 億元", [4.2e8]),
        ("約3,000戶供電六小時", [3000]),
        ("1,200 rooftop panels and a 4 MWh battery", [1200, 4]),
        ("prices rose 18% this quarter", [18]),
        ("NT$4.6 billion over ten years", [4.6e9]),
        ("六小時", []),
    ],
)
def test_numbers_with_their_scale(text, values):
    assert numbers(text) == pytest.approx(values)


# --- the rules --------------------------------------------------------------------------------


def q(quote, support="supports", *, trust="0.9", source="source:a", intact=True, evidence="e1"):
    return QuoteFacts(
        evidence_id=evidence,
        quote=quote,
        support_type=support,
        intact=intact,
        trust=Decimal(trust),
        source_key=source,
    )


def verdict(claim_type, text, quotes):
    return check_claim("c", claim_type, text, quotes, min_trust=MIN)


def test_supported_claims_pass():
    assert verdict(
        "fact", "The pilot is in Harbor District.", [q("The pilot in Harbor District")]
    ).passed
    assert verdict(
        "number", "The project cost NT$420 million.", [q("總經費新台幣 4.2 億元")]
    ).passed
    assert verdict("opinion", "It is a sensible first step.", []).passed


@pytest.mark.parametrize(
    ("claim_type", "text", "quotes", "problem"),
    [
        ("fact", "Anything.", [], "no supporting quote"),
        ("quote", "She said so.", [q("context only", "context")], "no supporting quote"),
        ("fact", "A thing.", [q("a thing", trust="0.2")], "only low-trust support"),
        (
            "fact",
            "A thing.",
            [q("a thing", trust="0.2"), q("a thing again", trust="0.3", evidence="e2")],
            "only low-trust support",  # the same source twice is not independent
        ),
        ("number", "3,500 homes stay powered.", [q("about 3,000 homes")], "the number 3500"),
        (
            "fact",
            "A thing.",
            [q("a thing"), q("not so", "contradicts", evidence="e9")],
            "contradicted by evidence ['e9']",
        ),
        ("fact", "A thing.", [q("a thing", intact=False)], "no longer matches evidence e1"),
        ("attribution", "The city says so.", [], "an attribution needs a quote"),
    ],
)
def test_failing_claims_say_why(claim_type, text, quotes, problem):
    result = verdict(claim_type, text, quotes)
    assert not result.passed
    assert any(problem in p for p in result.problems), result.problems


def test_independent_low_trust_sources_corroborate_and_attributions_carry_disputes():
    two = [
        q("a thing", trust="0.2"),
        q("a thing", trust="0.3", source="site:other.test", evidence="e2"),
    ]
    assert verdict("fact", "A thing.", two).passed
    disputed = verdict(
        "attribution",
        "An independent review puts the cost at NT$4.6 billion.",
        [q("NT$4.6 billion over ten years"), q("NT$420 million", "contradicts", evidence="e2")],
    )
    assert disputed.passed and disputed.notes == [
        "contradicting evidence is reported with the attribution"
    ]


# --- run_fact_check ---------------------------------------------------------------------------


@pytest.fixture
async def room(committed, tmp_path):
    async with committed() as session:
        run = await running_agent_run(session, "factcheck")
        company = await session.get(Company, run.company_id)
        story = Story(company_id=company.id, title="Lumen City microgrid", state="SELECTED")
        session.add(story)
        await session.commit()
    registry = ToolRegistry(committed)
    register_tools(
        registry,
        search_provider=FixtureSearchProvider([]),
        fetcher=FixtureFetcher(FIXTURES, json.loads((FIXTURES / "routes.json").read_text())),
        blobs=LocalFSBlobStore(tmp_path),
        embedder=build_embedder(None),
    )
    steps = itertools.count(1)

    async def call(tool, args, *, company_id=None, step=None):
        result = await registry.invoke(
            tool,
            args,
            company_id=company_id or company.id,
            actor=Actor.system("test"),
            tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
            run_id=run.id,
            task_id=run.task_id,
            agent_id=run.agent_id,
            step_seq=step if step is not None else next(steps),
        )
        return result

    pages = {
        url: (await call("fetch_url", {"url": url})).output["evidence_id"]
        for url in (PILOT, PRESS, COSTS)
    }
    specs = {
        "panels": (
            "number",
            "The microgrid links 1,200 rooftop panels.",
            [(PILOT, "links 1,200 rooftop solar panels", "supports")],
        ),
        "cost": (
            "number",
            "The city spent NT$420 million building it.",
            [(PRESS, "本計畫總經費新台幣 4.2 億元", "supports")],
        ),
        "cost_settled": (
            "fact",
            "The microgrid's full cost is settled.",
            [
                (PILOT, "The project cost NT$420 million to build", "supports"),
                (COSTS, "puts its cost at NT$4.6 billion over ten years", "contradicts"),
            ],
        ),
        "review": (
            "attribution",
            "An independent review puts the ten-year cost at NT$4.6 billion.",
            [(COSTS, "puts its cost at NT$4.6 billion over ten years", "supports")],
        ),
        "view": ("opinion", "Starting with one district is sensible.", []),
    }
    claims = {}
    for name, (claim_type, text, refs) in specs.items():
        made = await call(
            "create_claim",
            {
                "story_id": str(story.id),
                "text": text,
                "claim_type": claim_type,
                "evidence": [
                    {"evidence_id": pages[u], "quote": quote, "support_type": s}
                    for u, quote, s in refs
                ],
            },
        )
        assert made.ok, made.message
        claims[name] = made.output["claim_id"]
    return {
        "company": company,
        "story": story,
        "call": call,
        "committed": committed,
        "claims": claims,
    }


def draft(story_id, claim_ids):
    return {
        "story_id": str(story_id),
        "versions": [
            {
                "lang": lang,
                "title": title,
                "blocks": [
                    {"type": "paragraph", "text": f"{lang} sentence {i}", "claim_ids": [c]}
                    for i, c in enumerate(claim_ids)
                ],
            }
            for lang, title in (("zh-TW", "流明市微電網"), ("en", "Lumen City microgrid"))
        ],
    }


async def _events(committed, company_id, event_type):
    async with committed() as session:
        return (
            await session.scalars(
                select(EventRecord)
                .where(EventRecord.company_id == company_id, EventRecord.event_type == event_type)
                .order_by(EventRecord.seq)
            )
        ).all()


async def test_a_draft_with_a_contested_claim_fails_then_passes_without_it(room):
    r = room
    c = r["claims"]
    written = await r["call"]("write_draft", draft(r["story"].id, list(c.values())))
    assert written.ok, written.message
    article_id = written.output["article_id"]

    report = await r["call"]("run_fact_check", {"article_id": article_id}, step=700)
    assert report.ok, report.message
    out = report.output
    assert out["passed"] is False and (out["checked"], out["failed"]) == (5, 1)
    by_claim = {res["claim_id"]: res for res in out["results"]}
    assert by_claim[c["cost_settled"]]["verdict"] == "fail"
    assert (
        "write the contested point as who said what" in by_claim[c["cost_settled"]]["problems"][0]
    )
    assert all(by_claim[c[n]]["verdict"] == "pass" for n in ("panels", "cost", "review", "view"))
    assert by_claim[c["review"]]["notes"] == []  # supported attribution, no dispute attached to it

    # layer 3 for the editor: passing, non-opinion claims with quotes and each language's sentence
    review = {item["claim_id"]: item for item in out["semantic_review"]}
    assert set(review) == {c["panels"], c["cost"], c["review"]}
    assert review[c["cost"]]["quotes"][0]["quote"] == "本計畫總經費新台幣 4.2 億元"
    assert set(review[c["panels"]]["sentences"]) == {"zh-TW", "en"}
    assert "Layer 3 is yours" in out["note"]
    assert report.produced[0].type == "fact_check_report"

    async with r["committed"]() as session:
        statuses = {
            str(x.id): x.status
            for x in (
                await session.scalars(select(Claim).where(Claim.story_id == r["story"].id))
            ).all()
        }
    assert statuses[c["cost_settled"]] == "REJECTED"
    assert {statuses[c[n]] for n in ("panels", "cost", "review", "view")} == {"VERIFIED"}
    rejected = await _events(r["committed"], r["company"].id, "CLAIM_REJECTED")
    assert [e.payload["claim_id"] for e in rejected] == [c["cost_settled"]]
    assert len(await _events(r["committed"], r["company"].id, "CLAIM_VERIFIED")) == 4

    # a retried call returns the same report; a new run changes no status, so no new events
    again = await r["call"]("run_fact_check", {"article_id": article_id}, step=700)
    assert again.output["report_id"] == out["report_id"] and again.output["reused"] is True
    await r["call"]("run_fact_check", {"article_id": article_id})
    assert len(await _events(r["committed"], r["company"].id, "CLAIM_VERIFIED")) == 4

    # the revision drops the contested claim (it can no longer be cited) and passes
    kept = [c[n] for n in ("panels", "cost", "review", "view")]
    still_cited = await r["call"]("write_draft", draft(r["story"].id, list(c.values())))
    assert not still_cited.ok and "rejected by fact-check" in still_cited.message
    revised = await r["call"]("write_draft", draft(r["story"].id, kept))
    assert revised.ok and revised.output["version"] == 2
    final = await r["call"]("run_fact_check", {"article_id": article_id})
    assert final.output["passed"] is True and final.output["checked"] == 4
    async with r["committed"]() as session:
        reports = await session.scalar(
            select(func.count())
            .select_from(FactCheckReport)
            .where(FactCheckReport.article_id == uuid.UUID(article_id))
        )
    assert reports == 3


async def test_low_trust_by_company_policy(room):
    r = room
    async with r["committed"]() as session:
        # nothing here was listed by a source: a page's own site vouches for it at this trust
        await upsert_policy(
            session,
            r["company"].id,
            "newsroom.default_source_trust",
            "0.2",
            updated_by={"kind": "human", "id": "t"},
        )
        await session.commit()
    written = await r["call"]("write_draft", draft(r["story"].id, [r["claims"]["panels"]]))
    result = await r["call"]("run_fact_check", {"article_id": written.output["article_id"]})
    [res] = result.output["results"]
    assert res["verdict"] == "fail" and "only low-trust support" in res["problems"][0]


async def test_only_drafts_of_your_company_and_only_editors(room):
    r = room
    written = await r["call"]("write_draft", draft(r["story"].id, [r["claims"]["panels"]]))
    article_id = written.output["article_id"]
    async with r["committed"]() as session:
        other = await unique_company(session, "stranger")
        await session.commit()
    stranger = await r["call"]("run_fact_check", {"article_id": article_id}, company_id=other.id)
    assert not stranger.ok and "no article" in stranger.message

    async with r["committed"]() as session:
        article = await session.get(Article, uuid.UUID(article_id))
        actor = Actor.system("test")
        for state in ("IN_REVIEW", "APPROVED"):
            await ARTICLE_FSM.transition(session, article, state, actor=actor)
        await session.commit()
    approved = await r["call"]("run_fact_check", {"article_id": article_id})
    assert not approved.ok and "only drafts are fact-checked" in approved.message

    engine = build_policy_engine()
    agent = Actor.agent(uuid.uuid4())
    assert engine.decide(agent, "run_fact_check", role="editor").outcome == "allow"
    assert engine.decide(agent, "run_fact_check", role="writer").outcome == "deny"
