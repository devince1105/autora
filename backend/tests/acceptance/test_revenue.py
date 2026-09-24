"""Phase 7 acceptance (T-708): revenue reaches the company, and the company's spending stays gated.

``platform/13`` §2 asks for two things:

1. **A real payment, written by the webhook, shows on the Dashboard and in the cycle's review.**
   A reader signs in, checks out, and PAYUNi's notification — sealed with the store's secrets,
   exactly as PAYUNi seals it — is posted to the real endpoint. Then the day runs, in simulation,
   through the real worker: the ledger settles, reporting measures, the finance officer reads the
   books, the CEO reviews. The payment must be on the dashboard, in the cycle the operator reads,
   and in the document the CEO reviewed the cycle from.

   "Real" has one limit here, said plainly: PAYUNi's servers are not contacted. The sandbox store
   is not yet able to take an order (T-702); what this proves is everything on our side of the
   notification, which is everything that decides whether a payment counts.

2. **Marketing cannot spend past its cap.** T-703 (paid campaigns) is deferred by D-022: the
   company runs no ads. The strongest form of "stopped at the cap" is that there is no way to
   spend at all — and that the rule waiting for the day there is, sends anything over it to a
   person.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from pydantic import SecretStr
from sqlalchemy import select

from autora.app import (
    build_behaviors,
    build_policy_engine,
    build_runtime,
    build_tools,
    build_worker,
)
from autora.company import memberships
from autora.company import simulation as company_simulation
from autora.company.agents import hire_agent
from autora.company.companies import create_company
from autora.company.cycle import CYCLE_START_SCHEDULE
from autora.company.organization import (
    FINANCE_ROLE,
    add_business_unit,
    add_product,
    bootstrap_executive,
    role_by_key,
)
from autora.db.models import (
    BusinessUnitState,
    Cycle,
    CycleStage,
    Payment,
    ProductState,
    Schedule,
    Transaction,
    TransactionKind,
)
from autora.domains.newsroom.agents.marketing import TASK as DISTRIBUTE
from autora.infra.email import ConsoleSender
from autora.infra.payments import payuni
from autora.runtime.actor import Actor

API_DIR = Path(__file__).resolve().parents[2] / "api"
sys.path.insert(0, str(API_DIR))

from autora_api.app import create_app  # noqa: E402
from autora_api.deps import get_session, runtime_dep, sender_dep, settings_dep  # noqa: E402

OPERATOR = Actor.human("acceptance-operator")
TOKEN = "acceptance-operator-token"
MER_ID, KEY, IV = "TESTSHOP", "0123456789abcdef0123456789abcdef", "0123456789abcdef"
"""This test's store. Not anybody's: the real ids live in a developer's .env, never here."""
PRICE = Decimal("360")


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> datetime:
        self.now += timedelta(**delta)
        return self.now


async def _drive(worker, clock: Clock, *, hours: int, step_minutes: int = 20) -> None:
    for _ in range(int(hours * 60 / step_minutes)):
        await worker.run_until_idle()
        clock.advance(minutes=step_minutes)


async def _company(committed) -> tuple[uuid.UUID, str]:
    """A company with a CEO, a finance officer, and a year of membership for sale."""
    slug = f"rev-{uuid.uuid4().hex[:8]}"
    async with committed() as session:
        company, _ = await create_company(
            session, slug=slug, name="Revenue Co", mission="sell a year of reading",
            actor=OPERATOR,
        )  # fmt: skip
        # the test opens the day itself, so the payment can land inside it (see below)
        schedule = await session.scalar(
            select(Schedule).where(
                Schedule.company_id == company.id, Schedule.name == CYCLE_START_SCHEDULE
            )
        )
        schedule.enabled = False
        _, ceo_role = await bootstrap_executive(session, company.id, actor=OPERATOR)
        await hire_agent(
            session, company_id=company.id, role=ceo_role.key, display_name="Cyra",
            actor=OPERATOR, position=ceo_role,
        )  # fmt: skip
        await hire_agent(
            session, company_id=company.id, role=FINANCE_ROLE, display_name="Fen",
            actor=OPERATOR, position=await role_by_key(session, company.id, FINANCE_ROLE),
        )  # fmt: skip
        unit = await add_business_unit(
            session, company_id=company.id, key="ai_media", name="AI Media",
            actor=OPERATOR, state=BusinessUnitState.ACTIVE,
        )  # fmt: skip
        product = await add_product(
            session, company_id=company.id, key=memberships.PRODUCT_KEY, name="Membership",
            business_unit_id=unit.id, actor=OPERATOR, state=ProductState.LIVE,
        )  # fmt: skip
        await memberships.add_price(session, product, amount=PRICE)
        await session.commit()
        return company.id, slug


def _app(committed, settings, runtime, mailbox) -> httpx.AsyncClient:
    """The real app, on real commits: what it writes, the worker must be able to read."""
    app = create_app()

    async def _session():
        async with committed() as session:
            yield session

    configured = settings.model_copy(
        update={
            "api_bearer_token": SecretStr(TOKEN),
            "payuni_mer_id": MER_ID,
            "payuni_hash_key": SecretStr(KEY),
            "payuni_hash_iv": SecretStr(IV),
        }
    )
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[settings_dep] = lambda: configured
    app.dependency_overrides[runtime_dep] = lambda: runtime
    app.dependency_overrides[sender_dep] = lambda: mailbox
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


async def _buy_a_year(client: httpx.AsyncClient, mailbox: ConsoleSender, slug: str) -> str:
    """A reader signs in, checks out, and PAYUNi says the money arrived. Returns PAYUNi's number."""
    # an address of its own: this test commits, and readers are shared by every company
    address = f"reader-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/api/auth/link", json={"email": address, "company": slug})
    token = re.search(r"token=([A-Za-z0-9_\-]+)", mailbox.sent[-1].text).group(1)
    assert (await client.post("/api/auth/verify", json={"token": token})).status_code == 200
    checkout = await client.post("/api/checkout", json={"company": slug})
    assert checkout.status_code == 201, checkout.text
    trade_no = f"UNI{uuid.uuid4().hex[:12]}"
    sealed = payuni.seal(
        {
            "Status": payuni.SUCCEEDED, "Message": "交易成功", "MerID": MER_ID,
            "MerTradeNo": checkout.json()["mer_trade_no"], "TradeNo": trade_no,
            "TradeAmt": str(PRICE), "TradeStatus": payuni.TRADE_PAID, "PaymentType": "1",
        },
        key=KEY, iv=IV,
    )  # fmt: skip
    notified = await client.post(
        "/api/payments/payuni/notify",
        data=sealed.as_form(MER_ID) | {"Status": payuni.SUCCEEDED},
    )
    assert (notified.status_code, notified.text) == (200, "1|OK")
    return trade_no


def _mentions(document: str, amount: Decimal) -> bool:
    """Whether a JSON document carries this amount as a number anywhere (360 / 360.000000)."""

    def walk(value) -> bool:
        if isinstance(value, dict):
            return any(walk(v) for v in value.values())
        if isinstance(value, list):
            return any(walk(v) for v in value)
        if isinstance(value, str | int | float):
            try:
                return Decimal(str(value)) == amount
            except ArithmeticError:
                return False
        return False

    return walk(json.loads(document))


async def test_a_payment_the_webhook_wrote_reaches_the_dashboard_and_the_review(
    committed, e2e_settings, monkeypatch
):
    # what the agents were handed, as the model saw it: the finance officer's review in
    # MEASURING and the CEO's in REVIEWING. Recorded, not changed: the answers are the script's.
    seen: dict[str, list[str]] = {}
    scripted = company_simulation.respond

    def recording(request):
        ctx = request.context
        if (ctx.role, ctx.task_name) in (("ceo", "review"), ("finance", "review_budgets")):
            text = company_simulation._texts(request.messages[0])  # noqa: SLF001
            seen.setdefault(ctx.task_name, []).append("\n".join(text))
        return scripted(request)

    monkeypatch.setattr(company_simulation, "respond", recording)

    company_id, slug = await _company(committed)
    # the day opens a minute ago by its own clock, and PAYUNi pays now by the real one, so the
    # payment is inside the day the worker then runs through
    clock = Clock(datetime.now(UTC) - timedelta(minutes=1))
    runtime = build_runtime(e2e_settings)
    runtime.cycles.clock = clock
    async with committed() as session:
        cycle = await runtime.cycles.start(session, company_id)
        await session.commit()
        cycle_id = cycle.id

    mailbox = ConsoleSender(echo=False)
    async with _app(committed, e2e_settings, runtime, mailbox) as client:
        trade_no = await _buy_a_year(client, mailbox, slug)

        worker = build_worker(
            e2e_settings, session_factory=committed, company_ids=frozenset({company_id}),
            runtime=runtime,
        )  # fmt: skip
        worker.clock = clock
        worker.scheduler.clock = clock
        worker.maintenance_interval = 1
        await _drive(worker, clock, hours=17)

        # --- the money is where the webhook put it, once -------------------------------------
        async with committed() as session:
            payment = await session.scalar(select(Payment).where(Payment.company_id == company_id))
            revenue = (
                await session.scalars(
                    select(Transaction).where(
                        Transaction.company_id == company_id,
                        Transaction.kind == TransactionKind.REVENUE.value,
                    )
                )
            ).all()
            cycle = await session.get(Cycle, cycle_id)
        assert payment is not None and payment.external_ref == trade_no
        assert [t.amount for t in revenue] == [PRICE], "one payment, one revenue row"
        assert cycle.stage == CycleStage.DONE.value, f"the day did not finish: {cycle.stage}"

        # --- 1. the dashboard -----------------------------------------------------------------
        kpis = (await client.get(f"/api/companies/{company_id}/kpis")).json()
        assert Decimal(kpis["revenue"]["total"]) == PRICE
        assert (kpis["revenue"]["payments"], kpis["revenue"]["new_members"]) == (1, 1)
        assert kpis["revenue"]["members"] == 1

        # --- 2. the cycle the operator reads: its review, and the numbers measured for it ------
        detail = (await client.get(f"/api/cycles/{cycle_id}")).json()
        assert detail["review"], "the CEO did not review the day"
        assert Decimal(str(detail["kpis"]["revenue"])) == PRICE
        assert detail["kpis"]["new_members"] == 1

    # --- 3. what the finance officer and the CEO reviewed the day from --------------------------
    assert seen.get("review_budgets"), "the finance officer was never asked"
    assert seen.get("review"), "the CEO never reviewed"
    for task, documents in seen.items():
        snapshot = documents[0].split("The company right now:", 1)[-1]
        document = json.loads(snapshot[: snapshot.rfind("}") + 1])
        measured = document.get("last_cycle", {})
        assert measured.get("seq") == 1, (
            f"{task} was shown cycle {measured.get('seq')}, not this one"
        )
        assert _mentions(json.dumps(measured.get("kpis", {})), PRICE), (
            f"{task} was not shown the day's revenue: {measured}"
        )


# --- 2. marketing cannot spend past its cap -----------------------------------------------------


def test_marketing_has_no_way_to_spend_and_the_rule_for_it_asks_a_person(committed, e2e_settings):
    """D-022 defers paid campaigns. Until they exist: nothing marketing can call spends money,
    and ``spend_ad_budget`` — declared, waiting — sends anything over the cap to a person."""
    marketing = build_behaviors().resolve("marketing", DISTRIBUTE)
    assert set(marketing.tools) == {"create_distribution", "read_draft"}
    registered = build_tools(committed, e2e_settings, policy=build_policy_engine()).names()
    assert not [name for name in registered if "spend" in name or "campaign" in name], registered

    engine = build_policy_engine()
    marketer = Actor.agent(uuid.uuid4())

    def spend(amount, spent="90", cap="100"):
        return engine.decide(
            marketer, "spend_ad_budget", role="marketing", args={"amount": amount},
            facts={"campaign_spent": spent, "campaign_cap": cap},
        ).outcome  # fmt: skip

    assert spend("10") == "allow"  # up to the cap
    assert spend("10.01") == "needs_approval"  # past it, a person decides
    assert (
        engine.decide(
            marketer, "spend_ad_budget", role="marketing", args={"amount": "1"}, facts={}
        ).outcome
        == "needs_approval"
    ), "a cap nobody knows is not a cap that holds"
