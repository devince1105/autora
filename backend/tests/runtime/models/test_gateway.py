"""T-207: model router, fake provider and gateway."""

import re
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from autora.db.models import ModelCall, Task
from autora.infra.settings import load_settings
from autora.runtime.models import (
    FREE,
    CallContext,
    Message,
    ModelBinding,
    ModelCallFailed,
    ModelConfigError,
    ModelGateway,
    ModelRequest,
    ModelRouter,
    Price,
    Usage,
    router_from_settings,
)
from autora.runtime.models.providers.fake import FakeModelProvider, FakeToolUse, FakeTurn
from tests.conftest import running_agent_run

PACKAGE = Path(__file__).resolve().parents[3] / "autora"


class ResearchNote(BaseModel):
    story: str
    sources: list[str] = Field(min_length=1)


def _binding(provider="fake", model_id="fake-a", price=FREE):
    return ModelBinding(provider=provider, model_id=model_id, price=price)


# --- router & price ----------------------------------------------------------------------


def test_route_resolution_most_specific_first():
    router = ModelRouter(
        aliases={"frontier": _binding(), "fast": _binding(model_id="fake-b")},
        routes={
            "*.*": "frontier",
            "*.drafting": "fast",
            "editor.drafting": "frontier",
            "marketing.*": "fast",
        },
    )
    assert router.resolve("writer", "drafting") == "fast"
    assert router.resolve("editor", "drafting") == "frontier"
    assert router.resolve("marketing", "reasoning") == "fast"
    assert router.resolve("ceo", "reasoning") == "frontier"


@pytest.mark.parametrize(
    ("routes", "fallbacks", "message"),
    [
        ({"*.*": "missing"}, {}, "unknown alias"),
        ({"reasoning": "a"}, {}, "role.capability"),
        ({"*.*": "a"}, {"a": ["zzz"]}, "unknown"),
        ({"*.*": "a"}, {"a": ["a"]}, "itself"),
    ],
)
def test_invalid_router_config(routes, fallbacks, message):
    with pytest.raises(ModelConfigError, match=message):
        ModelRouter(aliases={"a": _binding()}, routes=routes, fallbacks=fallbacks)


def test_no_route_is_an_error():
    router = ModelRouter(aliases={"a": _binding()}, routes={"writer.drafting": "a"})
    with pytest.raises(ModelConfigError, match="no route"):
        router.resolve("ceo", "reasoning")


def test_price_cost_per_million_tokens():
    price = Price(input=Decimal("5"), output=Decimal("25"), cache_read=Decimal("0.5"))
    usage = Usage(input_tokens=12_000, output_tokens=800, cache_read_tokens=100_000)
    # 12k*5 + 800*25 + 100k*0.5 = 60000 + 20000 + 50000 = 130000 / 1e6
    assert price.cost(usage) == Decimal("0.130000")
    assert price.cost(Usage(input_tokens=1, output_tokens=0)) == Decimal("0.000005")


def test_router_from_settings_fake():
    settings = load_settings(_env_file=None, database_url="postgresql+asyncpg://u:p@h/db")
    router = router_from_settings(settings)
    assert router.binding(router.resolve("ceo", "reasoning")).provider == "fake"


def test_no_model_ids_hardcoded_in_package():
    """Model ids live in configuration only (platform/09). Vendor ids must not appear in code."""
    pattern = re.compile(r"(claude|gpt|gemini)-[0-9a-z]", re.IGNORECASE)
    offenders = [
        f"{path.relative_to(PACKAGE)}:{n}"
        for path in PACKAGE.rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


# --- fake provider & gateway -------------------------------------------------------------


@pytest.fixture
async def ctx(committed):
    async with committed() as session:
        run = await running_agent_run(session, "gw")
        task = await session.get(Task, run.task_id)
        await session.commit()
    return CallContext(
        company_id=run.company_id,
        project_id=task.project_id,
        agent_id=run.agent_id,
        task_id=run.task_id,
        run_id=run.id,
        role="researcher",
        task_name="research",
        attempt=1,
    )


def _gateway(committed, fake, router=None):
    router = router or ModelRouter(
        aliases={"frontier": _binding(price=Price(input=Decimal("10"), output=Decimal("50")))},
        routes={"*.*": "frontier"},
    )
    return ModelGateway(router=router, providers={"fake": fake}, session_factory=committed)


def _request(ctx, **kw):
    return ModelRequest(
        capability="research_extraction",
        context=ctx,
        messages=[Message.user("Find today's AI stories")],
        **kw,
    )


async def _calls(committed, run_id):
    async with committed() as session:
        return (
            await session.scalars(
                select(ModelCall).where(ModelCall.run_id == run_id).order_by(ModelCall.created_at)
            )
        ).all()


async def test_successful_call_is_recorded_with_attribution_and_cost(committed, ctx):
    fake = FakeModelProvider()
    fake.script(
        "researcher",
        "research",
        1,
        FakeTurn(tool_uses=[FakeToolUse(name="web_search", input={"query": "EU AI Act"})],
                 input_tokens=2000, output_tokens=100),
    )  # fmt: skip
    response = await _gateway(committed, fake).complete(_request(ctx))

    assert response.stop_reason == "tool_use"
    assert [(u.name, u.input) for u in response.tool_uses] == [
        ("web_search", {"query": "EU AI Act"})
    ]
    # 2000*10 + 100*50 = 25000 / 1e6
    assert response.cost_usd == Decimal("0.025000")
    [row] = await _calls(committed, ctx.run_id)
    assert row.id == response.model_call_id
    assert (row.status, row.alias, row.provider, row.model_id) == (
        "ok",
        "frontier",
        "fake",
        "fake-a",
    )
    assert (row.company_id, row.project_id, row.task_id, row.agent_id) == (
        ctx.company_id, ctx.project_id, ctx.task_id, ctx.agent_id,
    )  # fmt: skip
    assert (row.tokens_in, row.tokens_out, row.cost_usd) == (2000, 100, Decimal("0.025000"))
    assert (row.role, row.capability, row.stop_reason) == (
        "researcher",
        "research_extraction",
        "tool_use",
    )


async def test_script_is_consumed_in_order_per_attempt(committed, ctx):
    fake = FakeModelProvider()
    fake.script("researcher", "research", 1, FakeTurn(text="first"), FakeTurn(text="second"))
    fake.script("researcher", "research", 2, FakeTurn(text="retry"))
    gateway = _gateway(committed, fake)

    assert (await gateway.complete(_request(ctx))).text == "first"
    assert (await gateway.complete(_request(ctx))).text == "second"
    retry_ctx = ctx.model_copy(update={"attempt": 2})
    assert (await gateway.complete(_request(retry_ctx))).text == "retry"

    with pytest.raises(ModelCallFailed, match="no scripted reply #3"):
        await gateway.complete(_request(ctx))


async def test_structured_output_parsed_or_reported(committed, ctx):
    fake = FakeModelProvider()
    fake.script(
        "researcher", "research", 1,
        FakeTurn(structured={"story": "EU AI Act", "sources": ["https://example.eu/a"]}),
        FakeTurn(text='```json\n{"story": "fenced", "sources": ["s"]}\n```'),
        FakeTurn(structured={"story": "no sources", "sources": []}),
        FakeTurn(text="Sure! Here is the note."),
    )  # fmt: skip
    gateway = _gateway(committed, fake)
    request = _request(ctx, output_model=ResearchNote)

    ok = await gateway.complete(request)
    assert ok.parsed == ResearchNote(story="EU AI Act", sources=["https://example.eu/a"])
    assert ok.output_issues == []

    fenced = await gateway.complete(request)
    assert fenced.parsed.story == "fenced"

    invalid = await gateway.complete(request)
    assert invalid.parsed is None
    assert invalid.output_issues and invalid.output_issues[0].startswith("sources:")

    prose = await gateway.complete(request)
    assert prose.parsed is None and "not valid JSON" in prose.output_issues[0]


async def test_provider_outage_falls_back_and_records_both_calls(committed, ctx):
    fake = FakeModelProvider()
    fake.script(
        "researcher", "research", 1,
        FakeTurn(error="Overloaded", fallback_allowed=True),
        FakeTurn(text="from fallback"),
    )  # fmt: skip
    router = ModelRouter(
        aliases={
            "frontier": _binding(model_id="fake-big"),
            "fast": _binding(model_id="fake-small"),
        },
        routes={"*.*": "frontier"},
        fallbacks={"frontier": ["fast"]},
    )
    response = await _gateway(committed, fake, router).complete(_request(ctx))

    assert (response.text, response.alias, response.model_id) == (
        "from fallback",
        "fast",
        "fake-small",
    )
    rows = await _calls(committed, ctx.run_id)
    assert [(r.alias, r.status) for r in rows] == [("frontier", "error"), ("fast", "ok")]
    assert rows[0].error == {"error_class": "Overloaded", "message": "scripted failure"}


async def test_non_fallback_error_raises_after_recording(committed, ctx):
    fake = FakeModelProvider()
    fake.script(
        "researcher", "research", 1, FakeTurn(error="InvalidRequest", fallback_allowed=False)
    )
    router = ModelRouter(
        aliases={"frontier": _binding(), "fast": _binding(model_id="fake-small")},
        routes={"*.*": "frontier"},
        fallbacks={"frontier": ["fast"]},
    )
    with pytest.raises(ModelCallFailed) as exc:
        await _gateway(committed, fake, router).complete(_request(ctx))
    assert exc.value.last.error_class == "InvalidRequest"
    assert [(r.alias, r.status) for r in await _calls(committed, ctx.run_id)] == [
        ("frontier", "error")
    ]


async def test_missing_provider_is_a_config_error(committed, ctx):
    router = ModelRouter(
        aliases={"frontier": _binding(provider="nowhere")}, routes={"*.*": "frontier"}
    )
    with pytest.raises(ModelConfigError, match="not installed"):
        await _gateway(committed, FakeModelProvider(), router).complete(_request(ctx))


async def test_model_calls_are_append_only(db_session):
    run = await running_agent_run(db_session, "gw-ledger")
    call = ModelCall(
        company_id=run.company_id, run_id=run.id, role="r", capability="reasoning",
        alias="a", provider="fake", model_id="m", status="ok", cost_usd=Decimal("0.01"),
    )  # fmt: skip
    db_session.add(call)
    await db_session.flush()
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            text("UPDATE model_calls SET cost_usd = 0 WHERE id = :id"), {"id": call.id}
        )
    await db_session.rollback()
