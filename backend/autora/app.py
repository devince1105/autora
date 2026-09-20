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

    from autora.company.commands import CommandBus
    from autora.company.cycle import CycleRunner
    from autora.company.executive import Executive
    from autora.company.ledger import Ledger
    from autora.company.reporting import Reporting
    from autora.company.snapshot import SnapshotBuilder
    from autora.infra.blobstore import BlobStore
    from autora.infra.http import PageFetcher
    from autora.infra.search import SearchProvider
    from autora.infra.settings import Settings
    from autora.runtime.approvals import ApprovalService
    from autora.runtime.behaviors import BehaviorRegistry
    from autora.runtime.dag import TemplateRegistry, WorkflowEngine
    from autora.runtime.models.embeddings import Embedder
    from autora.runtime.models.providers.fake import FakeTurn
    from autora.runtime.models.types import ModelRequest
    from autora.runtime.policy import PolicyEngine
    from autora.runtime.scheduler import Scheduler
    from autora.runtime.services import ServiceRegistry
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
    from autora.company import executive, exploration
    from autora.domains import echo, newsroom
    from autora.runtime.dag import TemplateRegistry

    templates = TemplateRegistry()
    executive.register_templates(templates)  # the company's own work: planning and review
    exploration.register_templates(templates)  # and finding out what else it might do
    echo.register_templates(templates)
    newsroom.register_templates(templates)
    return templates


def build_behaviors(snapshots: SnapshotBuilder | None = None) -> BehaviorRegistry:
    """Every agent the runtime can run. The company's own come first: they must still work when
    every domain is deleted (ARCHITECTURE_V2_1 §9)."""
    from autora.company.agents import ceo, strategist
    from autora.domains import echo
    from autora.domains.newsroom import agents as newsroom_agents
    from autora.runtime.behaviors import BehaviorRegistry

    behaviors = BehaviorRegistry()
    ceo.register_behaviors(behaviors, snapshots)
    strategist.register_behaviors(behaviors, snapshots)
    echo.register_behaviors(behaviors)
    newsroom_agents.register_behaviors(behaviors)
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
    cycles: CycleRunner | None = None,
) -> Scheduler:
    """The scheduler with the company's daily cycle (T-601) and every domain's handlers
    (newsroom: the source poller T-501, story clustering T-504, the analytics collector
    T-516)."""
    from autora.company.cycle import CYCLE_START_SCHEDULE
    from autora.domains.newsroom.analytics import ANALYTICS_SCHEDULE, AnalyticsCollector
    from autora.domains.newsroom.settings import get_newsroom_settings
    from autora.domains.newsroom.sources import POLL_SCHEDULE, SourcePoller
    from autora.domains.newsroom.stories import CLUSTER_SCHEDULE, StoryDesk
    from autora.runtime.scheduler import Scheduler

    scheduler = Scheduler(session_factory, worker_id)
    poller = SourcePoller(
        fetcher=build_page_fetcher(settings), search=build_search_provider(settings)
    )
    scheduler.register(POLL_SCHEDULE, poller.schedule_handler())
    # the newsroom's own knob, read by the newsroom (ARCHITECTURE_V2_1 §9)
    desk = StoryDesk(
        build_embedder(settings), threshold=get_newsroom_settings().story_match_threshold
    )
    scheduler.register(CLUSTER_SCHEDULE, desk.schedule_handler())
    scheduler.register(ANALYTICS_SCHEDULE, AnalyticsCollector().schedule_handler())
    if cycles is not None:
        scheduler.register(CYCLE_START_SCHEDULE, cycles.schedule_handler())
    return scheduler


def build_embedder(settings: Settings | None) -> Embedder:
    """The ``embed`` binding sized for the newsroom's stored vectors (T-503)."""
    from autora.domains.newsroom.models import EMBED_DIM
    from autora.runtime.models.factory import embedder_from_settings

    return embedder_from_settings(settings, dim=EMBED_DIM)


def build_tools(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings | None = None,
    *,
    blobs: BlobStore | None = None,
    commands: CommandBus | None = None,
    policy: PolicyEngine | None = None,
    workflows: WorkflowEngine | None = None,
) -> ToolRegistry:
    """Every tool an agent can call. Without ``blobs``: the settings' blob store, else a
    temporary one. With ``commands``, executive agents also get ``submit_command`` — the only
    way any agent changes the company."""
    import tempfile
    from pathlib import Path

    from autora.company import tools as company_tools
    from autora.domains import echo
    from autora.domains.newsroom import tools as newsroom_tools
    from autora.infra.blobstore import LocalFSBlobStore
    from autora.runtime.tools import ToolRegistry

    if blobs is None:
        root = settings.blob_store_dir if settings else Path(tempfile.gettempdir()) / "autora-blobs"
        blobs = LocalFSBlobStore(root)
    tools = ToolRegistry(session_factory)
    if commands is not None:
        company_tools.register_tools(tools, commands)
    echo.register_tools(tools)
    newsroom_tools.register_tools(
        tools,
        search_provider=build_search_provider(settings),
        fetcher=build_page_fetcher(settings),
        blobs=blobs,
        embedder=build_embedder(settings),
        policy=policy,
        workflows=workflows,
    )
    return tools


def simulated_model(request: ModelRequest) -> FakeTurn:
    """The fake provider's answer to any unscripted request: ask the company, then each domain.

    The company comes first because its agents must keep working when every domain is deleted
    (ARCHITECTURE_V2_1 §9)."""
    from autora.company import simulation as company_simulation
    from autora.domains.echo import simulation as echo_simulation
    from autora.domains.newsroom import simulation as newsroom_simulation
    from autora.runtime.models.providers.fake import FakeTurn

    for respond in (
        company_simulation.respond,
        echo_simulation.respond,
        newsroom_simulation.respond,
    ):
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
    services: ServiceRegistry
    cycles: CycleRunner
    ledger: Ledger
    reporting: Reporting
    snapshots: SnapshotBuilder
    commands: CommandBus
    executive: Executive


def build_runtime(settings: Settings | None = None) -> Runtime:
    from autora.company import executive as company_executive
    from autora.company import exploration as company_exploration
    from autora.company import opportunities as company_opportunities
    from autora.company import summary as daily_summary
    from autora.company import verbs as company_verbs
    from autora.company import verbs_business as company_business_verbs
    from autora.company.commands import CommandBus
    from autora.company.cycle import CycleRunner, work_is_finished
    from autora.company.governance import Governance
    from autora.company.ledger import Ledger
    from autora.company.reporting import Reporting
    from autora.company.snapshot import SnapshotBuilder
    from autora.db.models import CycleStage
    from autora.domains import newsroom
    from autora.domains.newsroom import kpis as newsroom_kpis
    from autora.runtime.approvals import ApprovalService
    from autora.runtime.dag import WorkflowEngine
    from autora.runtime.services import ServiceRegistry
    from autora.runtime.task_manager import TaskManager

    load_event_catalogs()
    load_models()
    task_manager = TaskManager()
    if settings is not None:
        task_manager.lease = timedelta(seconds=settings.task_lease_seconds)
        task_manager.retry_base = timedelta(seconds=settings.task_retry_base_seconds)
    templates = build_templates()
    workflows = WorkflowEngine(task_manager, templates)
    policy = build_policy_engine()
    cycles = CycleRunner()
    cycles.finishes_when(CycleStage.EXECUTING, work_is_finished)
    ledger = Ledger()
    reporting = Reporting()
    reporting.register(newsroom_kpis.NAME, newsroom_kpis.kpis)
    # order matters: the ledger settles the cycle's costs, then reporting measures them
    cycles.when_entering(CycleStage.MEASURING, ledger.stage_hook())
    cycles.when_entering(CycleStage.MEASURING, reporting.stage_hook())
    snapshots = SnapshotBuilder(reporting, ledger)
    snapshots.register(newsroom_kpis.NAME, newsroom_kpis.candidates)
    approvals = ApprovalService(task_manager)
    commands = CommandBus(policy=policy, approvals=approvals, workflows=workflows)
    company_verbs.register(commands)
    company_business_verbs.register(commands)  # the business loop's eight (T-611)
    commands.install()
    # stale opportunities drop out first, then the rules fire, and only then does the CEO read
    # the cycle it is reviewing: it sees a company the deterministic parts have already acted on
    cycles.when_entering(CycleStage.REVIEWING, company_opportunities.stage_hook())
    cycles.when_entering(CycleStage.REVIEWING, Governance(commands).stage_hook())
    executive = company_executive.Executive(workflows)
    executive.install(cycles)
    # after the plan is settled: an exploration spends the budget the day already has
    company_exploration.Exploration(workflows).install(cycles)
    # last, so the day it describes is fully settled: the review is recorded by then (T-607)
    cycles.when_entering(CycleStage.DONE, daily_summary.stage_hook())
    runtime = Runtime(
        task_manager=task_manager,
        templates=templates,
        workflows=workflows,
        approvals=approvals,
        policy=policy,
        services=ServiceRegistry(),
        cycles=cycles,
        ledger=ledger,
        reporting=reporting,
        snapshots=snapshots,
        commands=commands,
        executive=executive,
    )
    newsroom.register(runtime)
    return runtime


def build_worker(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    blobs: BlobStore | None = None,
    company_ids: frozenset[uuid.UUID] | None = None,
    runtime: Runtime | None = None,
) -> Worker:
    """Everything a worker process runs: task loop, agent runner, maintenance, scheduler.

    ``runtime`` lets a caller share one it already built — a soak test drives the same cycle
    runner the worker advances, so both read the same (accelerated) clock (T-610).
    """
    from autora.company.cycle import maintenance_job as cycle_maintenance_job
    from autora.db.session import get_sessionmaker
    from autora.infra.blobstore import LocalFSBlobStore
    from autora.runtime.agent_runner import AgentRunner
    from autora.runtime.cost.guard import DbCostGuard
    from autora.runtime.models.factory import gateway_from_settings
    from autora.runtime.progress import ProgressPublisher
    from autora.runtime.services import ServiceDispatcher
    from autora.runtime.worker import Worker

    runtime = runtime or build_runtime(settings)
    session_factory = session_factory or get_sessionmaker()
    companies = (
        company_ids if company_ids is not None else (frozenset(settings.worker_company_ids) or None)
    )
    blobs = blobs or LocalFSBlobStore(settings.blob_store_dir)
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
        tools=build_tools(
            session_factory,
            settings,
            blobs=blobs,
            commands=runtime.commands,
            policy=runtime.policy,
            workflows=runtime.workflows,
        ),
        policy=runtime.policy,
        approvals=runtime.approvals,
        blobs=blobs,
        behaviors=build_behaviors(runtime.snapshots),
        progress=ProgressPublisher(session_factory),
    )
    return Worker(
        worker_id=settings.worker_id,
        session_factory=session_factory,
        task_manager=runtime.task_manager,
        runner=runner,
        approvals=runtime.approvals,
        scheduler=build_scheduler(settings, session_factory, settings.worker_id, runtime.cycles),
        services=ServiceDispatcher(
            session_factory=session_factory,
            registry=runtime.services,
            task_manager=runtime.task_manager,
            approvals=runtime.approvals,
            policy=runtime.policy,
            company_ids=companies,
        ),
        concurrency=settings.worker_concurrency,
        poll_interval=settings.worker_poll_seconds,
        maintenance_interval=settings.worker_maintenance_seconds,
        company_ids=companies,
        maintenance_jobs=[
            ("advance_cycles", cycle_maintenance_job(runtime.cycles, companies)),
            ("forget_stale_memories", runner.memory.maintenance_job()),
        ],
    )
