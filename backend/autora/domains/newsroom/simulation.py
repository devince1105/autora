"""The simulated model for newsroom tasks (``MODEL_PROVIDER=fake``; 3d-office/06 §6).

It reads its conversation like a real model would (the task's first message, the tool results so
far) and acts through the same tools, so everything a run does is real: searches hit the search
provider, pages become evidence, validators check the result. Only the decisions are scripted.

T-506: the researcher; T-507: the analyst; T-509: the writer; T-511: the editor. Marketing is
added with its behavior (T-513), the full demo scripts with T-518.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from autora.runtime.models.providers.fake import FakeToolUse, FakeTurn
from autora.runtime.models.types import ModelRequest, TextBlock, ToolResultBlock, ToolUseBlock

TARGET_SOURCES = 3
_URL = re.compile(r"https?://\S+")


def respond(request: ModelRequest) -> FakeTurn | None:
    """A reply for newsroom tasks; None for anything else."""
    key = (request.context.role, request.context.task_name)
    handler = _HANDLERS.get(key)
    return handler(request) if handler else None


# --- reading the conversation -----------------------------------------------------------------


def _first_text(request: ModelRequest) -> str:
    first = request.messages[0].content[0]
    return first.text if isinstance(first, TextBlock) else ""


def _field(text: str, name: str) -> str | None:
    match = re.search(rf"^{re.escape(name)}: (.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _calls(request: ModelRequest) -> list[tuple[str, dict[str, Any], ToolResultBlock | None]]:
    """Every tool call so far: (name, input, result)."""
    uses: dict[str, ToolUseBlock] = {}
    results: dict[str, ToolResultBlock] = {}
    order: list[str] = []
    for message in request.messages:
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                uses[block.id] = block
                order.append(block.id)
            elif isinstance(block, ToolResultBlock):
                results[block.tool_use_id] = block
    return [(uses[i].name, uses[i].input, results.get(i)) for i in order]


def _output(result: ToolResultBlock | None) -> dict[str, Any] | None:
    if result is None or result.is_error:
        return None
    try:
        value = json.loads(result.content)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# --- researcher (T-506) -----------------------------------------------------------------------


def _research(request: ModelRequest) -> FakeTurn:
    first = _first_text(request)
    story_id = _field(first, "Story id")
    title = _field(first, "Story") or "the story"
    leads = (
        [u.rstrip(").,") for u in _URL.findall(first.split("Leads:", 1)[1])]
        if "Leads:" in first
        else []
    )
    calls = _calls(request)

    searched = [c for c in calls if c[0] == "web_search"]
    if not searched:
        return FakeTurn(
            text="I'll look for more sources.",
            tool_uses=[FakeToolUse(name="web_search", input={"query": title, "k": 5})],
        )
    candidates = list(leads)
    for _, _, result in searched:
        for item in (_output(result) or {}).get("results", []):
            candidates.append(item["url"])
    tried = {c[1].get("url") for c in calls if c[0] == "fetch_url"}
    captured: dict[str, dict[str, Any]] = {}
    for name, _, result in calls:
        out = _output(result)
        if name == "fetch_url" and out and out.get("evidence_id"):
            captured[out["evidence_id"]] = out

    if len(captured) < TARGET_SOURCES:
        hosts = {urlsplit(o["url"]).hostname for o in captured.values()}
        untried = [u for u in dict.fromkeys(candidates) if u not in tried]
        untried.sort(key=lambda u: urlsplit(u).hostname in hosts)  # a new site first
        if untried:
            return FakeTurn(
                text="Capturing a source.",
                tool_uses=[FakeToolUse(name="fetch_url", input={"url": untried[0]})],
            )

    summaries = []
    for evidence_id, out in captured.items():
        name = (out.get("title") or out["url"])[:80]
        site = urlsplit(out["url"]).hostname
        summaries.append(
            {"evidence_id": evidence_id, "summary": f"來源「{name}」（{site}）的報導。"}
        )
    return FakeTurn(
        structured={
            "story_id": story_id,
            "evidence_ids": list(captured),
            "source_summaries": summaries,
            "suggested_angles": [f"{title}：對居民與城市的實際影響"],
        }
    )


# --- analyst (T-507) --------------------------------------------------------------------------

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# a period ends a sentence only before whitespace or the end ("NT$4.6 billion" is one number)
_SENTENCE = re.compile(r"(?:[^。！？!?\n.]|\.(?=\S))+[。！？!?.]")
_DIGIT = re.compile(r"\d")
MAX_CLAIMS = 4


def _sentences(text: str) -> list[str]:
    """Sentences with a number in them, short enough to quote (the analyst's pick)."""
    out = []
    for match in _SENTENCE.finditer(text):
        sentence = match.group(0).strip()
        if _DIGIT.search(sentence) and 20 <= len(sentence) <= 300:
            out.append(sentence)
    return out


def _analysis(request: ModelRequest) -> FakeTurn:
    first = _first_text(request)
    story_id = _field(first, "Story id")
    title = _field(first, "Story") or "the story"
    block = first.split("Evidence (from the researcher):", 1)[1] if "Evidence" in first else ""
    evidence_ids = list(dict.fromkeys(_UUID.findall(block.split("Suggested angles:", 1)[0])))
    calls = _calls(request)

    read = {c[1].get("evidence_id") for c in calls if c[0] == "read_evidence"}
    unread = [e for e in evidence_ids if e not in read]
    if unread:
        return FakeTurn(
            text="Reading the evidence.",
            tool_uses=[
                FakeToolUse(name="read_evidence", input={"evidence_id": e, "limit": 8000})
                for e in unread
            ],
        )

    made = [
        out["claim_id"]
        for name, _, result in calls
        if name == "create_claim" and (out := _output(result)) and out.get("claim_id")
    ]
    tried = any(name == "create_claim" for name, _, _ in calls)
    if not tried:
        # one number claim per sentence with a number, the sentence itself as its quote
        texts = [
            (args["evidence_id"], out["text"])
            for name, args, result in calls
            if name == "read_evidence" and (out := _output(result))
        ]
        picks: list[tuple[str, str]] = []
        for evidence_id, text in texts:
            for sentence in _sentences(text)[:2]:
                picks.append((evidence_id, sentence))
        picks = picks[:MAX_CLAIMS]
        if picks:
            return FakeTurn(
                text="Recording claims.",
                tool_uses=[
                    FakeToolUse(
                        name="create_claim",
                        input={
                            "story_id": story_id,
                            "text": sentence,
                            "claim_type": "number",
                            "evidence": [{"evidence_id": evidence_id, "quote": sentence}],
                        },
                    )
                    for evidence_id, sentence in picks
                ],
            )
    return FakeTurn(
        structured={
            "story_id": story_id,
            "claim_ids": made,
            "angle": f"{title}：以數據看成本與效益",
            "key_numbers": [],
            "contradictions": [],
        }
    )


# --- writer (T-509) ---------------------------------------------------------------------------

_LANG = re.compile(r"\b[a-z]{2}(?:-[A-Z][A-Za-z]+)?\b")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_CLAIM_LINE = re.compile(rf"^- ({_UUID.pattern}) \| (\w+)[^|]*\| (.+)$", re.MULTILINE)
MAX_PARAGRAPHS = 6


def _in(lang: str, text: str) -> str:
    """The claim's words in a language's paragraph: as they are when the claim is already in that
    language, else attributed to the source (the simulation does not translate)."""
    chinese = bool(_CJK.search(text))
    if lang.startswith("zh"):
        return text if chinese else f"根據來源：{text}"
    return f"According to the source: {text}" if chinese else text


def _draft(request: ModelRequest) -> FakeTurn:
    first = _first_text(request)
    story_id = _field(first, "Story id")
    title = _field(first, "Story") or "the story"
    langs_line = _field(first, "Languages") or "zh-TW (primary)"
    langs = _LANG.findall(langs_line.split(";")[0])
    claims = _CLAIM_LINE.findall(first.split("Claims (cite these ids):", 1)[-1])
    revision = "This is a revision" in first
    calls = _calls(request)

    if revision and not any(name == "read_draft" for name, _, _ in calls):
        return FakeTurn(
            text="Reading the current draft.",
            tool_uses=[FakeToolUse(name="read_draft", input={"story_id": story_id})],
        )
    written = [
        out for name, _, result in calls if name == "write_draft" and (out := _output(result))
    ]
    if not written:
        picks = claims[:MAX_PARAGRAPHS]
        versions = []
        for lang in langs:
            zh = lang.startswith("zh")
            versions.append(
                {
                    "lang": lang,
                    "title": f"{title}：數據一覽" if zh else f"{title}: the numbers",
                    "summary": f"{title}的重點數字。" if zh else f"The key numbers on {title}.",
                    "blocks": [
                        {"type": "heading", "text": "重點" if zh else "Key points"},
                        *(
                            {"type": "paragraph", "text": _in(lang, text), "claim_ids": [cid]}
                            for cid, _, text in picks
                        ),
                    ],
                }
            )
        args: dict[str, Any] = {"story_id": story_id, "versions": versions}
        if revision:
            args["change_summary"] = "依編輯意見修正各段落。"
        return FakeTurn(
            text="Writing the draft.", tool_uses=[FakeToolUse(name="write_draft", input=args)]
        )
    out = written[-1]
    return FakeTurn(
        structured={
            "article_id": out["article_id"],
            "draft_group_id": out["draft_group_id"],
            "versions": out["versions"],
        }
    )


# --- editor (T-511) ---------------------------------------------------------------------------


def _review(request: ModelRequest) -> FakeTurn:
    first = _first_text(request)
    article_id = _field(first, "Article id")
    calls = _calls(request)
    checked = [
        out for name, _, result in calls if name == "run_fact_check" and (out := _output(result))
    ]
    if not checked:
        return FakeTurn(
            text="Reading the draft and checking the facts.",
            tool_uses=[
                FakeToolUse(name="read_draft", input={"article_id": article_id}),
                FakeToolUse(name="run_fact_check", input={"article_id": article_id}),
            ],
        )
    report = checked[-1]
    issues = [
        {
            "message": f"主張「{r['text'][:80]}」沒有通過事實查核"
            f"（{'；'.join(r['problems'])[:200]}），請刪除或改寫這段。",
            "kind": "unsupported",
        }
        for r in report["results"]
        if r.get("verdict") == "fail"
    ] or (
        [{"message": "兩種語言引用的主張不一致，請對齊。", "kind": "translation"}]
        if not report["passed"]
        else []
    )
    decided = [
        name
        for name, _, result in calls
        if name in ("accept_draft", "request_revision") and _output(result)
    ]
    if not decided:
        if report["passed"]:
            use = FakeToolUse(
                name="accept_draft",
                input={"article_id": article_id, "fact_check_report_id": report["report_id"]},
            )
        else:
            use = FakeToolUse(
                name="request_revision", input={"article_id": article_id, "issues": issues}
            )
        return FakeTurn(text="Deciding.", tool_uses=[use])
    return FakeTurn(
        structured={
            "article_id": article_id,
            "verdict": "accept" if report["passed"] else "revise",
            "fact_check_report_id": report["report_id"],
            "issues": [] if report["passed"] else issues,
        }
    )


_HANDLERS = {
    ("researcher", "research"): _research,
    ("analyst", "analysis"): _analysis,
    ("writer", "draft"): _draft,
    ("editor", "review"): _review,
}
