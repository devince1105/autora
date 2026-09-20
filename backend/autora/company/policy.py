"""Permission rules for company commands (logs/platform/07_PERMISSION_MODEL.md §3).

Anything not listed is denied. Humans are always allowed (engine principle), so the "Human"
column of the matrix needs no rules here.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from autora.runtime.policy import Limit, PolicyEngine, Rule, allow, needs_approval

DEFAULT_MAX_WORKFLOWS_PER_CYCLE = 5
DEFAULT_CEO_BUDGET_ALLOCATION_LIMIT_USD = Decimal("5")
DEFAULT_EXPLORATION_BUDGET_LIMIT_USD = Decimal("2")
"""What the CEO may spend finding out, per allocation, before a person is asked. Smaller than
the ordinary limit on purpose: exploring is meant to be cheap, and a company that can fund a
large exploration alone can fund a business by calling it one (ARCHITECTURE_V2_1 §6)."""
DEFAULT_CEO_SCALE_LIMIT_USD = Decimal("50")
"""Capital the CEO may move into a business it already runs, per command."""


def max_workflows(args: Mapping[str, Any], facts: Mapping[str, Any], policies) -> str | None:
    """How much work may still be started this cycle (anti-runaway gate 5).

    Public because a business's own desk head starts its own work and must be held to the same
    number: a newsroom that could define its own cap could give itself a bigger day.
    """
    cap = int(policies.get("company.max_workflows_per_cycle", DEFAULT_MAX_WORKFLOWS_PER_CYCLE))
    started = int(facts.get("workflows_in_cycle", 0))
    return None if started < cap else f"{started} workflows already started this cycle (cap {cap})"


_max_workflows = max_workflows  # the name the rule below was written with


def _allocation_limit(args: Mapping[str, Any], facts, policies) -> str | None:
    limit = Decimal(
        str(
            policies.get(
                "governance.ceo_budget_allocation_limit_usd",
                DEFAULT_CEO_BUDGET_ALLOCATION_LIMIT_USD,
            )
        )
    )
    try:
        amount = Decimal(str(args.get("amount")))
    except (InvalidOperation, TypeError):
        return "amount missing or not a number"
    return None if amount <= limit else f"${amount} is above the ${limit} limit"


def _exploration_limit(args: Mapping[str, Any], facts, policies) -> str | None:
    return _under(
        args,
        policies,
        key="governance.exploration_budget_limit_usd",
        default=DEFAULT_EXPLORATION_BUDGET_LIMIT_USD,
    )


def _scale_limit(args: Mapping[str, Any], facts, policies) -> str | None:
    return _under(
        args, policies, key="governance.ceo_scale_limit_usd", default=DEFAULT_CEO_SCALE_LIMIT_USD
    )


def _under(args: Mapping[str, Any], policies, *, key: str, default: Decimal) -> str | None:
    limit = Decimal(str(policies.get(key, default)))
    try:
        amount = abs(Decimal(str(args.get("amount"))))
    except (InvalidOperation, TypeError):
        return "amount missing or not a number"
    return None if amount <= limit else f"${amount} is above the ${limit} limit"


def _reversible_step(args: Mapping[str, Any], facts, policies) -> str | None:
    """Which opportunity states the CEO may reach on its own.

    Only one is a person's: APPROVED is the moment the company commits to a business
    (ARCHITECTURE_V2_1 §6). Evaluating and validating are cheap and reversible, and walking
    away is the CEO's too. Anything else is not a state at all, and the handler refuses it —
    nobody should be asked to approve a typo.
    """
    return (
        None
        if str(args.get("to_state", "")) != "APPROVED"
        else "approving an opportunity commits the company to a business"
    )


ACTIONS = {
    # The tool an executive agent uses to ask for anything. Allowing the tool is not allowing
    # what it asks for: every command is decided again, on its own action, by the pipeline.
    "submit_command": "write",
    "create_cycle_goal": "write",
    "instantiate_workflow": "write",
    "create_project": "write",
    "allocate_budget": "write",
    "pause_project": "write",
    "kill_project": "write",
    "update_strategy": "write",
    "record_transaction": "write",
    "payment": "irreversible",
    "delete": "irreversible",
    "pause_agent": "write",
    "resume_agent": "write",
    # the business loop (T-611)
    "allocate_exploration_budget": "write",
    "score_opportunity": "write",
    "advance_opportunity": "write",
    "reject_opportunity": "write",
    "create_business_unit": "irreversible",
    "scale_business_unit": "write",
    "pause_business_unit": "write",
    "wind_down_business_unit": "irreversible",
}

RULES: list[Rule] = [
    *allow("submit_command", "ceo"),
    *allow("create_cycle_goal", "ceo"),
    *allow(
        "instantiate_workflow",
        "ceo",
        limit=Limit(_max_workflows, over="deny", description="max workflows per cycle"),
    ),
    *needs_approval("create_project", "ceo"),
    *allow(
        "allocate_budget",
        "ceo",
        limit=Limit(
            _allocation_limit, over="needs_approval", description="CEO budget allocation limit"
        ),
    ),
    *needs_approval("allocate_budget", "finance"),
    *allow("pause_project", "ceo", "system"),  # system: kill-criteria auto-pause (governance)
    *needs_approval("kill_project", "ceo"),
    *needs_approval("update_strategy", "ceo"),
    *needs_approval("payment", "ceo", "finance"),
    *allow("pause_agent", "system"),  # governance pauses an agent that keeps failing
    # --- the business loop (ARCHITECTURE_V2_1 §5-§6) ------------------------------------------
    *allow(
        "allocate_exploration_budget",
        "ceo",
        limit=Limit(
            _exploration_limit, over="needs_approval", description="exploration budget limit"
        ),
    ),
    *allow("score_opportunity", "ceo"),  # a number to compare by; it decides nothing
    *allow(
        "advance_opportunity",
        "ceo",
        limit=Limit(
            _reversible_step, over="needs_approval", description="APPROVED is a person's call"
        ),
    ),
    *allow("reject_opportunity", "ceo"),  # saying no is cheap and is kept as a record
    *needs_approval("create_business_unit", "ceo"),  # real capital, a lasting organisation
    *allow(
        "scale_business_unit",
        "ceo",
        limit=Limit(_scale_limit, over="needs_approval", description="CEO capital scale limit"),
    ),
    *allow("pause_business_unit", "ceo", "system"),  # stopping the bleeding is not ending it
    *needs_approval("wind_down_business_unit", "ceo"),  # irreversible: only a person ends one
    # record_transaction, delete, resume_agent: no agent rules -> denied; humans only.
]


def register(engine: PolicyEngine) -> None:
    for action, side_effect in ACTIONS.items():
        engine.declare(action, side_effect)
    engine.add(RULES)
