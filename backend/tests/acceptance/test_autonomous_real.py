"""AC-14 with a real model: seven days the company runs on its own judgement (T-610, T-519).

Everything about the autonomous loop has been proven with a scripted model. This is the same
loop with the model that is actually configured — the CEO's prompt, the strategist's prompt,
their output schemas and their validators, against something that improvises.

Run it **alone and with its own database**, because a pytest session recreates the test schema
and would wipe this one's data mid-run::

    DATABASE_URL=<dev url, database autora_soak> \
        .venv/bin/pytest backend -m integration -k autonomous_real -s

(the fixtures add the ``_test`` suffix themselves). ``-s`` shows the per-cycle line as it goes,
which is the point of watching a soak.

**What is real and what is not.** The model is real. The clock is not: a cycle's stages are
advanced by a fake clock, so the run is bounded by how long the model takes and not by a day.
The newsroom's line is not in this company at all — a real newsroom day takes hours (T-519 runs
one article and gives it 45 minutes), and seven of them would prove nothing this does not. What
is under test here is the **company's own** agents: plan, explore, review.

**What it may not do.** It may not require the model to succeed. A soak's job is to say what
happened: every cycle must reach DONE and carry a plan — the loop may not hang and may not
leave a day undecided — and how many of those plans the CEO actually produced, rather than the
fallback, is printed and asserted loosely. A model that fails every cycle is a result, not a
broken test.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from autora.app import build_runtime, build_worker
from autora.company import opportunities
from autora.company.agents import strategist
from autora.company.agents.roster import hire_agent
from autora.company.companies import create_company
from autora.company.organization import add_role, bootstrap_executive
from autora.db.models import (
    BusinessProposal,
    Cycle,
    CycleStage,
    EventRecord,
    ModelCall,
    Project,
    ProjectState,
)
from autora.infra.settings import SettingsError, load_settings
from autora.runtime.actor import Actor
from tests.acceptance.test_autonomous import Clock, _drive, _wind_to_next_start

OPERATOR = Actor.human("soak-operator")
CYCLES = int(os.environ.get("AUTONOMY_SOAK_CYCLES", "7"))
"""AC-14's seven, unless a shorter look is wanted."""


def _real_provider() -> str | None:
    try:
        settings = load_settings()
    except SettingsError:
        return None
    return None if settings.model_provider == "fake" else settings.model_provider


PROVIDER = _real_provider()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        PROVIDER is None, reason="needs MODEL_PROVIDER=anthropic|nvidia and its key"
    ),
]


async def test_seven_cycles_with_a_real_model(committed, db_settings, tmp_path, capsys):
    settings = load_settings().model_copy(
        update={
            "database_url": db_settings.database_url,
            "blob_store_dir": tmp_path / "blobs",
            "worker_id": "live-autonomy",
            "tools_profile": "fixture",
            # a free endpoint can take minutes over one reply (D-006); a timeout here is the
            # provider's, and the cycle's deadline would otherwise hide it as a fallback
            "nvidia_timeout_seconds": 420.0,
            "worker_poll_seconds": 0.2,
        }
    )
    clock = Clock(datetime.now(UTC))
    runtime = build_runtime(settings)
    runtime.cycles.clock = clock
    slug = f"soak-real-{uuid.uuid4().hex[:8]}"

    async with committed() as session:
        company, _ = await create_company(
            session, slug=slug, name="A Company With Its Own Judgement",
            mission="find something worth doing, and do it within its means", actor=OPERATOR,
        )  # fmt: skip
        department, ceo_role = await bootstrap_executive(session, company.id, actor=OPERATOR)
        strategist_role = await add_role(
            session, company_id=company.id, department_id=department.id,
            key=strategist.ROLE, title="Strategist", actor=OPERATOR,
        )  # fmt: skip
        for role, name in ((ceo_role, "Cyra"), (strategist_role, "Sol")):
            await hire_agent(
                session, company_id=company.id, role=role.key, display_name=name,
                actor=OPERATOR, position=role,
            )  # fmt: skip
        session.add(
            Project(
                company_id=company.id,
                name="Keep the lights on",
                state=ProjectState.ACTIVE.value,
                kill_criteria={"auto_pause_if": {"metric": "cost", "op": ">", "value": 5}},
            )
        )
        opportunity = await opportunities.discover(
            session,
            company_id=company.id,
            key="ai_english",
            title="AI English learning for adults",
            thesis="Adults pay for lessons, and a model teaches at the margin of zero.",
            market="Taiwan, working adults who already pay for classes",
            actor=OPERATOR,
        )
        await opportunities.add_signal(
            session, opportunity, source="search",
            summary="three subscription competitors, none of them bilingual", actor=OPERATOR,
        )  # fmt: skip
        await session.commit()
        company_id = company.id

    worker = build_worker(
        settings, session_factory=committed, company_ids=frozenset({company_id}),
        runtime=runtime,
    )  # fmt: skip
    worker.clock = clock
    worker.scheduler.clock = clock
    worker.maintenance_interval = 1

    for _ in range(CYCLES):
        await _wind_to_next_start(committed, company_id, clock)
        clock.advance(minutes=10)
        await _drive(worker, clock, hours=17)
        async with committed() as session:
            latest = await session.scalar(
                select(Cycle).where(Cycle.company_id == company_id).order_by(Cycle.seq.desc())
            )
        with capsys.disabled():
            print(
                f"  cycle {latest.seq}: {latest.stage} "
                f"plan={(latest.plan or {}).get('by')} "
                f"review={'missing' if (latest.review or {}).get('missing') else 'ok'}"
            )

    async with committed() as session:
        cycles = (
            await session.scalars(
                select(Cycle).where(Cycle.company_id == company_id).order_by(Cycle.seq)
            )
        ).all()
        calls = (
            await session.execute(
                select(ModelCall.status, func.count(), func.sum(ModelCall.cost_usd))
                .where(ModelCall.company_id == company_id)
                .group_by(ModelCall.status)
            )
        ).all()
        proposal = await session.scalar(
            select(BusinessProposal).where(BusinessProposal.company_id == company_id)
        )
        opened = await session.scalar(
            select(func.min(EventRecord.seq)).where(
                EventRecord.company_id == company_id,
                EventRecord.event_type == "CYCLE_STARTED",
            )
        )
        human = (
            await session.scalars(
                select(EventRecord.event_type).where(
                    EventRecord.company_id == company_id,
                    EventRecord.actor["kind"].astext == "human",
                    EventRecord.seq > opened,
                )
            )
        ).all()

    by_ceo = [c.seq for c in cycles if (c.plan or {}).get("by") == "ceo"]
    reviewed = [c.seq for c in cycles if c.review and not c.review.get("missing")]
    with capsys.disabled():
        print(
            f"\n{PROVIDER} {load_settings().frontier_model_id}: {len(cycles)} cycles, "
            f"{len(by_ceo)} planned by the CEO, {len(reviewed)} reviewed, "
            f"proposal={'yes' if proposal else 'no'}, model calls={[tuple(c) for c in calls]}"
        )

    # the loop's own promises, which hold whatever the model does
    assert len(cycles) == CYCLES, [f"{c.seq}:{c.stage}" for c in cycles]
    for cycle in cycles:
        assert cycle.stage == CycleStage.DONE.value, f"cycle {cycle.seq} hung in {cycle.stage}"
        assert cycle.plan, f"cycle {cycle.seq} ended with no plan, not even a fallback"
        assert cycle.governance is not None, f"nobody governed cycle {cycle.seq}"
    assert human == [], sorted(set(human))

    # and what the model managed, loosely: a soak reports, it does not demand perfection
    assert by_ceo, "the CEO never produced a usable plan in any cycle"
    assert reviewed, "the CEO never produced a usable review in any cycle"
