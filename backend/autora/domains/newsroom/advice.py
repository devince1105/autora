"""No advice in the newsroom's own voice (D-035): the company policy ``newsroom.no_advice``.

A newsroom that writes about stocks for readers in Taiwan must not tell them what to buy: giving
securities advice for pay needs a licence (證券投資信託及顧問法), and a disclaimer at the bottom
does not change what the article says. With the policy on, the newsroom reports what happened
and what others said, never its own call:

- **no opinion claims**: an assessment is only written as somebody's — an attribution or a quote
  claim, with their words as evidence ("Morgan Stanley raised its target to ...");
- **advice and prediction words only in somebody else's mouth**: a paragraph that says "worth
  buying", "is poised to rise" or "目標價" must cite an attribution or a quote claim, i.e. the
  words are reported, not ours. Titles and summaries cite nothing, so advice words are refused
  there outright; prediction words there need the version to cite somebody's view at all;
- **never "we"**: "我們認為", "本站建議", "we recommend" are refused everywhere.

What this cannot catch is an assessment in words that are not on these lists. The lists are the
first line; the person who approves every article before it is published (D-001) is the last.
Keep them short and literal: a word here that is also plain reporting ("加碼" is what Berkshire
did, not what we advise) would refuse true stories.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from autora.domains.newsroom.models import ClaimType

NO_ADVICE_KEY = "newsroom.no_advice"

SOMEBODY_ELSES = {ClaimType.ATTRIBUTION, ClaimType.QUOTE}
"""Claim types whose words are somebody's, reported: where an assessment may appear."""

ADVICE = re.compile(
    r"建議(投資人|讀者|你|您)?(買進|買入|賣出|持有|布局|佈局|進場|加碼|減碼|入手)"
    r"|值得(買進|買入|布局|佈局|投資|入手|進場)"
    r"|(可以|可|不妨)(逢低)?(買進|買入|布局|佈局|進場)"
    r"|逢低(買進|買入|布局|佈局|承接)"
    r"|推薦(買進|個股|標的)|首選(個股|標的)?|投資人(應|應該|可以|不妨)|買點|進場時機"
    r"|\b(should|must) (buy|sell|own)\b|\bworth (buying|owning)\b|\bbuy the dip\b"
    r"|\b(good|great) time to (buy|sell)\b|\binvestors should\b|\btop pick\b"
    r"|\b(recommend|recommended) (buying|selling)\b",
    re.IGNORECASE,
)
"""Telling the reader what to do with their money."""

PREDICTION = re.compile(
    r"可望|有望|看漲|看跌|看好|看壞|上看|目標價|將(大)?(漲|跌)|股價將"
    r"|\bpoised to\b|\blikely to (rise|fall|climb|drop|gain)\b|\bset to (soar|surge|rise)\b"
    r"|\bwill (rise|fall|climb|soar|surge)\b|\bprice target\b|\bupside\b",
    re.IGNORECASE,
)
"""Saying where a price is going."""

OUR_VIEW = re.compile(
    r"(我們|本站|本報|編輯部|筆者)(認為|建議|預期|預估|看好|相信)"
    r"|\bwe (think|believe|expect|recommend|suggest)\b|\bin our view\b|\bour (view|pick)\b",
    re.IGNORECASE,
)
"""The newsroom's own view, which it does not have."""


def no_advice(policies: Mapping[str, Any]) -> bool:
    return bool(policies.get(NO_ADVICE_KEY, False))


def opinion_refused() -> str:
    return (
        "this newsroom publishes no opinions of its own (company policy newsroom.no_advice): "
        "record the view as an attribution claim — who holds it — with their words as evidence"
    )


def _found(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0) if match else None


def advice_problems(versions: Iterable[Any], claim_types: Mapping[uuid.UUID, str]) -> list[str]:
    """Every place a draft speaks for itself about what to buy or where prices go.

    ``versions``: the draft's ``LanguageVersion``s. ``claim_types``: the type of each claim they
    cite (claims that do not exist are the caller's problem, not this one's).
    """
    issues: list[str] = []
    for version in versions:
        kinds = {
            ClaimType(claim_types[c])
            for block in version.blocks
            for c in block.claim_ids
            if c in claim_types
        }
        for claim_id in sorted({c for b in version.blocks for c in b.claim_ids}, key=str):
            if claim_types.get(claim_id) == ClaimType.OPINION:
                issues.append(
                    f"{version.lang}: claim {claim_id} is an opinion; {opinion_refused()}"
                )

        for field in ("title", "summary"):
            text = getattr(version, field) or ""
            if word := _found(OUR_VIEW, text) or _found(ADVICE, text):
                issues.append(
                    f"{version.lang} {field}: {word!r} gives advice in the newsroom's own voice; "
                    "report what happened, not what to buy"
                )
            elif (word := _found(PREDICTION, text)) and not kinds & SOMEBODY_ELSES:
                issues.append(
                    f"{version.lang} {field}: {word!r} is a prediction nobody in the article made; "
                    "only report a forecast as somebody's (an attribution or quote claim)"
                )

        for index, block in enumerate(version.blocks, 1):
            where = f"{version.lang} block {index} ({block.type})"
            if word := _found(OUR_VIEW, block.text):
                issues.append(f"{where}: {word!r} is the newsroom's own view; it has none")
                continue
            cites = {ClaimType(claim_types[c]) for c in block.claim_ids if c in claim_types}
            if cites & SOMEBODY_ELSES:
                continue  # reported words: whoever said them said them
            if word := _found(ADVICE, block.text) or _found(PREDICTION, block.text):
                issues.append(
                    f"{where}: {word!r} is advice or a forecast, and this block cites no one "
                    "who said it; cite the attribution or quote claim it reports, or remove it"
                )
    return issues


NO_ADVICE_BRIEF = (
    "Company policy newsroom.no_advice is ON: this newsroom reports facts and what others said, "
    "never its own view. No opinion claims. Any assessment, recommendation, rating, target price "
    "or forecast is somebody's: write who said it (an attribution or quote claim with their "
    "words), never as the newsroom's. Never write 'we think / 我們認為 / 本站建議', never "
    "tell readers what to buy or sell ('值得買進', '可逢低布局', 'worth buying'), and never "
    "predict prices yourself ('可望上漲', 'is poised to rise')."
)
"""What the analyst and the writer are told when the policy is on."""
