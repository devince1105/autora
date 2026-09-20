"""Reference reducer: snapshot + events -> snapshot (T-302, 3d-office/05 §3).

The browser store (T-305) applies events to a hydrated snapshot. This module is the executable
specification of that reduction, in Python, next to the projection it must agree with:

    view(from_snapshot(load_snapshot(t0)) + events(t0, t1], now) == load_snapshot(t1, now)

``tests/realtime/test_contract.py`` checks it on randomised runtime histories (real events from
the real task manager, approvals and activity service), and exports a fixture that the
TypeScript reducer must reproduce too.

Rules (each mirrors the code that writes the row the projection reads):

- **Duplicates**: an event with ``seq <= last_seq`` is ignored. Ephemeral events (no seq) never
  change the projection.
- **Agents**: AGENT_CREATED adds the agent. An activity event sets ``stored_state`` from its type
  (``autora.runtime.activity``), ``detail`` = payload + run/task/workflow context (cleared for
  IDLE and PAUSED; ``task_name`` is the task's display name), ``since`` changes only when the
  state does, ``last_event_seq`` = the event's seq. A non-final AGENT_RUN_FAILED or
  AGENT_RUN_ABORTED is trace data and changes nothing (the activity service rejects it too). A
  PAUSED agent stays PAUSED through task-manager transitions: no activity event is written then
  (``set_activity_unless_paused``), so there is nothing to apply.
- **Tasks**: TASK_CREATED adds the task (PENDING). TASK_READY / STARTED / WAITING / SUCCEEDED /
  FAILED(final) / CANCELLED / BLOCKED set the state; TASK_STARTED also sets attempt, run and
  agent; every TASK_* event sets ``since`` and ``last_event_seq``.
- **View** (at ``now``): effective activity state (COMPLETED past ``display_until`` reads IDLE),
  finished tasks older than the window left out, the last 100 events.

Not reproduced (not in the events): ``links`` in activity detail (no domain provides them yet),
agents created without AGENT_CREATED (only test fixtures do that).
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from autora.company import events as company_ev
from autora.db.models import ActivityState, TaskState
from autora.realtime.projection import (
    FINISHED_TASK_WINDOW,
    RECENT_EVENTS,
    ActivityView,
    AgentView,
    CycleView,
    RealtimeSnapshot,
    TaskView,
)
from autora.runtime.events import catalog as ev
from autora.runtime.events.schema import EventEnvelope

_ACTIVITY: dict[type, ActivityState] = {
    ev.AgentIdle: ActivityState.IDLE,
    ev.AgentResumed: ActivityState.IDLE,
    ev.AgentThinking: ActivityState.THINKING,
    ev.AgentWorking: ActivityState.WORKING,
    ev.AgentWaiting: ActivityState.WAITING,
    ev.AgentReviewing: ActivityState.REVIEWING,
    ev.AgentRunCompleted: ActivityState.COMPLETED,
    ev.AgentRunFailed: ActivityState.FAILED,
    ev.AgentRunAborted: ActivityState.FAILED,
    ev.AgentPaused: ActivityState.PAUSED,
}
_RUNLESS = (ActivityState.IDLE, ActivityState.PAUSED)
_TASK_STATE: dict[type, TaskState] = {
    ev.TaskReady: TaskState.READY,
    ev.TaskStarted: TaskState.RUNNING,
    ev.TaskWaiting: TaskState.WAITING_APPROVAL,
    ev.TaskSucceeded: TaskState.SUCCEEDED,
    ev.TaskCancelled: TaskState.CANCELLED,
    ev.TaskBlocked: TaskState.BLOCKED_BUDGET,
}
_FINISHED = {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}


@dataclass
class _Agent:
    id: uuid.UUID
    role: str
    display_name: str
    avatar_key: str
    department_id: uuid.UUID | None = None
    department_key: str | None = None
    activity: ActivityView | None = None


@dataclass
class RealtimeState:
    company_id: uuid.UUID
    last_seq: int = 0
    cycle: CycleView | None = None
    agents: dict[uuid.UUID, _Agent] = field(default_factory=dict)
    tasks: dict[uuid.UUID, TaskView] = field(default_factory=dict)
    recent: deque[EventEnvelope] = field(default_factory=lambda: deque(maxlen=RECENT_EVENTS))

    @classmethod
    def from_snapshot(cls, snapshot: RealtimeSnapshot) -> RealtimeState:
        state = cls(
            company_id=snapshot.company_id,
            last_seq=snapshot.last_seq,
            cycle=snapshot.cycle.model_copy() if snapshot.cycle else None,
        )
        for agent in snapshot.agents:
            state.agents[agent.id] = _Agent(
                agent.id, agent.role, agent.display_name, agent.avatar_key,
                agent.department_id, agent.department_key, agent.activity.model_copy(),
            )  # fmt: skip
        state.tasks = {task.id: task.model_copy() for task in snapshot.tasks}
        state.recent.extend(snapshot.recent_events)
        return state

    # --- events ----------------------------------------------------------------------------

    def apply(self, event: EventEnvelope) -> bool:
        """Apply one event. Returns False when it was ignored (duplicate, ephemeral, other
        company)."""
        if event.seq is None or event.seq <= self.last_seq or event.company_id != self.company_id:
            return False
        self.last_seq = event.seq
        self.recent.append(event)
        payload = event.payload

        if isinstance(payload, ev.AgentCreated) and event.agent_id is not None:
            self.agents[event.agent_id] = _Agent(
                event.agent_id,
                payload.role,
                payload.display_name,
                payload.avatar_key,
                payload.department_id,
                payload.department_key,
            )
        elif isinstance(payload, company_ev.AgentAssigned) and event.agent_id in self.agents:
            # the only thing that moves a drawn agent to another room without a reload
            agent = self.agents[event.agent_id]
            agent.role = payload.role
            agent.department_id = payload.department_id
            agent.department_key = payload.department_key
        elif isinstance(payload, company_ev.CycleStarted) and event.cycle_id is not None:
            self.cycle = CycleView(
                id=event.cycle_id,
                seq=payload.seq,
                stage=payload.stage,
                deadline=payload.deadline,
            )
        elif isinstance(payload, company_ev.CycleStageChanged) and self.cycle is not None:
            self.cycle = self.cycle.model_copy(
                update={"stage": payload.to_stage, "deadline": payload.deadline}
            )
        elif isinstance(payload, ev.AgentRetired) and event.agent_id is not None:
            self.agents.pop(event.agent_id, None)  # off the roster: the office stops drawing it
        elif type(payload) in _ACTIVITY and event.agent_id in self.agents:
            if not (
                isinstance(payload, ev.AgentRunFailed | ev.AgentRunAborted) and not payload.final
            ):
                self._activity(self.agents[event.agent_id], event)
        elif isinstance(payload, ev.TaskCreated) and event.task_id is not None:
            self.tasks[event.task_id] = TaskView(
                id=event.task_id,
                name=payload.name,
                display_name=payload.display_name,
                required_role=payload.required_role,
                state=TaskState.PENDING.value,
                depends_on=list(payload.depends_on),
                workflow_run_id=payload.workflow_run_id,
                attempt=0,
                run_id=None,
                agent_id=None,
                since=event.occurred_at,
                last_event_seq=event.seq,
            )
        elif event.event_type.startswith("TASK_") and event.task_id in self.tasks:
            self._task(self.tasks[event.task_id], event)
        return True

    def _activity(self, agent: _Agent, event: EventEnvelope) -> None:
        state = _ACTIVITY[type(event.payload)]
        runless = state in _RUNLESS
        detail: dict[str, Any] = event.payload.model_dump(mode="json")
        task = self.tasks.get(event.task_id) if event.task_id else None
        context = {
            "run_id": None if runless else event.run_id,
            "task_id": None if runless else event.task_id,
            "workflow_run_id": None if runless else event.workflow_run_id,
            "task_name": None if runless or task is None else task.display_name,
        }
        detail.update(
            {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in context.items() if v}
        )
        current = agent.activity
        agent.activity = ActivityView(
            state=state,
            stored_state=state,
            detail=detail,
            since=current.since
            if current is not None and current.stored_state == state
            else event.occurred_at,
            run_id=context["run_id"],
            task_id=context["task_id"],
            last_event_seq=event.seq,
        )

    @staticmethod
    def _task(task: TaskView, event: EventEnvelope) -> None:
        payload = event.payload
        task.since = event.occurred_at
        task.last_event_seq = event.seq
        if isinstance(payload, ev.TaskFailed):
            if payload.final:
                task.state = TaskState.FAILED.value
            return  # a retried failure is followed by TASK_READY in the same transaction
        target = _TASK_STATE.get(type(payload))
        if target is not None:
            task.state = target.value
        if isinstance(payload, ev.TaskStarted):
            task.attempt = payload.attempt
            task.run_id = payload.run_id
            task.agent_id = payload.agent_id

    # --- view ------------------------------------------------------------------------------

    def view(self, now: datetime) -> RealtimeSnapshot:
        """The projection at ``now``: what ``load_snapshot(..., now=now)`` returns."""
        agents = []
        for agent in self.agents.values():
            if agent.activity is None:
                continue
            activity = agent.activity.model_copy(update={"state": _effective(agent.activity, now)})
            agents.append(
                AgentView(
                    id=agent.id,
                    role=agent.role,
                    display_name=agent.display_name,
                    avatar_key=agent.avatar_key,
                    department_id=agent.department_id,
                    department_key=agent.department_key,
                    activity=activity,
                )  # fmt: skip
            )
        tasks = [
            task.model_copy()
            for task in self.tasks.values()
            if TaskState(task.state) not in _FINISHED or task.since > now - FINISHED_TASK_WINDOW
        ]
        return RealtimeSnapshot(
            company_id=self.company_id,
            last_seq=self.last_seq,
            server_time=now,
            agents=agents,
            tasks=tasks,
            recent_events=list(self.recent),
            cycle=self.cycle.model_copy() if self.cycle else None,
        )


def _effective(activity: ActivityView, now: datetime) -> ActivityState:
    if activity.stored_state is ActivityState.COMPLETED:
        until = activity.detail.get("display_until")
        if until is not None and datetime.fromisoformat(until) <= now:
            return ActivityState.IDLE
    return activity.stored_state


def canonical(snapshot: RealtimeSnapshot) -> dict[str, Any]:
    """The comparable content of a projection: keyed by id, JSON values, no server_time."""
    data = snapshot.model_dump(mode="json")
    return {
        "company_id": data["company_id"],
        "last_seq": data["last_seq"],
        "agents": {a["id"]: a for a in data["agents"]},
        "tasks": {t["id"]: t for t in data["tasks"]},
        "recent_event_seqs": [e["seq"] for e in data["recent_events"]],
        "kpis": data["kpis"],
        "cycle": data["cycle"],
    }
