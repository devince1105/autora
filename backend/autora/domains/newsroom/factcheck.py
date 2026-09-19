"""Deterministic fact-check (T-510, platform/05 §3 layers 1 and 2). No language model decides
anything here; layer 3 (does the quote really support the claim?) is the editor's, and the
report lists what it has to judge.

**Layer 1, structure** (fact, number and quote claims):
- at least one ``supports`` quote;
- every quote is still the evidence's own text at its recorded place;
- low-trust sources cannot support a claim alone: at least one supporting source must reach
  ``min_trust``, or two independent sources (different sources or sites) must agree;
- a **number** claim's numbers each appear in a supporting quote, compared as values with their
  scale words: "NT$420 million" matches "4.2 億", "3,000" matches "3000", "18%" matches "18 %".

**Layer 2, cross-check**:
- a fact, number or quote claim with a ``contradicts`` quote fails: a contested point must be
  written as who said what (an ``attribution`` claim), which may carry contradictions;
- other passages of the story's evidence that are close to the claim (vector search) are listed
  for the editor: advisory, they do not decide the verdict.

Opinions need no evidence; attributions need no support rule beyond having some quote.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urlsplit

from autora.domains.newsroom.models import ClaimType, SupportType

CHECKED = {ClaimType.FACT, ClaimType.NUMBER, ClaimType.QUOTE}

_SCALE = {
    "thousand": 1e3,
    "million": 1e6,
    "millions": 1e6,
    "billion": 1e9,
    "billions": 1e9,
    "trillion": 1e12,
    "千": 1e3,
    "萬": 1e4,
    "万": 1e4,
    "億": 1e8,
    "亿": 1e8,
    "兆": 1e12,
}
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*"
    r"(thousand|millions?|billions?|trillion|千|萬|万|億|亿|兆)?",
    re.IGNORECASE,
)


def numbers(text: str) -> list[float]:
    """The numbers in ``text`` as values, scale words applied (``4.2 億`` -> 420000000.0)."""
    values = []
    for whole, fraction, scale in _NUMBER.findall(text):
        value = float(whole.replace(",", "") + (fraction or ""))
        values.append(value * _SCALE.get(scale.lower(), 1.0) if scale else value)
    return values


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


@dataclass(frozen=True)
class QuoteFacts:
    evidence_id: str
    quote: str
    support_type: str
    intact: bool
    """The quote is still the evidence's text at its recorded offsets."""
    trust: Decimal
    source_key: str
    """Who vouches for it: the source, or the page's site when no source listed it."""


def source_key(source_id: object | None, url: str) -> str:
    return f"source:{source_id}" if source_id else f"site:{urlsplit(url).hostname or url}"


@dataclass
class ClaimVerdict:
    claim_id: str
    claim_type: str
    passed: bool = True
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def fail(self, problem: str) -> None:
        self.passed = False
        self.problems.append(problem)


def check_claim(
    claim_id: str,
    claim_type: str,
    text: str,
    quotes: list[QuoteFacts],
    *,
    min_trust: Decimal,
) -> ClaimVerdict:
    verdict = ClaimVerdict(claim_id=claim_id, claim_type=claim_type)
    kind = ClaimType(claim_type)
    broken = [q for q in quotes if not q.intact]
    for q in broken:
        verdict.fail(f"a quote no longer matches evidence {q.evidence_id} at its place")
    supports = [q for q in quotes if q.intact and q.support_type == SupportType.SUPPORTS]
    contradicts = [q for q in quotes if q.intact and q.support_type == SupportType.CONTRADICTS]

    if kind in CHECKED:
        if not supports:
            verdict.fail("no supporting quote")
        else:
            trusted = [q for q in supports if q.trust >= min_trust]
            independent = {q.source_key for q in supports}
            if not trusted and len(independent) < 2:
                verdict.fail(
                    f"only low-trust support (below {min_trust}) from one source: "
                    "add a trusted source or a second independent one"
                )
        if kind is ClaimType.NUMBER:
            quoted = [n for q in supports for n in numbers(q.quote)]
            for value in numbers(text):
                if not any(_same(value, n) for n in quoted):
                    shown = f"{value:g}"
                    verdict.fail(f"the number {shown} is not in any supporting quote")
        if contradicts:
            verdict.fail(
                f"contradicted by evidence {sorted({q.evidence_id for q in contradicts})}: "
                "write the contested point as who said what (an attribution claim)"
            )
    else:
        if contradicts:
            verdict.notes.append("contradicting evidence is reported with the attribution")
        if kind is ClaimType.ATTRIBUTION and not quotes:
            verdict.fail("an attribution needs a quote showing who said or did it")
    return verdict
