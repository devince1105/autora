"""T-600: the org chart over the API, and the shape the newsroom seed actually builds."""

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
    assert executive["roles"][0]["held_by"] == []  # the chair is defined, not filled
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
    assert desk["roles"][0]["is_lead"] and desk["roles"][0]["held_by"] == []
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

    assert org["headcount"] == 5  # the five newsroom agents; no CEO, no editor-in-chief
    assert org["unplaced"] == []
    (unit,) = org["units"]
    (desk,) = unit["departments"]
    assert desk["headcount"] == 5  # all of them sit in the desk's teams
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
