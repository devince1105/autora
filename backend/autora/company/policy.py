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


def _max_workflows(args: Mapping[str, Any], facts: Mapping[str, Any], policies) -> str | None:
    cap = int(policies.get("company.max_workflows_per_cycle", DEFAULT_MAX_WORKFLOWS_PER_CYCLE))
    started = int(facts.get("workflows_in_cycle", 0))
    return None if started < cap else f"{started} workflows already started this cycle (cap {cap})"


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


ACTIONS = {
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
}

RULES: list[Rule] = [
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
    # record_transaction, delete, resume_agent: no agent rules -> denied; humans only.
]


def register(engine: PolicyEngine) -> None:
    for action, side_effect in ACTIONS.items():
        engine.declare(action, side_effect)
    engine.add(RULES)
