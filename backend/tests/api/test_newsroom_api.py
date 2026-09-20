"""T-517: the newsroom's admin API — stories, articles, sources, starting a story, timelines."""

import uuid

from sqlalchemy import select

from autora.db.models import Project, Schedule
from autora.domains.newsroom.models import Story


async def test_stories_list_and_detail(api, newsroom_room):
    room = newsroom_room
    listed = await api.get(f"/api/companies/{room.company.id}/stories")
    assert listed.status_code == 200, listed.text
    [story] = listed.json()
    assert story["id"] == str(room.story.id) and story["claims"] == 2 and story["evidence"] == 2
    assert story["article"] is None
    assert (await api.get(f"/api/companies/{room.company.id}/stories?state=DROPPED")).json() == []

    detail = (await api.get(f"/api/stories/{room.story.id}")).json()
    assert detail["title"] == "Lumen City microgrid"
    assert {e["id"] for e in detail["evidence_list"]} == set(room.evidence.values())
    claims = {c["id"]: c for c in detail["claim_list"]}
    panels = claims[room.claims["panels"]]
    assert panels["claim_type"] == "number" and panels["status"] == "UNVERIFIED"
    [quote] = panels["quotes"]
    # the quote is shown in its place in the evidence text (AC-7)
    assert quote["quote"] == "links 1,200 rooftop solar panels"
    assert quote["before"].endswith(" ") and quote["after"]
    assert quote["url"].startswith("https://news.fixtures.autora.test/")
    assert (await api.get(f"/api/stories/{uuid.uuid4()}")).status_code == 404


async def test_article_detail_with_versions_checks_and_distribution(api, newsroom_room):
    room = newsroom_room
    article_id = await room.publish()
    [summary] = (await api.get(f"/api/companies/{room.company.id}/articles")).json()
    assert summary["id"] == article_id and summary["state"] == "PUBLISHED"
    assert summary["version"] == 1 and sorted(summary["langs"]) == ["en", "zh-TW"]
    assert summary["views"] == 0

    detail = (await api.get(f"/api/articles/{article_id}")).json()
    assert detail["shown"] == 1 and set(detail["languages"]) == {"zh-TW", "en"}
    zh = detail["languages"]["zh-TW"]
    assert zh["title"] == "流明市首座社區微電網啟用"
    assert [b["claim_ids"] for b in zh["blocks"]] == [[c] for c in room.claims.values()]
    assert {c["id"] for c in detail["claims"]} == set(room.claims.values())
    assert all(c["status"] == "VERIFIED" for c in detail["claims"])
    [version] = detail["versions"]
    assert version["current"] and version["published"] and version["version"] == 1
    [check] = detail["fact_checks"]
    assert check["passed"] and check["version"] == 1 and check["checked"] == 2
    assert [d["channel"] for d in detail["distributions"]] == ["site"]
    assert detail["distributions"][0]["content"]["en"]["url"].startswith("/news/en/articles/")
    assert detail["public_urls"]["zh-TW"] == f"/news/zh-TW/articles/{summary['slug']}"
    assert detail["story_title"] == "Lumen City microgrid" and detail["analytics"] == []

    # another version number: nothing to show in it
    other = (await api.get(f"/api/articles/{article_id}?version=9")).json()
    assert other["shown"] == 1
    assert (await api.get(f"/api/articles/{uuid.uuid4()}")).status_code == 404


async def test_sources_list_and_add(api, newsroom_room, db_session):
    room = newsroom_room
    added = await api.post(
        f"/api/companies/{room.company.id}/sources",
        json={
            "name": "Lumen City News",
            "kind": "rss",
            "url": "https://news.fixtures.autora.test/feed.xml",
            "trust_level": "0.7",
            "language": "en",
        },
    )
    assert added.status_code == 201, added.text
    assert added.json()["status"] == "active" and added.json()["items"] == 0
    [listed] = (await api.get(f"/api/companies/{room.company.id}/sources")).json()
    assert listed["name"] == "Lumen City News" and listed["trust_level"] == "0.70"
    schedules = (
        await db_session.scalars(
            select(Schedule.name).where(Schedule.company_id == room.company.id)
        )
    ).all()
    assert "newsroom.poll_sources" in schedules

    bad = await api.post(
        f"/api/companies/{room.company.id}/sources", json={"name": "x", "kind": "rss"}
    )
    assert bad.status_code == 422 and "feed url" in bad.text
    unknown = await api.get(f"/api/companies/{uuid.uuid4()}/sources")
    assert unknown.status_code == 404


async def test_starting_a_story_from_the_page(api, newsroom_room, db_session):
    room = newsroom_room
    project = Project(
        company_id=room.company.id,
        name="newsroom",
        state="ACTIVE",
        kill_criteria={"max_cost_usd": 5},
    )
    story = Story(company_id=room.company.id, title="Harbor residents", state="DISCOVERED")
    db_session.add_all([project, story])
    await db_session.flush()

    started = await api.post(f"/api/stories/{story.id}/start", json={"project_id": str(project.id)})
    assert started.status_code == 201, started.text
    run_id = started.json()["workflow_run_id"]
    await db_session.refresh(story)
    assert story.state == "IN_PRODUCTION" and story.project_id == project.id
    again = await api.post(f"/api/stories/{story.id}/start", json={})
    assert again.status_code == 409 and "only a selected story" in again.text

    # the workflow's timeline: its events carry its id as correlation
    events = await api.get(
        "/api/events", params={"company_id": str(room.company.id), "correlation_id": run_id}
    )
    types = [e["event_type"] for e in events.json()["items"]]
    assert "WORKFLOW_RUN_CREATED" in types and all(
        e["correlation_id"] == run_id for e in events.json()["items"]
    )

    dropped = Story(company_id=room.company.id, title="Coffee", state="DROPPED")
    db_session.add(dropped)
    await db_session.flush()
    assert (await api.post(f"/api/stories/{dropped.id}/start", json={})).status_code == 409


async def test_the_pages_need_the_operator(api, newsroom_room):
    room = newsroom_room
    anonymous = await api.get(
        f"/api/companies/{room.company.id}/stories", headers={"Authorization": ""}
    )
    assert anonymous.status_code == 401
