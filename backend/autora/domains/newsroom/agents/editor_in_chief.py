"""The editor-in-chief: decides what the newsroom covers today (T-605b).

This is the job that used to be wrongly assigned to the CEO. The CEO says what AI Media may
spend; the editor-in-chief says what it spends it *on* — which stories are worth the desk's day,
in what order, and how many are enough.

It is asked once a cycle, in PLANNING, after the CEO. That order matters: a desk head decides
inside the budget the company just set, not beside it.

**It commissions; it does not write.** Its one tool takes a candidate story and puts the whole
line to work on it — research, analysis, draft, review, approval, publication. What comes out is
the writers' and editors' business, and the chief sees it again only in tomorrow's numbers.

**Its plan is checked against the desk.** Like the CEO's, what it writes down must match what it
actually did: a story listed in the plan has to be one the newsroom is genuinely working on.
Saying "we are covering the microgrid story" without commissioning it would leave a company that
believes it has three articles coming and no one writing them.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.ledger import Ledger
from autora.db.models import Budget, BusinessUnit, Project
from autora.domains.newsroom.models import Article, ArticleState, Story, StoryState
from autora.runtime.behaviors import AgentBehavior, RunContext

ROLE = "editor_in_chief"
TASK = "plan"

MAX_STORIES = 5
"""What one desk can carry in a day. The company's own cap on workflows per cycle applies too,
and is the one that actually refuses — this is the chief's own judgement, stated up front."""

CANDIDATES = 8


class Commission(BaseModel):
    story_id: uuid.UUID
    priority: int = Field(ge=1, le=MAX_STORIES)
    angle: str | None = Field(default=None, max_length=300)
    """What this story is about, for the researcher. Optional: some stories are obvious."""


class EditorialPlan(BaseModel):
    """What the newsroom is covering today, and why."""

    stories: list[Commission] = Field(default_factory=list, max_length=MAX_STORIES)
    target_articles: int = Field(ge=0, le=MAX_STORIES)
    rationale: str = Field(min_length=1, max_length=2000)


SYSTEM_PROMPT = f"""You are the editor-in-chief of a small bilingual newsroom.

Once a day you decide what the desk covers. You are given the candidate stories, what the desk
published recently, what is still in production, and the budget the company allocated to this
newsroom for the cycle.

Choose the stories worth the day. Judge them on what a reader in this city would want to know
and on whether the sources can carry a checkable article — not on how interesting the topic
sounds. A story with one weak source is not a story.

Commission each one with commission_story. That puts the whole desk to work on it: research,
analysis, a bilingual draft, review, and publication after a person approves. You do not write,
edit or fact-check — the desk does that, and you see the result in tomorrow's numbers.

At most {MAX_STORIES} stories, and fewer is usually right: two well-sourced articles beat five
thin ones, and the company pays for every one of them. If nothing is worth covering, commission
nothing and say so — an empty day is a decision, not a failure.

Then write your plan. It must list exactly the stories you commissioned."""


# --- what it must not get wrong ---------------------------------------------------------------


async def stories_are_the_company_s_candidates(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    """Every story must be this newsroom's, and one that could still be taken up."""
    assert isinstance(output, EditorialPlan)
    if not output.stories:
        return []
    ids = [c.story_id for c in output.stories]
    rows = {
        story.id: story
        for story in (
            await session.scalars(
                select(Story).where(Story.company_id == ctx.company_id, Story.id.in_(ids))
            )
        ).all()
    }
    issues = []
    for story_id in ids:
        story = rows.get(story_id)
        if story is None:
            issues.append(f"story {story_id} is not one of this newsroom's")
        elif story.state in (StoryState.PUBLISHED.value, StoryState.DROPPED.value):
            issues.append(f"{story.title!r} is already {story.state}; it cannot be covered again")
    return issues


async def commissioned_what_it_listed(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    """A story in the plan has to be one the desk is actually working on.

    Otherwise the company believes three articles are coming and nobody is writing them.
    """
    assert isinstance(output, EditorialPlan)
    if not output.stories:
        return []
    ids = [c.story_id for c in output.stories]
    working = {
        story_id
        for story_id in (
            await session.scalars(
                select(Story.id).where(
                    Story.id.in_(ids),
                    Story.state.in_([StoryState.IN_PRODUCTION.value, StoryState.PUBLISHED.value]),
                )
            )
        ).all()
    }
    return [
        f"story {story_id} is in the plan but was never commissioned with commission_story"
        for story_id in ids
        if story_id not in working
    ]


async def the_count_matches(session: AsyncSession, ctx: RunContext, output: BaseModel) -> list[str]:
    assert isinstance(output, EditorialPlan)
    if output.target_articles != len(output.stories):
        return [
            f"target_articles is {output.target_articles} but {len(output.stories)} stories "
            "are listed; they have to be the same number"
        ]
    return []


# --- what it is given ---------------------------------------------------------------------------


async def desk_context(session: AsyncSession, ctx: RunContext) -> str | None:
    """OBSERVE: the candidates, the desk's recent record, and what it may spend."""
    candidates = (
        await session.scalars(
            select(Story)
            .where(
                Story.company_id == ctx.company_id,
                Story.state.in_([StoryState.DISCOVERED.value, StoryState.SELECTED.value]),
            )
            .order_by(Story.score.desc(), Story.updated_at.desc())
            .limit(CANDIDATES)
        )
    ).all()
    in_production = (
        await session.scalars(
            select(Story.title).where(
                Story.company_id == ctx.company_id,
                Story.state == StoryState.IN_PRODUCTION.value,
            )
        )
    ).all()
    published = await session.scalar(
        select(func.count())
        .select_from(Article)
        .where(
            Article.company_id == ctx.company_id,
            Article.state == ArticleState.PUBLISHED.value,
        )
    )

    lines = ["Candidate stories (best first):"]
    if candidates:
        for story in candidates:
            lines.append(
                f"- {story.id} · {story.title} · score {float(story.score or 0):.2f} · "
                f"{story.sources_count} source(s)"
                + (f" · {story.summary[:160]}" if story.summary else "")
            )
    else:
        lines.append("- none: nothing has been gathered that is worth covering")

    lines.append("")
    lines.append(
        f"Already in production: {', '.join(in_production) if in_production else 'nothing'}"
    )
    lines.append(f"Articles published so far: {int(published or 0)}")
    budget = await _budget(session, ctx.company_id)
    lines.append(
        f"This newsroom's budget: {budget}"
        if budget
        else "This newsroom has no budget of its own this cycle; the company's cap applies."
    )
    return "\n".join(lines)


async def _budget(session: AsyncSession, company_id: uuid.UUID) -> str | None:
    """What AI Media may spend, and what is left of it."""
    unit = await session.scalar(
        select(BusinessUnit).where(
            BusinessUnit.company_id == company_id, BusinessUnit.key == "ai_media"
        )
    )
    if unit is None:
        return None
    amount = await session.scalar(
        select(Budget.amount).where(
            Budget.company_id == company_id, Budget.business_unit_id == unit.id
        )
    )
    if amount is None:
        return None
    projects = (
        await session.scalars(select(Project.id).where(Project.business_unit_id == unit.id))
    ).all()
    spent = Decimal(0)
    ledger = Ledger()
    for project_id in projects:
        spent += await ledger.spent(session, company_id, project_id=project_id)
    return f"${amount} allocated, ${spent} spent so far"


def _summary(plan: BaseModel) -> str:
    assert isinstance(plan, EditorialPlan)
    if not plan.stories:
        return "nothing worth covering today"
    return f"{len(plan.stories)} story(ies) commissioned"


BEHAVIOR = AgentBehavior(
    role=ROLE,
    task_name=TASK,
    capability="reasoning",
    system_prompt=SYSTEM_PROMPT,
    output_model=EditorialPlan,
    tools=("commission_story",),
    validators=(
        stories_are_the_company_s_candidates,
        commissioned_what_it_listed,
        the_count_matches,
    ),
    max_steps=8,
    repair_limit=2,
    max_output_tokens=2048,
    context=desk_context,
    summarize=_summary,
)
