"""Policy engine: decides whether an actor may perform an action (T-205, logs/platform/07).

Outcomes are ``allow``, ``deny`` or ``needs_approval`` (a human must approve first). Rules are
registered by the layer that owns the action: company commands by ``autora.company``, domain
tools by each domain. The engine itself only knows the fixed principles:

1. **Humans (operators) may do anything.** Their actions are still recorded.
2. **Everything not explicitly allowed is denied** (unknown action, unknown role, no rule).
3. **Irreversible actions always need a human.** An agent or system rule that says ``allow`` is
   raised to ``needs_approval``; nothing can loosen this.
4. **Companies can only tighten.** ``company_policies["policy.overrides"]`` =
   ``{"<action>": {"<role>": "deny" | "needs_approval"}}``. An override looser than the rule is
   ignored (and the decision says so), so a policy change can never widen what an agent may do.
5. **Limits.** A rule may carry a check (quota, state precondition) evaluated against the call's
   args, caller-supplied facts and company policies. Exceeding it yields the rule's ``over``
   outcome (``needs_approval`` or ``deny``).

``decide`` is pure. ``decide_and_record`` also writes ``policy_decisions`` (audit) and emits
POLICY_DENIED for denials.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import PolicyDecision
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event

Outcome = Literal["allow", "needs_approval", "deny"]
SideEffect = Literal["read", "write", "irreversible"]

_STRICTNESS: dict[str, int] = {"allow": 0, "needs_approval": 1, "deny": 2}
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
ANY_AGENT = "*"
OVERRIDES_KEY = "policy.overrides"

LimitCheck = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], str | None]
"""(args, facts, company_policies) -> None when within the limit, else the reason it is not."""


class PolicyError(Exception):
    pass


@dataclass(frozen=True)
class Limit:
    check: LimitCheck
    over: Outcome
    description: str


@dataclass(frozen=True)
class Rule:
    action: str
    role: str
    """An agent role, ``system``, or ``*`` for any agent role (not system, not human)."""
    outcome: Outcome
    limit: Limit | None = None

    @property
    def rule_id(self) -> str:
        return f"{self.action}:{self.role}"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    rule_id: str
    reason: str
    action: str
    role: str | None

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


@dataclass
class PolicyEngine:
    _actions: dict[str, SideEffect] = field(default_factory=dict)
    _rules: dict[tuple[str, str], Rule] = field(default_factory=dict)

    # --- registration ----------------------------------------------------------------------

    def declare(self, action: str, side_effect: SideEffect) -> None:
        if not _IDENT.match(action):
            raise PolicyError(f"action must be lower_snake_case: {action!r}")
        existing = self._actions.get(action)
        if existing is not None and existing != side_effect:
            raise PolicyError(f"{action} already declared as {existing}")
        self._actions[action] = side_effect

    def add(self, rules: Iterable[Rule]) -> None:
        for rule in rules:
            if rule.action not in self._actions:
                raise PolicyError(f"rule for undeclared action {rule.action!r}")
            if rule.role != ANY_AGENT and not _IDENT.match(rule.role):
                raise PolicyError(f"invalid role {rule.role!r}")
            if rule.role == "human":
                raise PolicyError("humans are always allowed; do not write rules for them")
            key = (rule.action, rule.role)
            if key in self._rules:
                raise PolicyError(f"duplicate rule {rule.rule_id}")
            self._rules[key] = rule

    def actions(self) -> dict[str, SideEffect]:
        return dict(self._actions)

    # --- decisions -------------------------------------------------------------------------

    def decide(
        self,
        actor: Actor,
        action: str,
        *,
        role: str | None = None,
        args: Mapping[str, Any] | None = None,
        facts: Mapping[str, Any] | None = None,
        company_policies: Mapping[str, Any] | None = None,
    ) -> Decision:
        """``role`` is the agent's role for agent actors; ignored for humans and system."""
        args, facts, policies = args or {}, facts or {}, company_policies or {}

        if actor.kind == "human":
            return Decision("allow", "human_operator", "operators may do anything", action, "human")
        if action not in self._actions:
            return Decision(
                "deny", "unknown_action", f"{action!r} is not a known action", action, role
            )

        role = "system" if actor.kind == "system" else role
        if role is None:
            raise PolicyError("agent decisions need the agent's role")
        rule = self._rules.get((action, role))
        if rule is None and actor.kind == "agent":
            rule = self._rules.get((action, ANY_AGENT))
        if rule is None:
            return Decision("deny", "default_deny", f"no rule lets {role} {action}", action, role)

        outcome: Outcome = rule.outcome
        reasons = [f"rule {rule.rule_id}: {rule.outcome}"]
        if rule.limit is not None and outcome == "allow":
            violation = rule.limit.check(args, facts, policies)
            if violation is not None:
                outcome = rule.limit.over
                reasons.append(f"limit ({rule.limit.description}) exceeded: {violation}")

        if self._actions[action] == "irreversible" and outcome == "allow":
            outcome = "needs_approval"
            reasons.append("irreversible actions always need a human")

        override = ((policies.get(OVERRIDES_KEY) or {}).get(action) or {}).get(role)
        if override is not None:
            if override not in _STRICTNESS:
                reasons.append(f"ignored invalid company override {override!r}")
            elif _STRICTNESS[override] > _STRICTNESS[outcome]:
                outcome = override
                reasons.append(f"tightened by company policy to {override}")
            elif _STRICTNESS[override] < _STRICTNESS[outcome]:
                reasons.append(f"ignored company override {override!r}: policies can only tighten")

        return Decision(outcome, rule.rule_id, "; ".join(reasons), action, role)

    async def decide_and_record(
        self,
        session: AsyncSession,
        actor: Actor,
        action: str,
        *,
        company_id: uuid.UUID,
        role: str | None = None,
        args: Mapping[str, Any] | None = None,
        facts: Mapping[str, Any] | None = None,
        company_policies: Mapping[str, Any] | None = None,
        agent_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ) -> Decision:
        decision = self.decide(
            actor, action, role=role, args=args, facts=facts, company_policies=company_policies
        )
        session.add(
            PolicyDecision(
                company_id=company_id,
                actor=actor.as_json(),
                role=decision.role,
                action=action,
                args_hash=args_hash(args or {}),
                outcome=decision.outcome,
                rule_id=decision.rule_id,
                reason=decision.reason[:2000],
                agent_id=agent_id,
                run_id=run_id,
                task_id=task_id,
            )
        )
        await session.flush()
        if decision.outcome == "deny":
            await emit(
                session,
                new_event(
                    ev.PolicyDenied(
                        action=action, rule_id=decision.rule_id, detail=decision.reason
                    ),
                    company_id=company_id,
                    actor=actor,
                    aggregate_type="agent_run" if run_id else "policy",
                    aggregate_id=run_id or company_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    task_id=task_id,
                ),
            )
        return decision


def args_hash(args: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def allow(action: str, *roles: str, limit: Limit | None = None) -> list[Rule]:
    return [Rule(action, role, "allow", limit) for role in roles]


def needs_approval(action: str, *roles: str) -> list[Rule]:
    return [Rule(action, role, "needs_approval") for role in roles]
