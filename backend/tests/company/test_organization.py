"""T-600: the organisation — businesses, functions, positions, and who holds them.

The point of these tests is the set of rules that no single database constraint can express:
the chart cannot contradict itself, and an agent's role string, its position and its department
always say the same thing.
"""

import uuid

import pytest
from sqlalchemy import select

from autora.company.agents import WrongCompany, hire_agent
from autora.company.organization import (
    Contradiction,
    DuplicateKey,
    UnknownUnit,
    active_units,
    add_business_unit,
    add_department,
    add_product,
    add_role,
    assign,
    business_unit_by_key,
    department_by_key,
    org_chart,
    product_by_key,
    role_by_key,
)
from autora.db.models import (
    Agent,
    BusinessUnitState,
    Department,
    EventRecord,
    ProductState,
)
from autora.runtime.actor import Actor
from tests.conftest import unique_company

ACTOR = Actor.human("founder")


async def _company(session, prefix="org"):
    return await unique_company(session, prefix)


async def _newsroom(session, company_id):
    """The shape ARCHITECTURE_V2 describes: a business with a department that has teams,
    beside the company's own shared functions."""
    media = await add_business_unit(
        session,
        company_id=company_id,
        key="ai_media",
        name="AI Media",
        actor=ACTOR,
        state=BusinessUnitState.ACTIVE,
    )
    executive = await add_department(
        session, company_id=company_id, key="executive", name="Executive", actor=ACTOR
    )
    newsroom = await add_department(
        session,
        company_id=company_id,
        key="newsroom",
        name="Newsroom",
        actor=ACTOR,
        business_unit_id=media.id,
    )
    research = await add_department(
        session,
        company_id=company_id,
        key="newsroom_research",
        name="Research",
        actor=ACTOR,
        parent_department_id=newsroom.id,
    )
    return media, executive, newsroom, research


# --- the shape ------------------------------------------------------------------------------


async def test_a_department_can_belong_to_a_business_or_to_the_company(db_session):
    """The one nullable column that lets both kinds live in the same table."""
    company = await _company(db_session)
    media, executive, newsroom, _ = await _newsroom(db_session, company.id)

    assert executive.business_unit_id is None  # a shared function
    assert newsroom.business_unit_id == media.id  # this business's own function


async def test_a_team_inherits_its_parent_s_business(db_session):
    company = await _company(db_session)
    media, _, newsroom, research = await _newsroom(db_session, company.id)

    assert research.parent_department_id == newsroom.id
    assert research.business_unit_id == media.id  # inherited, not passed in


async def test_a_team_may_not_work_for_another_business(db_session):
    company = await _company(db_session)
    media, _, newsroom, _ = await _newsroom(db_session, company.id)
    other = await add_business_unit(
        db_session, company_id=company.id, key="ai_edu", name="AI Education", actor=ACTOR
    )

    with pytest.raises(Contradiction, match="cannot belong"):
        await add_department(
            db_session,
            company_id=company.id,
            key="newsroom_seo",
            name="SEO",
            actor=ACTOR,
            parent_department_id=newsroom.id,
            business_unit_id=other.id,
        )


async def test_keys_are_unique_per_company_and_reusable_across_them(db_session):
    first = await _company(db_session, "org-a")
    second = await _company(db_session, "org-b")
    await add_business_unit(
        db_session, company_id=first.id, key="ai_media", name="AI Media", actor=ACTOR
    )

    with pytest.raises(DuplicateKey, match="business unit"):
        await add_business_unit(
            db_session, company_id=first.id, key="ai_media", name="Again", actor=ACTOR
        )
    # another company may run a business of the same name
    await add_business_unit(
        db_session, company_id=second.id, key="ai_media", name="AI Media", actor=ACTOR
    )


async def test_a_department_of_another_company_cannot_be_a_parent(db_session):
    first = await _company(db_session, "org-a")
    second = await _company(db_session, "org-b")
    _, _, newsroom, _ = await _newsroom(db_session, first.id)

    with pytest.raises(UnknownUnit):
        await add_department(
            db_session,
            company_id=second.id,
            key="borrowed",
            name="Borrowed",
            actor=ACTOR,
            parent_department_id=newsroom.id,
        )


async def test_a_department_cannot_be_its_own_parent(db_session):
    """The database's own guard, so no code path can produce a one-node cycle."""
    company = await _company(db_session)
    _, _, newsroom, _ = await _newsroom(db_session, company.id)

    newsroom.parent_department_id = newsroom.id
    with pytest.raises(Exception, match="not_its_own_parent"):
        await db_session.flush()
    await db_session.rollback()


async def test_a_key_has_to_look_like_a_role_key(db_session):
    """Same shape as agents.role: the model router rejects a second dot, so no namespaces."""
    company = await _company(db_session)
    db_session.add(Department(company_id=company.id, key="ai.media", name="Nope"))
    with pytest.raises(Exception, match="key_format"):
        await db_session.flush()
    await db_session.rollback()


# --- positions ------------------------------------------------------------------------------


async def test_a_role_is_a_position_in_a_department(db_session):
    company = await _company(db_session)
    _, _, newsroom, research = await _newsroom(db_session, company.id)
    chief = await add_role(
        db_session,
        company_id=company.id,
        key="editor_in_chief",
        title="Editor-in-Chief",
        department_id=newsroom.id,
        actor=ACTOR,
        is_lead=True,
    )
    researcher = await add_role(
        db_session,
        company_id=company.id,
        key="researcher",
        title="Researcher",
        department_id=research.id,
        actor=ACTOR,
        reports_to_role_id=chief.id,
        defaults={"tools": ["web_search"], "budget": {"per_run_usd": "0.40"}},
    )

    assert chief.is_lead and not researcher.is_lead
    assert researcher.reports_to_role_id == chief.id
    assert researcher.defaults["tools"] == ["web_search"]
    assert await role_by_key(db_session, company.id, "researcher") is not None


async def test_a_role_needs_a_department_of_its_own_company(db_session):
    first = await _company(db_session, "org-a")
    second = await _company(db_session, "org-b")
    _, _, newsroom, _ = await _newsroom(db_session, first.id)

    with pytest.raises(UnknownUnit):
        await add_role(
            db_session,
            company_id=second.id,
            key="writer",
            title="Writer",
            department_id=newsroom.id,
            actor=ACTOR,
        )


# --- agents in the organisation -------------------------------------------------------------


async def test_hiring_into_a_position_sets_role_department_and_defaults(db_session):
    company = await _company(db_session)
    _, _, _, research = await _newsroom(db_session, company.id)
    position = await add_role(
        db_session,
        company_id=company.id,
        key="researcher",
        title="Researcher",
        department_id=research.id,
        actor=ACTOR,
        defaults={"tools": ["web_search", "fetch_url"], "budget": {"per_run_usd": "0.40"}},
    )

    agent = await hire_agent(
        db_session,
        company_id=company.id,
        role="researcher",
        display_name="Rae",
        actor=ACTOR,
        position=position,
    )

    assert (agent.role, agent.role_id, agent.department_id) == (
        "researcher",
        position.id,
        research.id,
    )
    assert agent.tools == ["web_search", "fetch_url"]
    assert agent.budget == {"per_run_usd": "0.40"}


async def test_what_is_passed_in_wins_over_the_position_s_defaults(db_session):
    company = await _company(db_session)
    _, _, _, research = await _newsroom(db_session, company.id)
    position = await add_role(
        db_session, company_id=company.id, key="researcher", title="Researcher",
        department_id=research.id, actor=ACTOR, defaults={"tools": ["web_search"]},
    )  # fmt: skip

    agent = await hire_agent(
        db_session, company_id=company.id, role="researcher", display_name="Rae",
        actor=ACTOR, position=position, tools=["fetch_url"],
    )  # fmt: skip

    assert agent.tools == ["fetch_url"]


async def test_hiring_refuses_a_position_that_says_something_else(db_session):
    """The role string and the position must agree, or the org chart would lie about what the
    runtime will actually dispatch to this agent."""
    company = await _company(db_session)
    _, _, _, research = await _newsroom(db_session, company.id)
    position = await add_role(
        db_session, company_id=company.id, key="researcher", title="Researcher",
        department_id=research.id, actor=ACTOR,
    )  # fmt: skip

    with pytest.raises(WrongCompany, match="say the same thing"):
        await hire_agent(
            db_session, company_id=company.id, role="writer", display_name="Wren",
            actor=ACTOR, position=position,
        )  # fmt: skip


async def test_hiring_refuses_a_position_of_another_company(db_session):
    first = await _company(db_session, "org-a")
    second = await _company(db_session, "org-b")
    _, _, _, research = await _newsroom(db_session, first.id)
    position = await add_role(
        db_session, company_id=first.id, key="researcher", title="Researcher",
        department_id=research.id, actor=ACTOR,
    )  # fmt: skip

    with pytest.raises(WrongCompany, match="another company"):
        await hire_agent(
            db_session, company_id=second.id, role="researcher", display_name="Rae",
            actor=ACTOR, position=position,
        )  # fmt: skip


async def test_an_agent_hired_without_a_position_still_works(db_session):
    """Backwards compatible on purpose: agents that predate the organisation keep running."""
    company = await _company(db_session)

    agent = await hire_agent(
        db_session, company_id=company.id, role="writer", display_name="Wren", actor=ACTOR
    )

    assert agent.role == "writer"
    assert (agent.role_id, agent.department_id) == (None, None)


async def test_the_created_event_carries_the_department(db_session):
    """Required, not decorative: the realtime reducer rebuilds an agent from this event alone,
    so without the department the office could not place it (ARCHITECTURE_V2 §16.1)."""
    company = await _company(db_session)
    _, _, _, research = await _newsroom(db_session, company.id)
    position = await add_role(
        db_session, company_id=company.id, key="researcher", title="Researcher",
        department_id=research.id, actor=ACTOR,
    )  # fmt: skip

    agent = await hire_agent(
        db_session, company_id=company.id, role="researcher", display_name="Rae",
        actor=ACTOR, position=position,
    )  # fmt: skip

    created = await db_session.scalar(
        select(EventRecord).where(
            EventRecord.company_id == company.id, EventRecord.event_type == "AGENT_CREATED"
        )
    )
    assert created.payload["department_id"] == str(research.id)
    assert created.payload["department_key"] == "newsroom_research"
    assert created.agent_id == agent.id


# --- moving ---------------------------------------------------------------------------------


async def test_assigning_moves_the_role_the_position_and_the_home_together(db_session):
    company = await _company(db_session)
    _, _, newsroom, research = await _newsroom(db_session, company.id)
    researcher = await add_role(
        db_session, company_id=company.id, key="researcher", title="Researcher",
        department_id=research.id, actor=ACTOR,
    )  # fmt: skip
    chief = await add_role(
        db_session, company_id=company.id, key="editor_in_chief", title="Editor-in-Chief",
        department_id=newsroom.id, actor=ACTOR, is_lead=True,
    )  # fmt: skip
    agent = await hire_agent(
        db_session, company_id=company.id, role="researcher", display_name="Rae",
        actor=ACTOR, position=researcher,
    )  # fmt: skip

    await assign(db_session, agent, chief, actor=ACTOR)

    assert (agent.role, agent.role_id, agent.department_id) == (
        "editor_in_chief",
        chief.id,
        newsroom.id,
    )
    moved = await db_session.scalar(
        select(EventRecord).where(
            EventRecord.company_id == company.id, EventRecord.event_type == "AGENT_ASSIGNED"
        )
    )
    assert moved.payload["role"] == "editor_in_chief"
    assert moved.payload["department_key"] == "newsroom"
    assert moved.payload["previous_role"] == "researcher"
    assert moved.payload["previous_department_id"] == str(research.id)


async def test_assigning_the_same_position_again_says_nothing(db_session):
    company = await _company(db_session)
    _, _, _, research = await _newsroom(db_session, company.id)
    position = await add_role(
        db_session, company_id=company.id, key="researcher", title="Researcher",
        department_id=research.id, actor=ACTOR,
    )  # fmt: skip
    agent = await hire_agent(
        db_session, company_id=company.id, role="researcher", display_name="Rae",
        actor=ACTOR, position=position,
    )  # fmt: skip

    await assign(db_session, agent, position, actor=ACTOR)

    assert not (
        await db_session.scalars(
            select(EventRecord).where(
                EventRecord.company_id == company.id,
                EventRecord.event_type == "AGENT_ASSIGNED",
            )
        )
    ).all()


async def test_an_agent_cannot_take_a_position_in_another_company(db_session):
    first = await _company(db_session, "org-a")
    second = await _company(db_session, "org-b")
    _, _, _, research = await _newsroom(db_session, first.id)
    position = await add_role(
        db_session, company_id=first.id, key="researcher", title="Researcher",
        department_id=research.id, actor=ACTOR,
    )  # fmt: skip
    agent = await hire_agent(
        db_session, company_id=second.id, role="writer", display_name="Wren", actor=ACTOR
    )

    with pytest.raises(Contradiction, match="another company"):
        await assign(db_session, agent, position, actor=ACTOR)


# --- products -------------------------------------------------------------------------------


async def test_a_product_belongs_to_a_business(db_session):
    company = await _company(db_session)
    media, *_ = await _newsroom(db_session, company.id)

    product = await add_product(
        db_session,
        company_id=company.id,
        key="daily_english_world",
        name="Daily English World",
        business_unit_id=media.id,
        actor=ACTOR,
        state=ProductState.LIVE,
        public_url="/news/zh-TW",
    )

    assert product.business_unit_id == media.id
    assert await product_by_key(db_session, company.id, "daily_english_world") is not None
    created = await db_session.scalar(
        select(EventRecord).where(
            EventRecord.company_id == company.id, EventRecord.event_type == "PRODUCT_CREATED"
        )
    )
    assert created.payload["business_unit_id"] == str(media.id)


async def test_a_product_of_an_unknown_business_is_refused(db_session):
    company = await _company(db_session)

    with pytest.raises(UnknownUnit):
        await add_product(
            db_session, company_id=company.id, key="ghost", name="Ghost",
            business_unit_id=uuid.uuid4(), actor=ACTOR,
        )  # fmt: skip


# --- reading it -----------------------------------------------------------------------------


async def test_the_org_chart_is_the_whole_shape(db_session):
    company = await _company(db_session)
    media, executive, newsroom, research = await _newsroom(db_session, company.id)
    ceo_role = await add_role(
        db_session, company_id=company.id, key="ceo", title="CEO",
        department_id=executive.id, actor=ACTOR, is_lead=True,
    )  # fmt: skip
    researcher = await add_role(
        db_session, company_id=company.id, key="researcher", title="Researcher",
        department_id=research.id, actor=ACTOR,
    )  # fmt: skip
    await add_role(
        db_session, company_id=company.id, key="editor_in_chief", title="Editor-in-Chief",
        department_id=newsroom.id, actor=ACTOR, is_lead=True,
    )  # fmt: skip
    await add_product(
        db_session, company_id=company.id, key="daily_english_world",
        name="Daily English World", business_unit_id=media.id, actor=ACTOR,
    )  # fmt: skip
    await hire_agent(
        db_session, company_id=company.id, role="ceo", display_name="Cee",
        actor=ACTOR, position=ceo_role,
    )  # fmt: skip
    await hire_agent(
        db_session, company_id=company.id, role="researcher", display_name="Rae",
        actor=ACTOR, position=researcher,
    )  # fmt: skip
    stray = await hire_agent(
        db_session, company_id=company.id, role="writer", display_name="Wren", actor=ACTOR
    )

    chart = await org_chart(db_session, company.id)

    # the company's own functions
    assert [d.department.key for d in chart.shared.departments] == ["executive"]
    assert [a.display_name for a in chart.shared.departments[0].agents] == ["Cee"]
    # the business, its departments and what it sells
    (unit,) = chart.units
    assert unit.unit.key == "ai_media"
    assert [p.key for p in unit.products] == ["daily_english_world"]
    (desk,) = unit.departments
    assert desk.department.key == "newsroom"
    assert [r.key for r in desk.roles] == ["editor_in_chief"]
    # the team inside it, with its own people
    (team,) = desk.teams
    assert team.department.key == "newsroom_research"
    assert [a.display_name for a in team.agents] == ["Rae"]
    assert desk.headcount == 1  # nobody at the desk itself; one in its team
    # an agent with no place on the chart is still counted, not lost
    assert [a.display_name for a in chart.unplaced] == [stray.display_name]
    assert chart.headcount == 3


async def test_a_company_with_no_organisation_has_an_empty_chart(db_session):
    company = await _company(db_session)
    await hire_agent(
        db_session, company_id=company.id, role="writer", display_name="Wren", actor=ACTOR
    )

    chart = await org_chart(db_session, company.id)

    assert chart.shared.departments == () and chart.units == ()
    assert len(chart.unplaced) == 1 and chart.headcount == 1


async def test_a_retired_agent_leaves_the_chart(db_session):
    from autora.company.agents import retire_agent

    company = await _company(db_session)
    _, _, _, research = await _newsroom(db_session, company.id)
    position = await add_role(
        db_session, company_id=company.id, key="researcher", title="Researcher",
        department_id=research.id, actor=ACTOR,
    )  # fmt: skip
    agent = await hire_agent(
        db_session, company_id=company.id, role="researcher", display_name="Rae",
        actor=ACTOR, position=position,
    )  # fmt: skip
    await retire_agent(db_session, agent, actor=ACTOR)

    chart = await org_chart(db_session, company.id)

    assert chart.headcount == 0
    assert chart.units[0].departments[0].teams[0].roles  # the position stays; the person left


async def test_only_running_businesses_are_planned_for(db_session):
    company = await _company(db_session)
    await _newsroom(db_session, company.id)  # ai_media, ACTIVE
    await add_business_unit(
        db_session, company_id=company.id, key="ai_edu", name="AI Education", actor=ACTOR
    )  # PROPOSED

    assert [u.key for u in await active_units(db_session, company.id)] == ["ai_media"]


async def test_lookups_by_key(db_session):
    company = await _company(db_session)
    await _newsroom(db_session, company.id)

    assert (await business_unit_by_key(db_session, company.id, "ai_media")).name == "AI Media"
    assert (await department_by_key(db_session, company.id, "newsroom")).name == "Newsroom"
    assert await department_by_key(db_session, company.id, "nope") is None


async def test_every_agent_of_a_department_is_found_by_its_home(db_session):
    """What the 3D office asks: who is in this room?"""
    company = await _company(db_session)
    _, _, _, research = await _newsroom(db_session, company.id)
    for key, title in (("researcher", "Researcher"), ("analyst", "Analyst")):
        position = await add_role(
            db_session, company_id=company.id, key=key, title=title,
            department_id=research.id, actor=ACTOR,
        )  # fmt: skip
        await hire_agent(
            db_session, company_id=company.id, role=key, display_name=title,
            actor=ACTOR, position=position,
        )  # fmt: skip

    in_research = (
        await db_session.scalars(
            select(Agent.display_name)
            .where(Agent.department_id == research.id)
            .order_by(Agent.display_name)
        )
    ).all()

    assert list(in_research) == ["Analyst", "Researcher"]
