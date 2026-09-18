"""Shared setup for end-to-end tests: a staffed company with an ACTIVE project."""

from dataclasses import dataclass

import pytest

from autora.app import build_runtime
from autora.db.models import Company, Project, ProjectState
from autora.domains import echo
from autora.runtime.actor import Actor
from tests.conftest import unique_company

OPERATOR = Actor.human("operator")


@dataclass
class EchoCompany:
    company: Company
    project: Project
    agents: dict  # role -> Agent


@pytest.fixture
def e2e_settings(db_settings, tmp_path):
    """Test DB, fake model, blobs in a temp dir; whatever the developer's .env says."""
    return db_settings.model_copy(
        update={
            "model_provider": "fake",
            "blob_store_dir": tmp_path / "blobs",
            "worker_id": "e2e-worker",
            "worker_poll_seconds": 0.05,
        }
    )


@pytest.fixture
async def echo_company(committed) -> EchoCompany:
    async with committed() as session:
        company = await unique_company(session, "echo")
        project = Project(
            company_id=company.id,
            name="Echo demo",
            state=ProjectState.ACTIVE.value,
            kill_criteria={"max_cost_usd": 10},
        )
        session.add(project)
        hired = await echo.staff_company(session, company.id, actor=OPERATOR)
        await session.commit()
    return EchoCompany(company, project, {a.role: a for a in hired})


async def start_echo(committed, echo_company: EchoCompany, **params):
    from autora.company.workflows import start_workflow

    runtime = build_runtime()
    async with committed() as session:
        run, tasks = await start_workflow(
            session,
            policy=runtime.policy,
            workflows=runtime.workflows,
            company_id=echo_company.company.id,
            project_id=echo_company.project.id,
            template=echo.TEMPLATE.name,
            params={"topic": "EU AI Act", **params},
            actor=OPERATOR,
        )
        await session.commit()
    return run, tasks
