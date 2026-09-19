"""Permission rules for newsroom tools and actions (logs/platform/07_PERMISSION_MODEL.md §3).

D-001: publishing needs a human approval by default. ``approve_article`` by the system (automatic
approval after a passed fact-check) is only allowed when the company policy
``newsroom.auto_approve_if_fact_check_passed`` is true; otherwise it needs a human.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from autora.runtime.policy import Limit, PolicyEngine, Rule, allow

AUTO_APPROVE_KEY = "newsroom.auto_approve_if_fact_check_passed"

# D-002: which languages articles are written and published in (company policy, not code)
PRIMARY_LANG_KEY = "newsroom.primary_lang"
LANGS_KEY = "newsroom.langs"
REQUIRE_ALL_LANGS_KEY = "newsroom.require_all_langs"
LANGUAGE_DEFAULTS = {
    PRIMARY_LANG_KEY: "zh-TW",
    LANGS_KEY: ["zh-TW", "en"],
    REQUIRE_ALL_LANGS_KEY: True,
}


@dataclass(frozen=True)
class LanguagePolicy:
    primary: str
    langs: tuple[str, ...]
    require_all: bool


def language_policy(policies: Mapping[str, Any]) -> LanguagePolicy:
    """The company's language rules (D-002), defaults filled in; the primary is always a
    language."""
    primary = policies.get(PRIMARY_LANG_KEY) or LANGUAGE_DEFAULTS[PRIMARY_LANG_KEY]
    langs = list(policies.get(LANGS_KEY) or LANGUAGE_DEFAULTS[LANGS_KEY])
    if primary not in langs:
        langs.insert(0, primary)
    require_all = policies.get(REQUIRE_ALL_LANGS_KEY)
    return LanguagePolicy(
        primary=primary,
        langs=tuple(langs),
        require_all=LANGUAGE_DEFAULTS[REQUIRE_ALL_LANGS_KEY]
        if require_all is None
        else bool(require_all),
    )


WRITERS_AND_READERS = ("researcher", "analyst", "writer", "editor", "marketing", "ceo")


def _auto_approve_enabled(args, facts, policies: Mapping[str, Any]) -> str | None:
    if policies.get(AUTO_APPROVE_KEY) is True:
        if facts.get("fact_check_passed") is True:
            return None
        return "fact-check has not passed"
    return f"{AUTO_APPROVE_KEY} is off (D-001: a human approves publication)"


def _article_published(args, facts, policies) -> str | None:
    state = facts.get("article_state")
    return None if state == "PUBLISHED" else f"article is {state or 'unknown'}, not PUBLISHED"


def _within_campaign_cap(args, facts, policies) -> str | None:
    try:
        spent, amount, cap = (float(facts["campaign_spent"]), float(args["amount"]),
                              float(facts["campaign_cap"]))  # fmt: skip
    except (KeyError, TypeError, ValueError):
        return "campaign cap or amount unknown"
    return None if spent + amount <= cap else f"{spent + amount} exceeds campaign cap {cap}"


ACTIONS = {
    "web_search": "read",
    "fetch_url": "write",  # stores an evidence snapshot
    "read_evidence": "read",
    "search_evidence": "read",
    "create_claim": "write",
    "link_evidence": "write",
    "list_claims": "read",
    "write_draft": "write",
    "read_draft": "read",
    "run_fact_check": "write",
    "request_revision": "write",
    "accept_draft": "write",
    "approve_article": "write",
    "publish_article": "write",
    "create_distribution": "write",
    "spend_ad_budget": "write",
}

RULES: list[Rule] = [
    *allow("web_search", "researcher", "marketing"),
    *allow("fetch_url", "researcher", "marketing"),
    *allow("read_evidence", *WRITERS_AND_READERS),
    *allow("search_evidence", *WRITERS_AND_READERS),
    *allow("create_claim", "analyst"),
    *allow("link_evidence", "analyst"),
    *allow("list_claims", *WRITERS_AND_READERS),
    *allow("write_draft", "writer"),
    *allow("read_draft", "writer", "editor", "marketing", "ceo"),
    *allow("run_fact_check", "editor"),
    *allow("request_revision", "editor"),
    *allow("accept_draft", "editor"),
    *allow(
        "approve_article",
        "system",
        limit=Limit(_auto_approve_enabled, over="needs_approval", description="auto-approval"),
    ),
    *allow("publish_article", "system"),  # the Publisher service, after APPROVED
    *allow(
        "create_distribution",
        "marketing",
        limit=Limit(_article_published, over="deny", description="published articles only"),
    ),
    *allow(
        "spend_ad_budget",
        "marketing",
        limit=Limit(_within_campaign_cap, over="needs_approval", description="campaign cap"),
    ),
]


def register(engine: PolicyEngine) -> None:
    for action, side_effect in ACTIONS.items():
        engine.declare(action, side_effect)
    engine.add(RULES)
