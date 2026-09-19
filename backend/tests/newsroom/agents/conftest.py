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
LINE = (("research", "researcher"), ("analysis", "analyst"), ("draft", "writer"))


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
    for name, role in LINE:
        async with committed() as session:
            line.tasks[name] = await TaskManager().add_task(
                session,
                company_id=company.id,
                project_id=project.id,
                name=name,
                display_name=name.title(),
                required_role=role,
                input={"params": {"story_id": str(story.id), **((params or {}).get(name) or {})}},
                depends_on=[previous.id] if previous else [],
                ready=True,
            )
            await session.commit()
        await worker.run_until_idle()
        previous = line.tasks[name]
        if name == until:
            break
    return line
