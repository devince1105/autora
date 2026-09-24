"""T-205: policy engine and the permission matrix (logs/platform/07_PERMISSION_MODEL.md §3)."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from autora.app import build_policy_engine
from autora.db.models import EventRecord, PolicyDecision
from autora.runtime.actor import Actor
from autora.runtime.policy import OVERRIDES_KEY, PolicyEngine, PolicyError, Rule, allow
from tests.conftest import unique_company

A, H, D = "allow", "needs_approval", "deny"
ROLES = (
    "researcher",
    "analyst",
    "writer",
    "editor",
    "marketing",
    "editor_in_chief",
    "ceo",
    "finance",
    "strategist",
    "business",
)

# One row per action, one column per role, transcribed from platform/07 §3 (P5 finance incl.).
# Limits are evaluated with facts/args that satisfy them; limit behaviour is tested separately.
MATRIX = {
    "web_search":           (A, D, D, D, A, D, D, D, D, A),
    "fetch_url":            (A, D, D, D, A, D, D, D, D, A),
    "compare_13f":          (A, D, D, D, D, D, D, D, D, D),  # D-037
    "read_evidence":        (A, A, A, A, A, A, A, D, D, A),
    "search_evidence":      (A, A, A, A, A, A, A, D, D, D),
    "create_claim":         (D, A, D, D, D, D, D, D, D, D),
    "link_evidence":        (D, A, D, D, D, D, D, D, D, D),
    "list_claims":          (A, A, A, A, A, A, A, D, D, D),
    "write_draft":          (D, D, A, D, D, D, D, D, D, D),
    "read_draft":           (D, D, A, A, A, D, A, D, D, D),
    "run_fact_check":       (D, D, D, A, D, D, D, D, D, D),
    "request_revision":     (D, D, D, A, D, D, D, D, D, D),
    "accept_draft":         (D, D, D, A, D, D, D, D, D, D),
    "approve_article":      (D, D, D, D, D, D, D, D, D, D),
    "publish_article":      (D, D, D, D, D, D, D, D, D, D),
    "create_distribution":  (D, D, D, D, A, D, D, D, D, D),
    "spend_ad_budget":      (D, D, D, D, A, D, D, D, D, D),
    "create_cycle_goal":    (D, D, D, D, D, D, A, D, D, D),
    "instantiate_workflow": (D, D, D, D, D, A, A, D, D, D),
    "create_project":       (D, D, D, D, D, D, H, D, D, D),
    "allocate_budget":      (D, D, D, D, D, D, A, H, D, D),
    "pause_project":        (D, D, D, D, D, D, A, D, D, D),
    "kill_project":         (D, D, D, D, D, D, H, D, D, D),
    "update_strategy":      (D, D, D, D, D, D, H, D, D, D),
    "record_transaction":   (D, D, D, D, D, D, D, D, D, D),
    "payment":              (D, D, D, D, D, D, H, H, D, D),
    "delete":               (D, D, D, D, D, D, D, D, D, D),
    "pause_agent":          (D, D, D, D, D, D, D, D, D, D),
    "resume_agent":         (D, D, D, D, D, D, D, D, D, D),
    # The desk head (T-605b): it commissions stories and starts the work it commissions, under
    # the company's own cap on workflows per cycle.
    "commission_story":     (D, D, D, D, D, A, D, D, D, D),
    # The executive's one tool. Allowing it is not allowing what it asks for: every command is
    # decided again on its own action (T-605a). The finance officer's too (T-705): what it asks
    # for is an allocation, and that goes to a person (allocate_budget, finance: H above). The
    # business agent's too (T-706): what it may ask for is below.
    "submit_command":       (D, D, D, D, D, D, A, A, A, A),
    # Echo domain (T-213), not in platform/07: each echo desk writes its own note.
    "echo_note":            (A, A, A, D, D, D, D, D, D, D),
    # The business loop (T-611, ARCHITECTURE_V2_1 §5-§6). The CEO looks, scores, validates and
    # walks away on its own; a person decides what is irreversible or spends real capital.
    "allocate_exploration_budget": (D, D, D, D, D, D, A, D, D, D),
    "score_opportunity":           (D, D, D, D, D, D, A, D, D, D),
    "advance_opportunity":         (D, D, D, D, D, D, A, D, D, D),
    "reject_opportunity":          (D, D, D, D, D, D, A, D, D, D),
    "draft_proposal":              (D, D, D, D, D, D, A, D, A, D),
    "submit_proposal":             (D, D, D, D, D, D, A, D, A, D),
    "create_business_unit":        (D, D, D, D, D, D, H, D, D, D),
    "scale_business_unit":         (D, D, D, D, D, D, A, D, D, D),
    "pause_business_unit":         (D, D, D, D, D, D, A, D, D, D),
    "wind_down_business_unit":     (D, D, D, D, D, D, H, D, D, D),
    # The business agent (T-706) writes down what it noticed and does nothing else in the loop;
    # it reads the web with the newsroom's tools, lent to it by the composition root (above).
    "discover_opportunity":        (D, D, D, D, D, D, D, D, D, A),
    "record_opportunity_signal":   (D, D, D, D, D, D, D, D, D, A),
}  # fmt: skip

WITHIN_LIMITS = {
    "args": {"amount": "1", "to_state": "EVALUATING"},
    "facts": {
        "workflows_in_cycle": 0,
        "article_state": "PUBLISHED",
        "campaign_spent": 0,
        "campaign_cap": 100,
        "fact_check_passed": True,
    },
}


@pytest.fixture(scope="module")
def engine() -> PolicyEngine:
    return build_policy_engine()


def _agent():
    return Actor.agent(uuid.uuid4())


def test_matrix_covers_every_declared_action(engine):
    assert set(MATRIX) == set(engine.actions())


@pytest.mark.parametrize(
    ("action", "role", "expected"),
    [(action, role, row[i]) for action, row in MATRIX.items() for i, role in enumerate(ROLES)],
)
def test_permission_matrix(engine, action, role, expected):
    decision = engine.decide(_agent(), action, role=role, **WITHIN_LIMITS)
    assert decision.outcome == expected, decision.reason


@pytest.mark.parametrize("action", sorted(MATRIX))
def test_humans_may_do_anything(engine, action):
    decision = engine.decide(Actor.human("operator"), action)
    assert (decision.outcome, decision.rule_id) == ("allow", "human_operator")


def test_unknown_action_and_unknown_role_are_denied(engine):
    assert engine.decide(_agent(), "teleport", role="ceo").rule_id == "unknown_action"
    decision = engine.decide(_agent(), "web_search", role="intern")
    assert (decision.outcome, decision.rule_id) == ("deny", "default_deny")


def test_agent_needs_a_role(engine):
    with pytest.raises(PolicyError, match="role"):
        engine.decide(_agent(), "web_search")


# --- system actor & D-001 ------------------------------------------------------------------


def test_system_publishes_but_does_not_approve_by_default(engine):
    system = Actor.system("publisher")
    assert engine.decide(system, "publish_article").outcome == "allow"
    decision = engine.decide(system, "approve_article", facts={"fact_check_passed": True})
    assert decision.outcome == "needs_approval"
    assert "D-001" in decision.reason


def test_auto_approval_needs_policy_and_passed_fact_check(engine):
    system = Actor.system("editor_pipeline")
    policies = {"newsroom.auto_approve_if_fact_check_passed": True}
    passed = engine.decide(
        system, "approve_article", facts={"fact_check_passed": True}, company_policies=policies
    )
    assert passed.outcome == "allow"
    failed = engine.decide(
        system, "approve_article", facts={"fact_check_passed": False}, company_policies=policies
    )
    assert failed.outcome == "needs_approval"


def test_system_is_not_an_agent_wildcard(engine):
    wide = PolicyEngine()
    wide.declare("summarize", "read")
    wide.add(allow("summarize", "*"))
    assert wide.decide(_agent(), "summarize", role="writer").outcome == "allow"
    assert wide.decide(Actor.system("x"), "summarize").outcome == "deny"


# --- limits ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "role", "args", "facts", "policies", "expected"),
    [
        ("instantiate_workflow", "ceo", {}, {"workflows_in_cycle": 5}, {}, "deny"),
        ("instantiate_workflow", "ceo", {}, {"workflows_in_cycle": 5},
         {"company.max_workflows_per_cycle": 8}, "allow"),
        # the default limit is NT$160 (D-023; USD 5 before)
        ("allocate_budget", "ceo", {"amount": "160"}, {}, {}, "allow"),
        ("allocate_budget", "ceo", {"amount": "160.01"}, {}, {}, "needs_approval"),
        ("allocate_budget", "ceo", {"amount": "50"}, {},
         {"governance.ceo_budget_allocation_limit": 100}, "allow"),
        ("allocate_budget", "ceo", {}, {}, {}, "needs_approval"),
        ("create_distribution", "marketing", {}, {"article_state": "APPROVED"}, {}, "deny"),
        ("spend_ad_budget", "marketing", {"amount": 60},
         {"campaign_spent": 50, "campaign_cap": 100}, {}, "needs_approval"),
    ],
)  # fmt: skip
def test_limits(engine, action, role, args, facts, policies, expected):
    decision = engine.decide(
        _agent(), action, role=role, args=args, facts=facts, company_policies=policies
    )
    assert decision.outcome == expected, decision.reason


# --- tightening only ---------------------------------------------------------------------


def test_company_policy_can_tighten(engine):
    policies = {OVERRIDES_KEY: {"web_search": {"researcher": "needs_approval"}}}
    decision = engine.decide(_agent(), "web_search", role="researcher", company_policies=policies)
    assert decision.outcome == "needs_approval" and "tightened" in decision.reason


def test_company_policy_cannot_loosen(engine):
    policies = {
        OVERRIDES_KEY: {
            "create_project": {"ceo": "allow"},
            "write_draft": {"researcher": "allow"},
            "payment": {"ceo": "allow"},
        }
    }
    for action, role, expected in [
        ("create_project", "ceo", "needs_approval"),
        ("write_draft", "researcher", "deny"),
        ("payment", "ceo", "needs_approval"),
    ]:
        decision = engine.decide(_agent(), action, role=role, company_policies=policies)
        assert decision.outcome == expected
    loosen = engine.decide(_agent(), "create_project", role="ceo", company_policies=policies)
    assert "can only tighten" in loosen.reason


def test_irreversible_actions_always_need_a_human():
    engine = PolicyEngine()
    engine.declare("wire_money", "irreversible")
    engine.add([Rule("wire_money", "finance", "allow"), Rule("wire_money", "system", "allow")])
    assert engine.decide(_agent(), "wire_money", role="finance").outcome == "needs_approval"
    assert engine.decide(Actor.system("x"), "wire_money").outcome == "needs_approval"
    assert engine.decide(Actor.human("op"), "wire_money").outcome == "allow"


# --- registration ------------------------------------------------------------------------


def test_registration_errors():
    engine = PolicyEngine()
    engine.declare("act", "read")
    with pytest.raises(PolicyError, match="already declared"):
        engine.declare("act", "write")
    with pytest.raises(PolicyError, match="undeclared"):
        engine.add(allow("other", "ceo"))
    engine.add(allow("act", "ceo"))
    with pytest.raises(PolicyError, match="duplicate"):
        engine.add(allow("act", "ceo"))
    with pytest.raises(PolicyError, match="humans"):
        engine.add(allow("act", "human"))
    with pytest.raises(PolicyError, match="lower_snake_case"):
        engine.declare("Bad Action", "read")


# --- audit -------------------------------------------------------------------------------


async def test_decisions_are_recorded_and_denials_emit_event(db_session, engine):
    company = await unique_company(db_session, "policy")
    agent = _agent()

    allowed = await engine.decide_and_record(
        db_session, agent, "web_search", company_id=company.id, role="researcher",
        args={"query": "EU AI Act"},
    )  # fmt: skip
    denied = await engine.decide_and_record(
        db_session, agent, "publish_article", company_id=company.id, role="writer"
    )
    assert (allowed.outcome, denied.outcome) == ("allow", "deny")

    rows = (
        await db_session.scalars(
            select(PolicyDecision).where(PolicyDecision.company_id == company.id)
        )
    ).all()
    assert [(r.action, r.outcome, r.role) for r in rows] == [
        ("web_search", "allow", "researcher"),
        ("publish_article", "deny", "writer"),
    ]
    assert rows[0].actor == agent.as_json() and len(rows[0].args_hash) == 64

    events = (
        await db_session.scalars(
            select(EventRecord.payload).where(
                EventRecord.company_id == company.id, EventRecord.event_type == "POLICY_DENIED"
            )
        )
    ).all()
    assert [e["action"] for e in events] == ["publish_article"]
    assert events[0]["rule_id"] == "default_deny"

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            text("UPDATE policy_decisions SET outcome = 'allow' WHERE id = :id"),
            {"id": rows[1].id},
        )
    await db_session.rollback()
