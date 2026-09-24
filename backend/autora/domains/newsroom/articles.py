"""Articles and their versions (T-508, platform/05 §4, D-002).

A story has at most one article. The writer drafts it in every language at once: one call makes
one version per language, all in the same draft group with the same version number, all citing
the same claims. The checks (``check_draft``) run before anything is written and report every
problem at once, so a model can fix its draft in one go:

- languages follow the company policy (D-002): the primary language is required, every language
  must be one of the policy's, and with ``require_all_langs`` all of them are required;
- every paragraph and quote cites at least one claim, and headings cite none;
- cited claims belong to the story and were not rejected by fact-check;
- **every language cites the same set of claims** (the bilingual rule: the translations share
  one factual basis, so fact-check verifies the claims once);
- quote blocks stay short (no long reproduction of others' work, platform/05 §8).

The article's lifecycle is ``ARTICLE_FSM``; the writer may only draft while it is DRAFT (new, or
sent back for revision). Review, approval and publication move it on (T-511, T-512).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from autora.domains.newsroom.language import script_problems
from autora.domains.newsroom.models import ArticleState, ClaimStatus, StoryState
from autora.domains.newsroom.policy import LanguagePolicy
from autora.domains.newsroom.quotes import MAX_QUOTE
from autora.runtime.fsm import StateMachine, transitions

A = ArticleState
ARTICLE_FSM = StateMachine(
    entity_type="article",
    states=ArticleState,
    initial=A.DRAFT,
    transitions=transitions(
        {
            A.DRAFT: [A.IN_REVIEW],
            A.IN_REVIEW: [A.DRAFT, A.APPROVED, A.REJECTED],
            A.APPROVED: [A.PUBLISHED],
            A.PUBLISHED: [A.ARCHIVED],
            A.ARCHIVED: [A.PUBLISHED],  # put back on the site (D-044)
        }
    ),
)

CLOSED_STORIES = {StoryState.DROPPED, StoryState.IGNORED, StoryState.PUBLISHED}
MAX_BLOCKS = 60


class Block(BaseModel):
    type: Literal["heading", "paragraph", "quote"]
    text: str = Field(min_length=1, max_length=2000)
    claim_ids: list[uuid.UUID] = Field(
        default=[], max_length=20, description="The claims this block states (none for headings)."
    )


class LanguageVersion(BaseModel):
    lang: str = Field(min_length=2, max_length=10, description='A language code, e.g. "zh-TW".')
    title: str = Field(min_length=2, max_length=200)
    summary: str | None = Field(default=None, max_length=500)
    blocks: list[Block] = Field(min_length=1, max_length=MAX_BLOCKS)

    def cited(self) -> set[uuid.UUID]:
        return {claim for block in self.blocks for claim in block.claim_ids}


@dataclass(frozen=True)
class ClaimFacts:
    story_id: uuid.UUID
    status: str


def check_draft(
    versions: list[LanguageVersion],
    *,
    story_id: uuid.UUID,
    story_state: str,
    article_state: str | None,
    policy: LanguagePolicy,
    claims: dict[uuid.UUID, ClaimFacts],
) -> list[str]:
    """Every reason the draft cannot be written (empty: it can). ``claims``: the cited claims
    that exist, with their story and status."""
    issues: list[str] = []
    if StoryState(story_state) in CLOSED_STORIES:
        issues.append(f"the story is {story_state}: nothing more is written for it")
    if article_state is not None and article_state != ArticleState.DRAFT:
        issues.append(
            f"the article is {article_state}: drafts can only be written while it is DRAFT "
            "(new, or sent back for revision)"
        )

    langs = [v.lang for v in versions]
    if len(set(langs)) != len(langs):
        issues.append(f"each language once: got {langs}")
    unknown = [lang for lang in langs if lang not in policy.langs]
    if unknown:
        issues.append(f"languages not in the company's policy {list(policy.langs)}: {unknown}")
    if policy.primary not in langs:
        issues.append(f"the primary language {policy.primary} is required")
    if policy.require_all and set(policy.langs) - set(langs):
        missing = [lang for lang in policy.langs if lang not in langs]
        issues.append(f"every language is required (require_all_langs): missing {missing}")

    for version in versions:
        # the writer's own words are in the language they claim to be (D-002); the body may
        # quote its sources in theirs, so it is not counted
        issues.extend(script_problems(version.lang, title=version.title, summary=version.summary))
        for index, block in enumerate(version.blocks, 1):
            where = f"{version.lang} block {index} ({block.type})"
            if block.type == "heading" and block.claim_ids:
                issues.append(f"{where}: headings cite no claims")
            if block.type != "heading" and not block.claim_ids:
                issues.append(f"{where}: cite the claim(s) it states in claim_ids")
            if block.type == "quote" and len(block.text) > MAX_QUOTE:
                issues.append(f"{where}: quotes are at most {MAX_QUOTE} characters")

    cited = set().union(*(v.cited() for v in versions)) if versions else set()
    for claim_id in sorted(cited, key=str):
        facts = claims.get(claim_id)
        if facts is None or facts.story_id != story_id:
            issues.append(f"claim {claim_id} is not one of this story's claims")
        elif facts.status == ClaimStatus.REJECTED:
            issues.append(f"claim {claim_id} was rejected by fact-check and cannot be cited")

    if len(versions) > 1:
        reference = versions[0]
        for other in versions[1:]:
            only_ref = reference.cited() - other.cited()
            only_other = other.cited() - reference.cited()
            if only_ref or only_other:
                issues.append(
                    f"{reference.lang} and {other.lang} must cite the same claims: "
                    f"only in {reference.lang}: {sorted(map(str, only_ref))}; "
                    f"only in {other.lang}: {sorted(map(str, only_other))}"
                )
    return issues


_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title: str, article_id: uuid.UUID) -> str:
    """A readable, unique public name: the title's ASCII words and a piece of the id."""
    words = _NOT_SLUG.sub("-", title.lower()).strip("-")[:60].rstrip("-")
    suffix = article_id.hex[-6:]
    return f"{words}-{suffix}" if words else f"article-{suffix}"
