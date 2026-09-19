"""The simulated model for newsroom tasks (``MODEL_PROVIDER=fake``; 3d-office/06 §6).

It reads its conversation like a real model would (the task's first message, the tool results so
far) and acts through the same tools, so everything a run does is real: searches hit the search
provider, pages become evidence, validators check the result. Only the decisions are scripted.

T-506: the researcher. The other newsroom roles are added with their behaviors (T-507 ...), the
full demo scripts with T-518.
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


_HANDLERS = {("researcher", "research"): _research}
