"""Echo workflow template, agent behaviors, tool, and staffing."""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.agents import hire_agent
from autora.db.models import Agent, AgentStatus, Task
from autora.domains.echo.models import EchoNote
from autora.runtime.actor import Actor
from autora.runtime.behaviors import AgentBehavior, BehaviorRegistry, RunContext
from autora.runtime.dag import NodeSpec, TemplateRegistry, WorkflowTemplate
from autora.runtime.events.catalog import ProducedRef, Progress
from autora.runtime.tools import ToolContext, ToolRegistry, ToolResult

TEMPLATE_NAME = "echo.chain_v1"

ROLES = {"echo_research": "researcher", "echo_analyze": "analyst", "echo_write": "writer"}
"""Task name -> role. Real newsroom roles, so the office shows the familiar desks."""

TEMPLATE = WorkflowTemplate(
    name=TEMPLATE_NAME,
    nodes=(
        NodeSpec("echo_research", "Echo: gather ({topic})", ROLES["echo_research"], max_attempts=3),
        NodeSpec(
            "echo_analyze",
            "Echo: analyse ({topic})",
            ROLES["echo_analyze"],
            depends_on=("echo_research",),
        ),
        NodeSpec(
            "echo_write",
            "Echo: write up ({topic})",
            ROLES["echo_write"],
            depends_on=("echo_analyze",),
        ),
    ),
)

DISPLAY_NAMES = {"researcher": "Rae", "analyst": "Ana", "writer": "Wren"}

SYSTEM_PROMPT = """You are one desk in a three-desk relay: researcher, analyst, writer.
1. Call the echo_note tool exactly once with a one-sentence note about the topic, building on
   the upstream notes you were given.
2. Then reply with only a JSON object: {"note_id": "<id returned by echo_note>",
   "message": "<the note text>"}.
"""


class EchoNoteArgs(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class EchoReport(BaseModel):
    note_id: uuid.UUID
    message: str = Field(min_length=1)


# --- tool ----------------------------------------------------------------------------------


async def echo_note(args: EchoNoteArgs, ctx: ToolContext) -> ToolResult:
    """Write this task's note. Idempotent on the call's key: a re-run returns the same note."""
    role = "unknown"
    if ctx.agent_id is not None:
        role = await ctx.session.scalar(select(Agent.role).where(Agent.id == ctx.agent_id))
    note_id = await ctx.session.scalar(
        insert(EchoNote)
        .values(
            id=uuid.uuid4(),
            company_id=ctx.company_id,
            task_id=ctx.task_id,
            run_id=ctx.run_id,
            idempotency_key=ctx.idempotency_key,
            role=role,
            text=args.text,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(EchoNote.id)
    )
    reused = note_id is None
    if reused:
        note_id = await ctx.session.scalar(
            select(EchoNote.id).where(EchoNote.idempotency_key == ctx.idempotency_key)
        )
    return ToolResult(
        output={"note_id": str(note_id), "text": args.text, "reused": reused},
        summary=("reused note " if reused else "wrote note ") + str(note_id),
        produced=[ProducedRef(type="echo_note", id=note_id)],
        progress=Progress(label="notes", current=1, target=1),
    )


def register_tools(registry: ToolRegistry) -> None:
    registry.tool(
        "echo_note",
        description="Write your note for this task. Returns its note_id.",
        side_effect="write",
        retryable=True,
    )(echo_note)


# --- behaviors -----------------------------------------------------------------------------


async def upstream_notes(session: AsyncSession, ctx: RunContext) -> str | None:
    """OBSERVE: what the desks before this one reported (their task outputs)."""
    if not ctx.task.depends_on:
        return None
    rows = (
        await session.execute(
            select(Task.name, Task.output).where(Task.id.in_(ctx.task.depends_on))
        )
    ).all()
    lines = [f"- {name}: {json.dumps(output, ensure_ascii=False)}" for name, output in rows]
    return "Upstream notes:\n" + "\n".join(lines)


async def note_belongs_to_task(
    session: AsyncSession, ctx: RunContext, report: BaseModel
) -> list[str]:
    assert isinstance(report, EchoReport)
    note = await session.get(EchoNote, report.note_id)
    if note is None:
        return [f"note {report.note_id} does not exist; call echo_note and use the id it returns"]
    if note.task_id != ctx.task.id:
        return [f"note {report.note_id} belongs to another task"]
    return []


def register_behaviors(registry: BehaviorRegistry) -> None:
    for task_name, role in ROLES.items():
        registry.register(
            AgentBehavior(
                role=role,
                task_name=task_name,
                capability="reasoning",
                system_prompt=SYSTEM_PROMPT,
                output_model=EchoReport,
                tools=("echo_note",),
                validators=(note_belongs_to_task,),
                max_steps=4,
                repair_limit=1,
                max_output_tokens=4096,  # real models think before answering
                context=upstream_notes,
                summarize=lambda report: report.message[:200],
            )
        )


def register_templates(templates: TemplateRegistry) -> None:
    templates.register(TEMPLATE)


# --- staffing ------------------------------------------------------------------------------


async def staff_company(
    session: AsyncSession, company_id: uuid.UUID, *, actor: Actor
) -> list[Agent]:
    """Hire one active agent per echo role that the company does not have yet."""
    existing = set(
        (
            await session.scalars(
                select(Agent.role).where(
                    Agent.company_id == company_id, Agent.status == AgentStatus.ACTIVE
                )
            )
        ).all()
    )
    hired = []
    for role in ROLES.values():
        if role not in existing:
            hired.append(
                await hire_agent(
                    session,
                    company_id=company_id,
                    role=role,
                    display_name=DISPLAY_NAMES[role],
                    actor=actor,
                )
            )
    return hired
