"""T-505: locating quotes, and the claim tools (create_claim, link_evidence, list_claims)."""

import itertools
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

import autora.domains.newsroom as newsroom
from autora.app import build_embedder, build_policy_engine
from autora.db.models import Company, EventRecord
from autora.domains.newsroom.models import Claim, ClaimEvidence, Evidence, Story
from autora.domains.newsroom.quotes import MAX_QUOTE, check_length, locate
from autora.domains.newsroom.tools import claims as claim_tools
from autora.domains.newsroom.tools import evidence as evidence_tools
from autora.infra.blobstore import LocalFSBlobStore
from autora.infra.http import FixtureFetcher
from autora.runtime.actor import Actor
from autora.runtime.tools import ToolRegistry
from tests.conftest import running_agent_run, unique_company

FIXTURES = Path(newsroom.__file__).parent / "fixtures"
PILOT = "https://news.fixtures.autora.test/lumen-city-microgrid-pilot"
PRESS = "https://city.fixtures.autora.test/press/2026-09-14-microgrid"

# --- locating quotes --------------------------------------------------------------------------

TEXT = (
    "Lumen City switched on its first neighbourhood solar microgrid on Monday.\n\n"
    '"This is the first of five districts," said Mira Chen — the city\'s energy commissioner.'
)


def test_exact_and_typographic_matches_map_to_the_original():
    found = locate("first neighbourhood solar microgrid", TEXT)
    assert TEXT[found.start : found.end] == found.text == "first neighbourhood solar microgrid"

    # whitespace, curly quotes and dashes may differ; the stored text is the evidence's own
    loose = locate("microgrid on  Monday.  “This is the first", TEXT)
    assert loose.text == 'microgrid on Monday.\n\n"This is the first'
    dash = locate("said Mira Chen - the city's energy", TEXT)
    assert dash.text == "said Mira Chen — the city's energy"


@pytest.mark.parametrize(
    "wrong",
    [
        "first neighborhood solar microgrid",  # spelling
        "First neighbourhood solar microgrid",  # case
        "the first of six districts",  # a different number
        "solar microgrid on Tuesday",
    ],
)
def test_anything_but_typography_must_match(wrong):
    assert locate(wrong, TEXT) is None


def test_chinese_and_first_occurrence():
    text = "港區微電網串連 1,200 組屋頂太陽能板。港區微電網由能源處執行。"
    found = locate("港區微電網", text)
    assert (found.start, found.end) == (0, 5)
    assert locate("1,200 組屋頂\n太陽能板", text).text == "1,200 組屋頂太陽能板"
    assert (
        locate("港區 微電網　串連", text).text == "港區微電網串連"
    )  # spaces in Chinese are layout
    assert locate("   ", text) is None


def test_quote_length_limits():
    assert "too short" in check_length("ab")
    assert check_length("微電網啟") is None
    assert "too long" in check_length("x" * (MAX_QUOTE + 1))


# --- tools ------------------------------------------------------------------------------------


@pytest.fixture
async def desk(committed, tmp_path):
    async with committed() as session:
        run = await running_agent_run(session, "claims")  # an analyst's run would do the same
        company = await session.get(Company, run.company_id)
        story = Story(company_id=company.id, title="Lumen City microgrid")
        session.add(story)
        await session.commit()
    registry = ToolRegistry(committed)
    fetcher = FixtureFetcher(FIXTURES, json.loads((FIXTURES / "routes.json").read_text()))
    evidence_tools.register(registry, fetcher, LocalFSBlobStore(tmp_path), build_embedder(None))
    claim_tools.register(registry)
    steps = itertools.count(1)

    async def call(tool, args, *, company_id=None, step=None):
        return await registry.invoke(
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

    pilot = (await call("fetch_url", {"url": PILOT})).output["evidence_id"]
    press = (await call("fetch_url", {"url": PRESS})).output["evidence_id"]
    return {
        "company": company,
        "story": story,
        "call": call,
        "committed": committed,
        "pilot": pilot,
        "press": press,
    }


async def _count(committed, model, **where):
    async with committed() as session:
        query = select(func.count()).select_from(model)
        for column, value in where.items():
            query = query.where(getattr(model, column) == value)
        return await session.scalar(query)


async def test_a_claim_with_its_quotes(desk):
    result = await desk["call"](
        "create_claim",
        {
            "story_id": str(desk["story"].id),
            "text": "The microgrid can power about 3,000 homes for six hours in an outage.",
            "claim_type": "number",
            "evidence": [
                {
                    "evidence_id": desk["pilot"],
                    # different spacing than the page; stored as the page has it
                    "quote": "keep about 3,000 homes powered  for six hours",
                },
                {"evidence_id": desk["press"], "quote": "停電時可維持約 3,000 戶供電六小時"},
            ],
        },
    )
    assert result.ok, result.message
    out = result.output
    assert out["reused"] is False and "missing" not in out
    claim_id = uuid.UUID(out["claim_id"])
    assert [(p.type, p.id) for p in result.produced] == [("claim", claim_id)]
    en, zh = out["evidence"]
    assert en["quote"] == "keep about 3,000 homes powered for six hours"
    async with desk["committed"]() as session:
        page = await session.get(Evidence, uuid.UUID(desk["pilot"]))
        assert page.extracted_text[en["start"] : en["end"]] == en["quote"]
        claim = await session.get(Claim, claim_id)
        assert (claim.status, claim.claim_type, claim.story_id) == (
            "UNVERIFIED",
            "number",
            desk["story"].id,
        )
        [event] = (
            await session.scalars(
                select(EventRecord).where(
                    EventRecord.company_id == desk["company"].id,
                    EventRecord.event_type == "CLAIM_CREATED",
                )
            )
        ).all()
    assert event.payload["evidence_ids"] == [desk["pilot"], desk["press"]]
    assert event.payload["claim_type"] == "number" and event.run_id is not None
    assert zh["support_type"] == "supports"


async def test_a_retried_call_returns_the_same_claim(desk):
    args = {
        "story_id": str(desk["story"].id),
        "text": "Harbor District is the first of five districts.",
        "claim_type": "fact",
    }
    first = await desk["call"]("create_claim", args, step=500)
    again = await desk["call"]("create_claim", args, step=500)  # same call key
    assert again.output["claim_id"] == first.output["claim_id"] and again.output["reused"] is True
    assert await _count(desk["committed"], Claim, story_id=desk["story"].id) == 1
    assert first.output["missing"].startswith("a fact claim needs at least one supporting quote")


async def test_one_unfound_quote_writes_nothing(desk):
    result = await desk["call"](
        "create_claim",
        {
            "story_id": str(desk["story"].id),
            "text": "The battery holds 4 MWh.",
            "claim_type": "number",
            "evidence": [
                {"evidence_id": desk["pilot"], "quote": "a 4 MWh battery"},
                {"evidence_id": desk["pilot"], "quote": "a 5 MWh battery"},
            ],
        },
    )
    assert not result.ok and result.error_class == "ClaimError" and not result.will_retry
    assert "quote not found" in result.message and "Copy it exactly" in result.message
    assert await _count(desk["committed"], Claim, story_id=desk["story"].id) == 0
    assert await _count(desk["committed"], ClaimEvidence, company_id=desk["company"].id) == 0


async def test_limits_and_other_companies(desk):
    story = str(desk["story"].id)
    too_long = await desk["call"](
        "create_claim",
        {
            "story_id": story,
            "text": "Everything.",
            "claim_type": "fact",
            "evidence": [{"evidence_id": desk["pilot"], "quote": "x" * 600}],
        },
    )
    assert not too_long.ok and "too long" in too_long.message

    async with desk["committed"]() as session:
        other = await unique_company(session, "stranger")
        foreign_story = Story(company_id=other.id, title="theirs")
        session.add(foreign_story)
        await session.commit()
    foreign = await desk["call"](
        "create_claim",
        {"story_id": str(foreign_story.id), "text": "Not ours.", "claim_type": "fact"},
    )
    assert not foreign.ok and "no story" in foreign.message
    peeking = await desk["call"](
        "create_claim",
        {
            "story_id": str(foreign_story.id),
            "text": "Using their tool.",
            "claim_type": "fact",
            "evidence": [],
        },
        company_id=other.id,
    )
    assert peeking.ok  # their own story is fine for them...
    stolen = await desk["call"](
        "link_evidence",
        {
            "claim_id": peeking.output["claim_id"],
            "evidence_id": desk["pilot"],
            "quote": "a 4 MWh battery",
        },
        company_id=other.id,
    )
    assert not stolen.ok and "no evidence" in stolen.message  # ...our evidence is not


async def test_link_evidence_later_and_list_claims(desk):
    story = str(desk["story"].id)
    fact = await desk["call"](
        "create_claim",
        {"story_id": story, "text": "The city spent NT$420 million.", "claim_type": "number"},
    )
    claim_id = fact.output["claim_id"]
    assert "missing" in fact.output

    linked = await desk["call"](
        "link_evidence",
        {
            "claim_id": claim_id,
            "evidence_id": desk["pilot"],
            "quote": "The project cost NT$420 million to build",
        },
    )
    assert linked.ok and "missing" not in linked.output
    assert linked.produced[0].type == "claim_evidence"
    again = await desk["call"](
        "link_evidence",
        {
            "claim_id": claim_id,
            "evidence_id": desk["pilot"],
            "quote": "The project cost NT$420 million to build",
        },
    )
    assert again.ok and len(again.output["evidence"]) == 1  # the same link once
    context = await desk["call"](
        "link_evidence",
        {
            "claim_id": claim_id,
            "evidence_id": desk["press"],
            "quote": "本計畫總經費新台幣 4.2 億元",
            "support_type": "context",
        },
    )
    assert [e["support_type"] for e in context.output["evidence"]] == ["supports", "context"]

    opinion = await desk["call"](
        "create_claim",
        {"story_id": story, "text": "The pilot is a sensible first step.", "claim_type": "opinion"},
    )
    assert "missing" not in opinion.output

    listed = await desk["call"]("list_claims", {"story_id": story})
    assert listed.ok
    claims = listed.output["claims"]
    assert [c["claim_type"] for c in claims] == ["number", "opinion"]
    assert claims[0]["evidence"][0]["url"] == PILOT and claims[0]["status"] == "UNVERIFIED"


def test_who_may_use_the_claim_tools():
    engine = build_policy_engine()
    agent = Actor.agent(uuid.uuid4())

    def outcome(action, role):
        return engine.decide(agent, action, role=role).outcome

    assert outcome("create_claim", "analyst") == "allow"
    assert outcome("link_evidence", "analyst") == "allow"
    assert outcome("create_claim", "writer") == "deny"
    assert {
        outcome("list_claims", r) for r in ("analyst", "writer", "editor", "marketing", "ceo")
    } == {"allow"}
