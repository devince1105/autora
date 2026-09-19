"""``web_search`` (T-500, D-003): find candidate pages for a story.

Results are **candidates, not evidence**: the tool writes nothing and produces no artifacts. An
agent that wants to use a page must ``fetch_url`` it (T-502), which snapshots it as Evidence;
the tool's output says so, so the model is told on every call. The provider (Tavily live, or the
fixture corpus) is chosen by the composition root from ``TOOLS_PROFILE``; its cost is recorded
on the call's TOOL_COMPLETED event (``cost_usd``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from autora.infra.search import SearchProvider
from autora.runtime.tools import ToolContext, ToolFn, ToolRegistry, ToolResult

NAME = "web_search"
CANDIDATES_NOTE = (
    "These are search candidates, not evidence. To use a page, call fetch_url on it; only "
    "fetched pages (evidence) may support claims."
)


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=400, description="What to search for.")
    k: int = Field(default=5, ge=1, le=10, description="How many results (1-10).")
    recency_days: int | None = Field(
        default=None, ge=1, le=365, description="Only pages published within this many days."
    )


def web_search_tool(provider: SearchProvider) -> ToolFn:
    async def web_search(args: WebSearchArgs, ctx: ToolContext) -> ToolResult:
        response = await provider.search(args.query, k=args.k, recency_days=args.recency_days)
        results = [r.model_dump(mode="json", exclude_none=True) for r in response.results]
        return ToolResult(
            output={"results": results, "note": CANDIDATES_NOTE},
            summary=f"{len(results)} results for {args.query[:80]!r} ({response.provider})",
            cost_usd=response.cost_usd,
        )

    return web_search


def register(registry: ToolRegistry, provider: SearchProvider) -> None:
    registry.tool(
        NAME,
        description=(
            "Search the web for pages about a topic. Returns candidate URLs with titles and "
            "snippets; they are not evidence until fetched with fetch_url."
        ),
        side_effect="read",
        timeout_s=20.0,
        retryable=True,
    )(web_search_tool(provider))
