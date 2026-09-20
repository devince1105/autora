"""T-600: the org chart over the API, and the shape the newsroom seed actually builds."""

from decimal import Decimal

import pytest

from autora.company.organization import bootstrap_executive
from autora.domains.newsroom import organization as newsroom_org
from autora.domains.newsroom.workflow import staff_newsroom
from autora.runtime.actor import Actor
from tests.conftest import unique_company

ACTOR = Actor.human("founder")


@pytest.fixture
async def staffed(committed):
    """A company organised the way the newsroom seed organises it."""
    async with committed() as session:
        company = await unique_company(session, "org-api")
        await bootstrap_executive(session, company.id, actor=ACTOR)
        await staff_newsroom(session, company.id, actor=ACTOR)
        await session.commit()
    return company


async def test_the_chart_separates_the_company_s_functions_from_its_business(api, staffed):
    response = await api.get(f"/api/companies/{staffed.id}/org")
    assert response.status_code == 200
    org = response.json()

    # what the company does for itself, and has nobody doing yet
    assert [d["key"] for d in org["shared"]["departments"]] == ["executive"]
    executive = org["shared"]["departments"][0]
    assert [r["key"] for r in executive["roles"]] == ["ceo"]
    # this fixture staffs the newsroom only; the demo seed also hires a CEO (T-605a)
    assert executive["roles"][0]["held_by"] == []
    assert executive["headcount"] == 0

    # the business it is in
    (unit,) = org["units"]
    assert (unit["key"], unit["state"]) == ("ai_media", "ACTIVE")
    assert [p["key"] for p in unit["products"]] == ["daily_english_world"]
    assert unit["products"][0]["public_url"] == "/news/zh-TW"


async def test_the_newsroom_is_a_department_with_teams_under_it(api, staffed):
    org = (await api.get(f"/api/companies/{staffed.id}/org")).json()
    (unit,) = org["units"]
    (desk,) = unit["departments"]

    assert desk["key"] == "newsroom"
    assert [r["key"] for r in desk["roles"]] == ["editor_in_chief"]
    # the chair has someone in it now: the desk head decides what the newsroom covers (T-605b)
    assert desk["roles"][0]["is_lead"] and len(desk["roles"][0]["held_by"]) == 1
    assert [t["key"] for t in desk["teams"]] == [
        "newsroom_audience",
        "newsroom_editing",
        "newsroom_research",
        "newsroom_writing",
    ]
    research = next(t for t in desk["teams"] if t["key"] == "newsroom_research")
    assert sorted(r["key"] for r in research["roles"]) == ["analyst", "researcher"]
    assert sorted(a["display_name"] for a in research["agents"]) == ["Ana", "Rae"]


async def test_everyone_hired_is_on_the_chart_and_counted_once(api, staffed):
    org = (await api.get(f"/api/companies/{staffed.id}/org")).json()

    assert org["headcount"] == 6  # the five who make the articles, plus the head of the desk
    assert org["unplaced"] == []
    (unit,) = org["units"]
    (desk,) = unit["departments"]
    assert desk["headcount"] == 6  # the head sits at the desk; the rest in its teams
    assert sum(t["headcount"] for t in desk["teams"]) == 5


async def test_a_position_says_who_holds_it(api, staffed):
    org = (await api.get(f"/api/companies/{staffed.id}/org")).json()
    (desk,) = org["units"][0]["departments"]
    writing = next(t for t in desk["teams"] if t["key"] == "newsroom_writing")
    (writer,) = writing["roles"]

    assert writer["key"] == "writer" and writer["title"] == "Writer"
    assert writer["responsibilities"]
    (wren,) = writing["agents"]
    assert writer["held_by"] == [wren["id"]]
    assert wren["department_key"] == "newsroom_writing"


async def test_an_agent_carries_its_department_in_the_roster_too(api, staffed):
    agents = (await api.get(f"/api/companies/{staffed.id}/agents")).json()

    by_name = {a["display_name"]: a for a in agents}
    assert by_name["Rae"]["department_key"] == "newsroom_research"
    assert by_name["Mika"]["department_key"] == "newsroom_audience"


async def test_a_company_with_no_organisation_still_answers(api):
    company = (
        await api.post(
            "/api/companies",
            json={"slug": f"bare-{id(object()) % 10**9}", "name": "Bare", "type": "newsroom"},
        )
    ).json()

    org = (await api.get(f"/api/companies/{company['id']}/org")).json()

    assert org["shared"]["departments"] == [] and org["units"] == []
    assert org["headcount"] == 0


async def test_an_unknown_company_is_not_found(api):
    response = await api.get("/api/companies/01a0bd00-0000-7000-8000-000000000000/org")
    assert response.status_code == 404


async def test_seeding_twice_builds_one_organisation(committed, staffed):
    """The seed is run again on every dev restart; it must not grow a second newsroom."""
    async with committed() as session:
        await staff_newsroom(session, staffed.id, actor=ACTOR)
        await session.commit()
        chart = await newsroom_org.build(session, staffed.id, actor=ACTOR)

    assert chart.business_unit.key == "ai_media"
    assert len(chart.teams) == 4


# --- the CEO's view (T-603) ------------------------------------------------------------------


async def test_the_snapshot_shows_the_company_as_a_portfolio(api, staffed):
    response = await api.get(f"/api/companies/{staffed.id}/snapshot")
    assert response.status_code == 200
    snapshot = response.json()

    (unit,) = snapshot["portfolio"]
    assert (unit["key"], unit["state"]) == ("ai_media", "ACTIVE")
    assert [p["key"] for p in unit["products"]] == ["daily_english_world"]
    assert unit["kill_criteria"]["auto_pause_if"]["metric"] == "cost_per_published_article"
    assert snapshot["capital"]["balance"] == "0.000000"
    assert snapshot["trimmed"] == []  # a company this size fits


async def test_the_snapshot_carries_the_newsroom_s_candidates(api, committed, staffed):
    """What the newsroom puts in front of whoever plans the day (the editor-in-chief, T-605b)."""
    from autora.domains.newsroom.models import Story

    async with committed() as session:
        session.add_all([
            Story(company_id=staffed.id, title="A microgrid for Lumen City",
                  state="DISCOVERED", score=Decimal("0.90"), items_count=3),
            Story(company_id=staffed.id, title="Quieter trams", state="DISCOVERED",
                  score=Decimal("0.40"), items_count=1),
            Story(company_id=staffed.id, title="Old news", state="PUBLISHED",
                  score=Decimal("0.99"), items_count=9),
        ])  # fmt: skip
        await session.commit()

    snapshot = (await api.get(f"/api/companies/{staffed.id}/snapshot")).json()

    candidates = snapshot["domains"]["newsroom"]["candidates"]
    assert [c["title"] for c in candidates] == ["A microgrid for Lumen City", "Quieter trams"]
    assert candidates[0]["score"] == 0.9  # ordered by score; a published story is not a candidate


async def test_a_tight_budget_is_reported_not_hidden(api, staffed):
    snapshot = (await api.get(f"/api/companies/{staffed.id}/snapshot?tokens=60")).json()

    assert snapshot["trimmed"]
    assert snapshot["portfolio"][0]["key"] == "ai_media"  # still there: the decision needs it


async def test_the_snapshot_of_an_unknown_company_is_not_found(api):
    response = await api.get("/api/companies/01a0bd00-0000-7000-8000-000000000000/snapshot")
    assert response.status_code == 404
