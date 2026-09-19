"""T-511: the editor's decision on a draft — accept_draft and request_revision."""

import uuid

from sqlalchemy import select

from autora.app import build_policy_engine
from autora.db.models import EventRecord
from autora.domains.newsroom.models import Article, Claim, Story
from autora.domains.newsroom.publisher import approve_article
from autora.runtime.actor import Actor

ISSUE = {"message": "第二段請說明是誰統計的。", "kind": "missing_context", "lang": "en"}


async def _events(room, event_type):
    async with room.committed() as session:
        return (
            await session.scalars(
                select(EventRecord)
                .where(
                    EventRecord.company_id == room.company.id,
                    EventRecord.event_type == event_type,
                )
                .order_by(EventRecord.id)
            )
        ).all()


async def _article(room) -> Article:
    async with room.committed() as session:
        return await session.scalar(select(Article).where(Article.story_id == room.story.id))


async def _draft_and_check(room, claim_ids=None):
    drafted = await room.call("write_draft", room.draft(claim_ids or list(room.claims.values())))
    assert drafted.ok, drafted.message
    checked = await room.call("run_fact_check", {"article_id": drafted.output["article_id"]})
    assert checked.ok, checked.message
    return drafted.output, checked.output


async def test_accepting_a_checked_draft_sends_it_to_approval(newsroom_room):
    room = newsroom_room
    draft, report = await _draft_and_check(room)
    assert report["passed"]
    accept = {"article_id": draft["article_id"], "fact_check_report_id": report["report_id"]}
    accepted = await room.call("accept_draft", accept)
    assert accepted.ok, accepted.message
    assert accepted.output["verdict"] == "accept" and accepted.output["reused"] is False
    article = await _article(room)
    assert article.state == "IN_REVIEW"
    [reviewed] = await _events(room, "ARTICLE_REVIEWED")
    assert reviewed.payload == {
        "article_id": draft["article_id"],
        "version_id": draft["versions"]["zh-TW"],
        "verdict": "accept",
        "fact_check_passed": True,
        "by_role": "researcher",  # the room's run is a researcher's
    }
    assert reviewed.task_id is not None and reviewed.aggregate_id == article.id

    # a retried call: the same decision, nothing new
    again = await room.call("accept_draft", accept)
    assert again.ok and again.output["reused"] is True
    assert len(await _events(room, "ARTICLE_REVIEWED")) == 1
    # a different decision on the same draft is refused
    revise = await room.call(
        "request_revision", {"article_id": draft["article_id"], "issues": [ISSUE]}
    )
    assert not revise.ok and "already reviewed: accept" in revise.message

    # the accepted draft goes on to approval
    async with room.committed() as session:
        approved = await approve_article(
            session,
            policy=build_policy_engine(),
            company_id=room.company.id,
            article_id=article.id,
            actor=Actor.human("editor-in-chief"),
        )
        assert approved.state == "APPROVED"
        await session.commit()


async def test_accept_needs_the_latest_passed_fact_check(newsroom_room):
    room = newsroom_room
    drafted = await room.call("write_draft", room.draft(list(room.claims.values())))
    article_id = drafted.output["article_id"]
    no_check = await room.call(
        "accept_draft", {"article_id": article_id, "fact_check_report_id": str(uuid.uuid4())}
    )
    assert not no_check.ok and "run the fact-check on this draft first" in no_check.message

    first = await room.call("run_fact_check", {"article_id": article_id})
    second = await room.call("run_fact_check", {"article_id": article_id})
    stale = await room.call(
        "accept_draft",
        {"article_id": article_id, "fact_check_report_id": first.output["report_id"]},
    )
    assert not stale.ok and f"latest fact-check ({second.output['report_id']})" in stale.message

    # a claim without evidence fails the fact-check: the draft cannot be accepted
    async with room.committed() as session:
        bare = Claim(
            company_id=room.company.id,
            story_id=room.story.id,
            text="It cost NT$999 million.",
            claim_type="number",
        )
        session.add(bare)
        await session.commit()
    _, failed = await _draft_and_check(room, [*room.claims.values(), str(bare.id)])
    assert not failed["passed"]
    refused = await room.call(
        "accept_draft",
        {"article_id": article_id, "fact_check_report_id": failed["report_id"]},
    )
    assert not refused.ok and "did not pass: request a revision" in refused.message
    assert (await _article(room)).state == "DRAFT"


async def test_revisions_are_limited_then_the_story_is_dropped(newsroom_room):
    room = newsroom_room
    for revision in (1, 2):
        draft, _ = await _draft_and_check(room)
        asked = await room.call(
            "request_revision", {"article_id": draft["article_id"], "issues": [ISSUE, ISSUE]}
        )
        assert asked.ok, asked.message
        out = asked.output
        assert out["verdict"] == "revise" and out["revision"] == revision
        assert out["revisions_left"] == 2 - revision and not out["dropped"]
        assert out["issues"][0] == ISSUE
        article = await _article(room)
        assert article.state == "DRAFT" and article.revision_count == revision

    requested = await _events(room, "ARTICLE_REVISION_REQUESTED")
    assert [e.payload["revision"] for e in requested] == [1, 2]
    assert requested[0].payload["issues_count"] == 2
    assert requested[0].payload["by_role"] == "researcher"

    # a third revision: the article is rejected and the story dropped
    draft, _ = await _draft_and_check(room)
    third = await room.call(
        "request_revision", {"article_id": draft["article_id"], "issues": [ISSUE]}
    )
    assert third.ok, third.message
    assert third.output["dropped"] is True and "story dropped" in third.output["next"]
    article = await _article(room)
    assert article.state == "REJECTED" and article.revision_count == 2
    async with room.committed() as session:
        assert (await session.get(Story, room.story.id)).state == "DROPPED"
    [rejected] = await _events(room, "ARTICLE_REJECTED")
    assert rejected.payload["reason"] == "still not ready after 2 revisions"
    assert len(await _events(room, "STORY_DROPPED")) == 1
    assert len(await _events(room, "ARTICLE_REVISION_REQUESTED")) == 2
    # nothing more is written for the story
    late = await room.call("write_draft", room.draft(list(room.claims.values())))
    assert not late.ok and "DROPPED" in late.message


async def test_a_revision_needs_an_issue(newsroom_room):
    room = newsroom_room
    draft, _ = await _draft_and_check(room)
    empty = await room.call("request_revision", {"article_id": draft["article_id"], "issues": []})
    assert not empty.ok and empty.error_class == "InvalidToolArguments"
    other = await room.call(
        "request_revision", {"article_id": str(uuid.uuid4()), "issues": [ISSUE]}
    )
    assert not other.ok and "no article" in other.message
