"""T-508: articles, bilingual drafts (write_draft / read_draft) and the language policy (D-002)."""

import itertools
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

import autora.domains.newsroom as newsroom
from autora.app import build_embedder, build_policy_engine
from autora.db.models import Company, EventRecord
from autora.db.repositories.companies import upsert_policy
from autora.domains.newsroom.advice import NO_ADVICE_KEY, advice_problems
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
    # a title in the language it claims: the draft rules refuse one that is not (D-002)
    return LanguageVersion(
        lang=lang,
        title=f"標題 {lang}" if lang.startswith("zh") else f"Title {lang}",
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
    assert ARTICLE_FSM.terminal_states() == {"REJECTED"}  # taken down can go back up (D-044)
    assert ARTICLE_FSM.can("PUBLISHED", "ARCHIVED") and ARTICLE_FSM.can("ARCHIVED", "PUBLISHED")


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


# --- no advice in the newsroom's own voice (D-035) ------------------------------------------


def _version(lang, title, blocks, summary=None):
    return SimpleNamespace(
        lang=lang,
        title=title,
        summary=summary,
        blocks=[SimpleNamespace(type="paragraph", text=t, claim_ids=c) for t, c in blocks],
    )


FACT, NUMBER, SAID, VIEW = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
TYPES = {FACT: "fact", NUMBER: "number", SAID: "attribution", VIEW: "opinion"}


def test_plain_reporting_passes_even_with_market_words():
    """「加碼」is what Berkshire did; a rule that refused it would refuse the news."""
    version = _version(
        "zh-TW",
        "波克夏上季加碼西方石油、減持蘋果",
        [("波克夏第二季加碼西方石油 1,200 萬股。", [NUMBER]), ("同季減持蘋果。", [FACT])],
    )
    assert advice_problems([version], TYPES) == []


@pytest.mark.parametrize(
    "text",
    ["台積電值得逢低布局。", "建議投資人買進輝達。", "輝達股價可望上漲。", "目標價 1,500 元。",
     "Nvidia is poised to rally.", "It is worth buying at these levels."],
)  # fmt: skip
def test_advice_or_a_forecast_nobody_said_is_refused(text):
    version = _version("zh-TW", "標題", [(text, [FACT])])
    [issue] = advice_problems([version], TYPES)
    assert "cites no one" in issue


def test_the_same_words_are_fine_as_somebody_elses():
    version = _version(
        "zh-TW",
        "摩根士丹利調升台積電目標價",
        [("摩根士丹利將目標價上調至 1,500 元，維持買進評等。", [SAID])],
    )
    assert advice_problems([version], TYPES) == []


def test_the_newsroom_never_has_a_view_not_even_when_quoting():
    version = _version("en", "Title", [("We think the analysts are right.", [SAID])])
    [issue] = advice_problems([version], TYPES)
    assert "own view" in issue


def test_titles_cite_nothing_so_advice_there_is_always_ours():
    advice = _version("zh-TW", "輝達值得買進", [("輝達公布財報。", [SAID])])
    forecast_alone = _version("zh-TW", "輝達可望再創高", [("輝達公布財報。", [FACT])])
    forecast_reported = _version(
        "zh-TW", "分析師：輝達可望再創高", [("高盛表示可望再創高。", [SAID])]
    )
    assert "own voice" in advice_problems([advice], TYPES)[0]
    assert "nobody in the article made" in advice_problems([forecast_alone], TYPES)[0]
    assert advice_problems([forecast_reported], TYPES) == []


@pytest.mark.parametrize(
    "text",
    ["輝達持股遭大砍，顯示其已獲利了結。", "逆勢加碼拼多多。", "相較於對台積電的青睞。",
     "杜肯米勒狂加亞馬遜。", "Berkshire is betting on Alphabet.", "A contrarian move."],
)  # fmt: skip
def test_guessing_at_a_motive_is_refused_unless_somebody_said_it(text):
    """D-042: all six came from the first real drafts or their English twins."""
    [issue] = advice_problems([_version("zh-TW", "標題", [(text, [FACT])])], TYPES)
    assert "motive" in issue
    assert advice_problems([_version("zh-TW", "標題", [(text, [SAID])])], TYPES) == []


def test_a_headline_that_guesses_at_a_motive_is_refused():
    version = _version("zh-TW", "H&H 逆勢加碼拼多多", [("H&H 加碼拼多多 5,273,800 股。", [NUMBER])])
    [issue] = advice_problems([version], TYPES)
    assert "title" in issue and "逆勢" in issue


def test_market_words_that_look_like_motives_are_not():
    """避險基金 is a hedge fund, 消費者信心指數 a statistic: only a reading of intent is refused."""
    plain = "避險基金本季申報持股，消費者信心指數下滑 2.1 點。"
    assert advice_problems([_version("zh-TW", "標題", [(plain, [NUMBER])])], TYPES) == []
    [issue] = advice_problems(
        [
            _version(
                "zh-TW", "標題", [("加碼 Alphabet，表明對該公司長期成長的堅定信心。", [NUMBER])]
            )
        ],
        TYPES,
    )  # the sentence gpt-oss-20b added on its own (D-039)
    assert "motive" in issue


def test_what_changed_is_never_a_motive():
    """The words for what a filing shows must pass, or no 13F story could be written."""
    plain = "減持輝達 7,563,100 股，出清台積電，新建倉阿里巴巴，加碼拼多多，大幅減持 Arm。"
    assert (
        advice_problems(
            [_version("zh-TW", "H&H 13F：減持輝達、出清台積電", [(plain, [NUMBER])])], TYPES
        )
        == []
    )


def test_an_opinion_claim_cannot_be_cited():
    version = _version("zh-TW", "標題", [("這是一個合理的第一步。", [VIEW])])
    assert "is an opinion" in advice_problems([version], TYPES)[0]


async def _no_advice(d):
    async with d["committed"]() as session:
        await upsert_policy(
            session, d["company"].id, NO_ADVICE_KEY, True, updated_by={"kind": "human", "id": "t"}
        )
        await session.commit()


async def test_with_the_policy_on_the_analyst_cannot_record_an_opinion(newsroom_desk):
    d = newsroom_desk
    args = {
        "story_id": str(d["story"].id),
        "text": "A sensible first step.",
        "claim_type": "opinion",
    }
    assert (await d["call"]("create_claim", args)).ok, "off by default: the demo newsroom may"
    await _no_advice(d)
    refused = await d["call"]("create_claim", args)
    assert not refused.ok and "attribution" in refused.message


async def test_with_the_policy_on_a_draft_that_advises_is_not_saved(newsroom_desk):
    d = newsroom_desk
    advising = draft(d["story"].id, d["claims"])
    advising["versions"][1]["blocks"][0]["text"] = "太陽能板與儲能電池，值得逢低布局。"
    assert (await d["call"]("write_draft", advising, step=801)).ok, "off by default"

    await _no_advice(d)
    advising["versions"][1]["blocks"][0]["text"] = "太陽能板與儲能電池，建議投資人買進。"
    refused = await d["call"]("write_draft", advising, step=802)
    assert not refused.ok and "cites no one" in refused.message
    assert (await d["call"]("write_draft", draft(d["story"].id, d["claims"]), step=803)).ok
