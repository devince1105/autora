from dataclasses import dataclass, field

from autora.app import build_worker
from autora.company.agents import hire_agent
from autora.db.models import Company, Project, Task
from autora.domains.newsroom.models import Story
from autora.runtime.actor import Actor
from autora.runtime.task_manager import TaskManager
from tests.conftest import unique_company
from tests.echo_fixtures import e2e_settings  # noqa: F401 (fixture)

# the newsroom line, in order: (task, role)
LINE = (
    ("research", "researcher"),
    ("analysis", "analyst"),
    ("draft", "writer"),
    ("review", "editor"),
    ("distribute", "marketing"),  # after publication: run_line stops before it
)
ROLES = dict(LINE)


@dataclass
class Line:
    company: Company
    project: Project
    story: Story
    tasks: dict[str, Task] = field(default_factory=dict)
    worker: object = None


async def run_line(committed, settings, *, until: str, params=None) -> Line:
    """Run the newsroom line on a new story with a real worker (simulated model, offline tools)
    up to and including task ``until``. Each task depends on the one before; the workflow engine
    (T-514) is not wired here, so each is added ready once the one before has run."""
    async with committed() as session:
        company = await unique_company(session, "line")
        project = Project(
            company_id=company.id,
            name="newsroom",
            state="ACTIVE",
            kill_criteria={"max_cost_usd": 10},
        )
        session.add(project)
        await session.flush()
        for _, role in LINE:
            await hire_agent(
                session,
                company_id=company.id,
                role=role,
                display_name=role,
                actor=Actor.system("t"),
            )
        story = Story(company_id=company.id, title="Lumen City microgrid", state="SELECTED")
        session.add(story)
        await session.commit()
    worker = build_worker(settings, session_factory=committed, company_ids=frozenset({company.id}))
    line = Line(company=company, project=project, story=story, worker=worker)
    previous = None
    for name, _ in LINE:
        line.tasks[name] = await add_task(
            committed,
            line,
            name,
            params=(params or {}).get(name),
            depends_on=[previous.id] if previous else [],
        )
        await worker.run_until_idle()
        previous = line.tasks[name]
        if name == until:
            break
    return line


async def add_task(committed, line: Line, name: str, *, params=None, depends_on=()) -> Task:
    """Add a ready newsroom task for the line's story (a revision, a second review ...)."""
    async with committed() as session:
        task = await TaskManager().add_task(
            session,
            company_id=line.company.id,
            project_id=line.project.id,
            name=name,
            display_name=name.title(),
            required_role=ROLES[name],
            input={"params": {"story_id": str(line.story.id), **(params or {})}},
            depends_on=list(depends_on),
            ready=True,
        )
        await session.commit()
    return task
