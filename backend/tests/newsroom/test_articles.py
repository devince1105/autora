"""T-508: articles, bilingual drafts (write_draft / read_draft) and the language policy (D-002)."""

import itertools
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

import autora.domains.newsroom as newsroom
from autora.app import build_embedder, build_policy_engine
from autora.db.models import Company, EventRecord
from autora.db.repositories.companies import upsert_policy
from autora.domains.newsroom.articles import (
    ARTICLE_FSM,
    Block,
    ClaimFacts,
    LanguageVersion,
    check_draft,
    slugify,
)
from autora.domains.newsroom.models import Article, ArticleVersion, Story
from autora.domains.newsroom.policy import LanguagePolicy, language_policy
from autora.domains.newsroom.tools import claims as claim_tools
from autora.domains.newsroom.tools import drafts as draft_tools
from autora.domains.newsroom.tools import evidence as evidence_tools
from autora.infra.blobstore import LocalFSBlobStore
from autora.infra.http import FixtureFetcher
from autora.runtime.actor import Actor
from autora.runtime.fsm import IllegalTransition
from autora.runtime.tools import ToolRegistry
from tests.conftest import running_agent_run, unique_company

FIXTURES = Path(newsroom.__file__).parent / "fixtures"
PILOT = "https://news.fixtures.autora.test/lumen-city-microgrid-pilot"
DEFAULT = LanguagePolicy(primary="zh-TW", langs=("zh-TW", "en"), require_all=True)

# --- the rules --------------------------------------------------------------------------------

STORY = uuid.uuid4()
C1, C2, C3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
FACTS = {C1: ClaimFacts(STORY, "UNVERIFIED"), C2: ClaimFacts(STORY, "VERIFIED")}


def version(lang, *claims, heading_claims=()):
    return LanguageVersion(
        lang=lang,
        title=f"Title {lang}",
        blocks=[
            Block(type="heading", text="Heading", claim_ids=list(heading_claims)),
            Block(type="paragraph", text="Paragraph", claim_ids=list(claims)),
        ],
    )


def check(versions, *, facts=FACTS, policy=DEFAULT, story_state="SELECTED", article_state=None):
    return check_draft(
        versions,
        story_id=STORY,
        story_state=story_state,
        article_state=article_state,
        policy=policy,
        claims=facts,
    )


def test_a_good_bilingual_draft_passes():
    assert check([version("zh-TW", C1, C2), version("en", C2, C1)]) == []


@pytest.mark.parametrize(
    ("versions", "message"),
    [
        ([version("zh-TW", C1)], "every language is required (require_all_langs): missing ['en']"),
        ([version("en", C1)], "the primary language zh-TW is required"),
        (
            [version("zh-TW", C1), version("en", C1), version("ja", C1)],
            "not in the company's policy",
        ),
        ([version("zh-TW", C1), version("zh-TW", C1), version("en", C1)], "each language once"),
        ([version("zh-TW"), version("en")], "zh-TW block 2 (paragraph): cite the claim(s)"),
        ([version("zh-TW", C1, heading_claims=[C1]), version("en", C1)], "headings cite no claims"),
        (
            [version("zh-TW", C1, C3), version("en", C1, C3)],
            f"claim {C3} is not one of this story's",
        ),
        (
            [version("zh-TW", C1, C2), version("en", C1)],
            f"zh-TW and en must cite the same claims: only in zh-TW: ['{C2}']",
        ),
    ],
)
def test_broken_drafts_say_why(versions, message):
    issues = check(versions)
    assert any(message in issue for issue in issues), issues


def test_every_problem_is_reported_at_once_and_state_matters():
    other_story = {C1: ClaimFacts(uuid.uuid4(), "UNVERIFIED"), C2: ClaimFacts(STORY, "REJECTED")}
    long_quote = LanguageVersion(
        lang="zh-TW", title="Quote", blocks=[Block(type="quote", text="x" * 501, claim_ids=[C2])]
    )
    issues = check(
        [long_quote, version("en", C1)],
        facts=other_story,
        story_state="DROPPED",
        article_state="IN_REVIEW",
    )
    joined = "\n".join(issues)
    for expected in (
        "the story is DROPPED",
        "the article is IN_REVIEW",
        "quotes are at most 500 characters",
        f"claim {C1} is not one of this story's claims",
        f"claim {C2} was rejected by fact-check",
        "must cite the same claims",
    ):
        assert expected in joined
    assert check([version("zh-TW", C1), version("en", C1)], article_state="DRAFT") == []


def test_language_policy():
    assert language_policy({}) == DEFAULT
    single = language_policy({"newsroom.require_all_langs": False})
    assert check([version("zh-TW", C1)], policy=single) == []
    english_first = language_policy({"newsroom.primary_lang": "en", "newsroom.langs": ["zh-TW"]})
    assert english_first == LanguagePolicy(primary="en", langs=("en", "zh-TW"), require_all=True)


def test_slugs_and_lifecycle():
    article_id = uuid.UUID("01a0b8aa-0000-7000-8000-00000000abcd")
    assert (
        slugify("Lumen City switches on its microgrid!", article_id)
        == "lumen-city-switches-on-its-microgrid-00abcd"
    )
    assert slugify("流明市微電網啟用", article_id) == "article-00abcd"
    assert ARTICLE_FSM.can("DRAFT", "IN_REVIEW") and ARTICLE_FSM.can("IN_REVIEW", "DRAFT")
    assert not ARTICLE_FSM.can("PUBLISHED", "DRAFT")
    assert ARTICLE_FSM.terminal_states() == {"ARCHIVED", "REJECTED"}


# --- the tools --------------------------------------------------------------------------------


@pytest.fixture
async def newsroom_desk(committed, tmp_path):
    async with committed() as session:
        run = await running_agent_run(session, "drafts")
        company = await session.get(Company, run.company_id)
        story = Story(company_id=company.id, title="Lumen City microgrid", state="SELECTED")
        session.add(story)
        await session.commit()
    registry = ToolRegistry(committed)
    fetcher = FixtureFetcher(FIXTURES, json.loads((FIXTURES / "routes.json").read_text()))
    evidence_tools.register(registry, fetcher, LocalFSBlobStore(tmp_path), build_embedder(None))
    claim_tools.register(registry)
    draft_tools.register(registry)
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

    evidence_id = (await call("fetch_url", {"url": PILOT})).output["evidence_id"]
    claims = []
    for text, quote in (
        ("The microgrid links 1,200 rooftop panels.", "links 1,200 rooftop solar panels"),
        ("The battery holds 4 MWh.", "a 4 MWh battery"),
    ):
        made = await call(
            "create_claim",
            {
                "story_id": str(story.id),
                "text": text,
                "claim_type": "number",
                "evidence": [{"evidence_id": evidence_id, "quote": quote}],
            },
        )
        claims.append(made.output["claim_id"])
    return {
        "company": company,
        "story": story,
        "call": call,
        "committed": committed,
        "claims": claims,
    }


def draft(story_id, claims, *, en_claims=None, zh_title="流明市首座社區微電網啟用"):
    en_claims = claims if en_claims is None else en_claims
    return {
        "story_id": str(story_id),
        "versions": [
            {
                "lang": "en",
                "title": "Lumen City switches on its first microgrid",
                "blocks": [
                    {"type": "heading", "text": "What was built"},
                    {
                        "type": "paragraph",
                        "text": "Solar panels and a battery.",
                        "claim_ids": en_claims,
                    },
                ],
            },
            {
                "lang": "zh-TW",
                "title": zh_title,
                "summary": "港區微電網啟用。",
                "blocks": [
                    {"type": "paragraph", "text": "太陽能板與儲能電池。", "claim_ids": claims}
                ],
            },
        ],
    }


async def test_write_a_bilingual_draft_then_a_revision(newsroom_desk):
    d = newsroom_desk
    first = await d["call"]("write_draft", draft(d["story"].id, d["claims"]))
    assert first.ok, first.message
    out = first.output
    assert out["version"] == 1 and out["state"] == "DRAFT" and out["reused"] is False
    assert out["slug"].startswith("lumen-city-switches-on-its-first-microgrid-")
    assert [p.type for p in first.produced] == ["article_version", "article_version"]

    async with d["committed"]() as session:
        article = await session.get(Article, uuid.UUID(out["article_id"]))
        rows = (
            await session.scalars(
                select(ArticleVersion)
                .where(ArticleVersion.article_id == article.id)
                .order_by(ArticleVersion.id)
            )
        ).all()
        [created] = (
            await session.scalars(
                select(EventRecord).where(
                    EventRecord.aggregate_id == article.id,
                    EventRecord.event_type == "ARTICLE_CREATED",
                )
            )
        ).all()
    zh, en = rows  # the primary language is written first
    assert (zh.lang, en.lang) == ("zh-TW", "en")
    assert article.title == "流明市首座社區微電網啟用" and article.primary_lang == "zh-TW"
    assert zh.draft_group_id == en.draft_group_id == article.current_draft_group_id
    assert en.translation_of_version_id == zh.id and zh.translation_of_version_id is None
    assert set(zh.claim_ids) == set(en.claim_ids) == {uuid.UUID(c) for c in d["claims"]}
    assert zh.body == [
        {"type": "paragraph", "text": "太陽能板與儲能電池。", "claim_ids": d["claims"]}
    ]
    assert created.payload["version_id"] == str(zh.id) and created.payload["langs"] == [
        "zh-TW",
        "en",
    ]

    revised = await d["call"](
        "write_draft",
        {
            **draft(d["story"].id, d["claims"], zh_title="流明市微電網啟用"),
            "change_summary": "clearer title",
        },
    )
    assert revised.output["version"] == 2 and revised.output["article_id"] == out["article_id"]
    async with d["committed"]() as session:
        article = await session.get(Article, uuid.UUID(out["article_id"]))
        assert article.title == "流明市微電網啟用"
        assert str(article.current_draft_group_id) == revised.output["draft_group_id"]
        assert (
            await session.scalar(
                select(func.count())
                .select_from(EventRecord)
                .where(
                    EventRecord.aggregate_id == article.id,
                    EventRecord.event_type == "ARTICLE_CREATED",
                )
            )
            == 1
        )  # created once

    read = await d["call"]("read_draft", {"story_id": str(d["story"].id)})
    assert read.ok and read.output["version"] == 2
    assert read.output["versions"]["en"]["blocks"][1]["claim_ids"] == d["claims"]
    assert read.output["versions"]["zh-TW"]["change_summary"] == "clearer title"
    assert {c["claim_id"] for c in read.output["claims"]} == set(d["claims"])
    older = await d["call"]("read_draft", {"article_id": out["article_id"], "version": 1})
    assert older.output["versions"]["zh-TW"]["title"] == "流明市首座社區微電網啟用"


async def test_a_retry_returns_the_same_draft_and_a_bad_one_writes_nothing(newsroom_desk):
    d = newsroom_desk
    args = draft(d["story"].id, d["claims"])
    first = await d["call"]("write_draft", args, step=900)
    again = await d["call"]("write_draft", args, step=900)
    assert again.output["reused"] is True and again.output["versions"] == first.output["versions"]

    bad = await d["call"](
        "write_draft", draft(d["story"].id, d["claims"], en_claims=d["claims"][:1])
    )
    assert not bad.ok and bad.error_class == "DraftError" and not bad.will_retry
    assert "the draft was not saved" in bad.message and "must cite the same claims" in bad.message
    async with d["committed"]() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ArticleVersion)
            .where(ArticleVersion.company_id == d["company"].id)
        )
    assert count == 2  # only the first draft


async def test_drafting_stops_once_the_editor_has_it(newsroom_desk):
    d = newsroom_desk
    first = await d["call"]("write_draft", draft(d["story"].id, d["claims"]))
    async with d["committed"]() as session:
        article = await session.get(Article, uuid.UUID(first.output["article_id"]))
        await ARTICLE_FSM.transition(session, article, "IN_REVIEW", actor=Actor.system("test"))
        await session.commit()
    blocked = await d["call"]("write_draft", draft(d["story"].id, d["claims"]))
    assert not blocked.ok and "the article is IN_REVIEW" in blocked.message
    with pytest.raises(IllegalTransition):
        ARTICLE_FSM.check(article, "PUBLISHED")


async def test_the_company_language_policy_applies(newsroom_desk):
    d = newsroom_desk
    async with d["committed"]() as session:
        await upsert_policy(
            session,
            d["company"].id,
            "newsroom.require_all_langs",
            False,
            updated_by={"kind": "human", "id": "t"},
        )
        await session.commit()
    only_chinese = draft(d["story"].id, d["claims"])
    only_chinese["versions"] = only_chinese["versions"][1:]
    result = await d["call"]("write_draft", only_chinese)
    assert result.ok and list(result.output["versions"]) == ["zh-TW"]


async def test_other_companies_and_permissions(newsroom_desk):
    d = newsroom_desk
    async with d["committed"]() as session:
        other = await unique_company(session, "stranger")
        await session.commit()
    stranger = await d["call"](
        "write_draft", draft(d["story"].id, d["claims"]), company_id=other.id
    )
    assert not stranger.ok and "no story" in stranger.message
    unread = await d["call"]("read_draft", {"story_id": str(d["story"].id)}, company_id=other.id)
    assert not unread.ok
    neither = await d["call"]("read_draft", {})
    assert not neither.ok and neither.error_class == "InvalidToolArguments"

    engine = build_policy_engine()
    agent = Actor.agent(uuid.uuid4())
    assert engine.decide(agent, "write_draft", role="writer").outcome == "allow"
    assert engine.decide(agent, "write_draft", role="analyst").outcome == "deny"
    assert engine.decide(agent, "read_draft", role="editor").outcome == "allow"
