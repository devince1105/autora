"""Composition root: the only module allowed to import every layer (runtime, company, realtime,
domains). Processes (api, worker) and tools (schema codegen) assemble the system from here.

Adding a domain means registering its pieces below: events, policy rules, workflow templates,
tools, agent behaviors and (for ``MODEL_PROVIDER=fake``) its simulated model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from autora.infra.blobstore import BlobStore
    from autora.infra.http import PageFetcher
    from autora.infra.search import SearchProvider
    from autora.infra.settings import Settings
    from autora.runtime.approvals import ApprovalService
    from autora.runtime.behaviors import BehaviorRegistry
    from autora.runtime.dag import TemplateRegistry, WorkflowEngine
    from autora.runtime.models.providers.fake import FakeTurn
    from autora.runtime.models.types import ModelRequest
    from autora.runtime.policy import PolicyEngine
    from autora.runtime.scheduler import Scheduler
    from autora.runtime.task_manager import TaskManager
    from autora.runtime.tools import ToolRegistry
    from autora.runtime.worker import Worker


def load_event_catalogs() -> None:
    """Import every module that registers event payloads, so the registry is complete."""
    import autora.company.events  # noqa: F401
    import autora.domains.newsroom.events  # noqa: F401
    import autora.runtime.events  # noqa: F401


def load_models() -> None:
    """Import every module that declares tables, so SQLAlchemy's metadata is complete."""
    import autora.db.models  # noqa: F401
    import autora.domains.echo.models  # noqa: F401
    import autora.domains.newsroom.models  # noqa: F401


def build_policy_engine() -> PolicyEngine:
    """The policy engine with every layer's rules registered."""
    from autora.company import policy as company_policy
    from autora.domains.echo import policy as echo_policy
    from autora.domains.newsroom import policy as newsroom_policy
    from autora.runtime.policy import PolicyEngine

    engine = PolicyEngine()
    company_policy.register(engine)
    newsroom_policy.register(engine)
    echo_policy.register(engine)
    return engine


def build_templates() -> TemplateRegistry:
    from autora.domains import echo
    from autora.runtime.dag import TemplateRegistry

    templates = TemplateRegistry()
    echo.register_templates(templates)
    return templates


def build_behaviors() -> BehaviorRegistry:
    from autora.domains import echo
    from autora.runtime.behaviors import BehaviorRegistry

    behaviors = BehaviorRegistry()
    echo.register_behaviors(behaviors)
    return behaviors


def build_search_provider(settings: Settings | None) -> SearchProvider:
    """``web_search``'s provider: Tavily when ``TOOLS_PROFILE=live``, else the fixture corpus."""
    from pathlib import Path

    from autora.infra.search.fixture import FixtureSearchProvider
    from autora.infra.search.tavily import TavilySearchProvider

    if settings is not None and settings.tools_profile == "live":
        assert settings.tavily_api_key is not None  # Settings refuses live without a key
        return TavilySearchProvider(
            settings.tavily_api_key,
            timeout_s=settings.tavily_timeout_seconds,
            depth=settings.tavily_search_depth,
            cost_per_credit=settings.tavily_cost_per_credit,
            requests_per_minute=settings.tavily_requests_per_minute,
        )
    import autora.domains.newsroom as newsroom

    return FixtureSearchProvider.from_file(
        Path(newsroom.__file__).parent / "fixtures" / "search.json"
    )


def build_page_fetcher(settings: Settings | None) -> PageFetcher:
    """Pages and feeds: the web when ``TOOLS_PROFILE=live``, else the newsroom's fixture files."""
    import json
    from pathlib import Path

    from autora.infra.http import FixtureFetcher, HttpFetcher

    if settings is not None and settings.tools_profile == "live":
        return HttpFetcher(
            timeout_s=settings.fetch_timeout_seconds, max_bytes=settings.fetch_max_bytes
        )
    import autora.domains.newsroom as newsroom

    root = Path(newsroom.__file__).parent / "fixtures"
    return FixtureFetcher(root, json.loads((root / "routes.json").read_text("utf-8")))


def build_scheduler(
    settings: Settings | None,
    session_factory: async_sessionmaker[AsyncSession],
    worker_id: str,
) -> Scheduler:
    """The scheduler with every domain's handlers (newsroom: the source poller, T-501)."""
    from autora.domains.newsroom.sources import POLL_SCHEDULE, SourcePoller
    from autora.runtime.scheduler import Scheduler

    scheduler = Scheduler(session_factory, worker_id)
    poller = SourcePoller(
        fetcher=build_page_fetcher(settings), search=build_search_provider(settings)
    )
    scheduler.register(POLL_SCHEDULE, poller.schedule_handler())
    return scheduler


def build_tools(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings | None = None
) -> ToolRegistry:
    from autora.domains import echo
    from autora.domains.newsroom import tools as newsroom_tools
    from autora.runtime.tools import ToolRegistry

    tools = ToolRegistry(session_factory)
    echo.register_tools(tools)
    newsroom_tools.register_tools(tools, search_provider=build_search_provider(settings))
    return tools


def simulated_model(request: ModelRequest) -> FakeTurn:
    """The fake provider's answer to any unscripted request: ask each domain's simulation."""
    from autora.domains.echo import simulation as echo_simulation
    from autora.runtime.models.providers.fake import FakeTurn

    for respond in (echo_simulation.respond,):
        turn = respond(request)
        if turn is not None:
            return turn
    ctx = request.context
    return FakeTurn(
        error="NoSimulation",
        text=f"no simulation for role={ctx.role} task={ctx.task_name}",
        fallback_allowed=False,
    )


@dataclass(frozen=True)
class Runtime:
    """The wired-together runtime services a process needs (api, worker)."""

    task_manager: TaskManager
    templates: TemplateRegistry
    workflows: WorkflowEngine
    approvals: ApprovalService
    policy: PolicyEngine


def build_runtime(settings: Settings | None = None) -> Runtime:
    from autora.runtime.approvals import ApprovalService
    from autora.runtime.dag import WorkflowEngine
    from autora.runtime.task_manager import TaskManager

    load_event_catalogs()
    load_models()
    task_manager = TaskManager()
    if settings is not None:
        task_manager.lease = timedelta(seconds=settings.task_lease_seconds)
        task_manager.retry_base = timedelta(seconds=settings.task_retry_base_seconds)
    templates = build_templates()
    workflows = WorkflowEngine(task_manager, templates)
    return Runtime(
        task_manager=task_manager,
        templates=templates,
        workflows=workflows,
        approvals=ApprovalService(task_manager),
        policy=build_policy_engine(),
    )


def build_worker(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    blobs: BlobStore | None = None,
    company_ids: frozenset[uuid.UUID] | None = None,
) -> Worker:
    """Everything a worker process runs: task loop, agent runner, maintenance, scheduler."""
    from autora.db.session import get_sessionmaker
    from autora.infra.blobstore import LocalFSBlobStore
    from autora.runtime.agent_runner import AgentRunner
    from autora.runtime.cost.guard import DbCostGuard
    from autora.runtime.models.factory import gateway_from_settings
    from autora.runtime.progress import ProgressPublisher
    from autora.runtime.worker import Worker

    runtime = build_runtime(settings)
    session_factory = session_factory or get_sessionmaker()
    gateway = gateway_from_settings(
        settings,
        session_factory,
        cost_guard=DbCostGuard(session_factory),
        fake_default=simulated_model,
    )
    runner = AgentRunner(
        session_factory=session_factory,
        task_manager=runtime.task_manager,
        gateway=gateway,
        tools=build_tools(session_factory, settings),
        policy=runtime.policy,
        approvals=runtime.approvals,
        blobs=blobs or LocalFSBlobStore(settings.blob_store_dir),
        behaviors=build_behaviors(),
        progress=ProgressPublisher(session_factory),
    )
    return Worker(
        worker_id=settings.worker_id,
        session_factory=session_factory,
        task_manager=runtime.task_manager,
        runner=runner,
        approvals=runtime.approvals,
        scheduler=build_scheduler(settings, session_factory, settings.worker_id),
        concurrency=settings.worker_concurrency,
        poll_interval=settings.worker_poll_seconds,
        maintenance_interval=settings.worker_maintenance_seconds,
        company_ids=company_ids
        if company_ids is not None
        else (frozenset(settings.worker_company_ids) or None),
    )
